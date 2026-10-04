"""Ask once per property/key and resume verified conversations after manager learning."""
import re
import time
from sqlalchemy import select
from app.db.models import ManagerWorkspace,Property,Escalation,Event,SentMessage
from app.core.security import fingerprint
from app.services.knowledge import KnowledgeService
from app.services.onboarding import QUESTIONS
from app.services.escalation import EscalationService
from app.services.policies import normalized

class LearningService:
    def __init__(self,db,vault):self.db,self.vault=db,vault

    def missing(self,context,messages,suggested=()):
        if not context.property_id:return []
        candidates=set(suggested)&set(QUESTIONS)
        # A deterministic guard for a frequent operational question, even if the model
        # wrongly claims an answer. Broader missing information uses the structured plan.
        if re.search(r'poubell|garbage|rubbish|trash|basura|mull',normalized(' '.join(messages))):candidates.add('trash_location')
        with self.db.session() as session:
            prop=session.get(Property,context.property_id)
            from app.services.onboarding import OnboardingService
            unknown=set(OnboardingService(self.vault).progress(session,prop)['missing'])
        return sorted(candidates&unknown)

    def ask(self,context,fields):
        for field in fields:
            key='learn:'+fingerprint(context.property_id+':'+field)[:32]
            with self.db.session() as session:
                row=session.get(ManagerWorkspace,key)
                data=self.vault.decrypt(row.encrypted_data) if row else {'property_id':context.property_id,'field':field,'waiting':[]}
                pair={'conversation_id':context.conversation_id,'reservation_id':context.reservation_id}
                if pair not in data['waiting']:data['waiting'].append(pair)
                data['status']='waiting'
                if row:row.encrypted_data=self.vault.encrypt(data)
                else:session.add(ManagerWorkspace(id=key,encrypted_data=self.vault.encrypt(data)))
                session.commit()
            eid=EscalationService(self.db,self.vault).create(key,
                'Pour '+context.property_name+' : '+QUESTIONS[field]+' Je le retiendrai pour les prochains voyageurs.',context)
            with self.db.session() as session:
                e=session.get(Escalation,eid)
                if e.status=='resolved':e.status='open';e.resolved_at=None
                session.commit()

    async def resume(self,app):
        # Called after an explicit manager update. Preparing a response remains tied to
        # the original conversation and keeps observation-only events in TEST.
        prepared=[]
        async with self.db.lock('learning_resume'):
            with self.db.session() as session:
                rows=[(r.id,self.vault.decrypt(r.encrypted_data)) for r in session.scalars(select(ManagerWorkspace).where(ManagerWorkspace.id.like('learn:%')))]
            for key,data in rows:
                if data.get('status')!='waiting':continue
                with self.db.session() as session:
                    prop=session.get(Property,data['property_id'])
                    if not prop:continue
                    from app.services.onboarding import OnboardingService
                    if data['field'] in OnboardingService(self.vault).progress(session,prop)['missing']:continue
                waiting=[]
                for pair in data['waiting']:
                    try:
                        with self.db.session() as session:
                            event=session.scalar(select(Event).where(Event.conversation_id==pair['conversation_id']).order_by(Event.received_at.desc()))
                            if not event:continue
                            if event.received_at<time.time()-app.settings.max_event_age_hours*3600:continue
                            payload=self.vault.decrypt(event.encrypted_payload)
                        context,posts=await app.builder.build(payload)
                        if context.resolution_reason or context.property_id!=data['property_id']:waiting.append(pair);continue
                        body=payload['message']['body']
                        mid=payload['message'].get('_id')
                        from app.services.context import clean
                        if not any(p.get('_id')==mid and p.get('from',{}).get('type')=='guest' and clean(p.get('body'))==clean(body) for p in posts):continue
                        # If someone already answered, retain knowledge without reviving the exchange.
                        if posts and posts[-1].get('from',{}).get('type')!='guest':continue
                        messages,withheld=app.processor.safe_inputs(context,[body])
                        plan=await app.processor.agent.plan(context,messages)
                        if self.missing(context,messages,plan.missing_fields):waiting.append(pair);continue
                        reply=await app.processor.agent.reply(context,messages,plan,app.training.style_prompt())
                        from app.services.policies import guard_reply
                        reply=guard_reply(context,messages,plan,reply,payload.get('conversation',{}).get('language','fr'))
                        if reply.escalation_required or plan.manager_required or any(secret and secret in reply.text for secret in withheld) or not reply.text.strip() or len(reply.text)>8000:
                            waiting.append(pair);continue
                        # Use the existing audited decision/outbox path (confirmation remains visible).
                        with self.db.session() as session:
                            e=session.scalar(select(Escalation).where(Escalation.dedup_key==key))
                            eid=e.id if e else None
                        if not eid:continue
                        from app.schemas.actions import ManagerAction
                        action=ManagerAction(intent='decide_escalation',property=None,updates=[],state_key=None,state_status=None,valid_until=None,note=None,mapping_kind=None,external_id=None,escalation_id=eid,confidence=1,message='',decision='reply',response_text=reply.text)
                        if pair['conversation_id']!=context.conversation_id:continue
                        # One question may concern multiple guests: each gets a destination-specific escalation.
                        target=EscalationService(self.db,self.vault).create('learn-reply:'+fingerprint(key+context.conversation_id+mid), 'Réponse après apprentissage Maison.',context)
                        with self.db.session() as session:
                            if session.get(Escalation,target).status!='open':continue
                        action.escalation_id=target
                        result=await app.decisions.propose(action)
                        outcome=await app.decisions.confirm(result['decision_id'],True)
                        if outcome['status'] in {'sent','simulated'}:
                            from app.db.models import EscalationDecision,AIObservation
                            with self.db.session() as session:
                                decision=session.get(EscalationDecision,result['decision_id'])
                                sent=session.get(SentMessage,decision.message_id)
                                if sent and not session.scalar(select(AIObservation).where(AIObservation.sent_message_id==sent.id)):
                                    app.training.record(session,sent,context,[body],reply,sent.test_mode);session.commit()
                        prepared.append(outcome)
                    except Exception:waiting.append(pair)
                with self.db.session() as session:
                    row=session.get(ManagerWorkspace,key)
                    data['waiting']=waiting;data['status']='waiting' if waiting else 'learned';row.encrypted_data=self.vault.encrypt(data)
                    if not waiting:
                        e=session.scalar(select(Escalation).where(Escalation.dedup_key==key))
                        if e:e.status='resolved';e.resolved_at=time.time()
                    session.commit()
        return prepared
