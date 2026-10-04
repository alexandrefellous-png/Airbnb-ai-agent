import time
import re
from sqlalchemy import select
from app.core.security import fingerprint
from app.db.models import AuditLog, Escalation, EscalationDecision, Event, ReservationSnapshot, SentMessage
from app.guesty.errors import ContractError,LiveSendsBlocked
from app.services.properties import PropertyService

PRIORITIES = {"critical":"CRITIQUE","urgent":"URGENT","high":"URGENT","normal":"NORMAL","low":"FAIBLE"}

class DecisionService:
    def __init__(self,db,vault,settings,guesty):
        self.db,self.vault,self.settings,self.guesty=db,vault,settings,guesty
        self.properties=PropertyService(db,vault)

    def simulation(self,session,escalation):
        event=session.scalar(select(Event).where(Event.conversation_id==escalation.guesty_conversation_id).order_by(Event.received_at.desc()))
        payload=self.vault.decrypt(event.encrypted_payload) if event else {}
        return self.settings.test_mode or payload.get('__observation_only') is True

    def alerts(self,query=""):
        with self.db.session() as session:
            secrets=self.properties.secrets(session)
            properties={p["id"]:p for p in self.properties.list()}
            alerts=[]
            for e in session.scalars(select(Escalation).where(Escalation.status=="open").order_by(Escalation.created_at.desc())):
                summary=self.vault.redact(self.vault.decrypt(e.encrypted_summary),secrets)
                snapshot=session.get(ReservationSnapshot,e.reservation_id) if e.reservation_id else None
                event=session.scalar(select(Event).where(Event.conversation_id==e.guesty_conversation_id).order_by(Event.received_at.desc())) if e.guesty_conversation_id else None
                request=self.vault.redact(self.vault.decrypt(event.encrypted_payload).get("message",{}).get("body",""),secrets) if event else summary
                reply=session.scalar(select(SentMessage).where(SentMessage.conversation_id==e.guesty_conversation_id).order_by(SentMessage.created_at.desc())) if e.guesty_conversation_id else None
                pending=session.scalar(select(EscalationDecision).where(EscalationDecision.escalation_id==e.id,EscalationDecision.status=="pending",EscalationDecision.expires_at>time.time()))
                alerts.append({"id":e.id,"guest_name":e.guest_name or "Voyageur", "property_id":e.property_id,
                    'learning':e.dedup_key.startswith('learn:'),
                    "property_name":properties.get(e.property_id,{}).get("name","Logement à vérifier"),
                    "reservation_id":e.reservation_id,"conversation_id":e.guesty_conversation_id,
                    "check_in":snapshot.check_in if snapshot else None,"check_out":snapshot.check_out if snapshot else None,
                    "summary":summary,"request":request,"temporary_reply":self.vault.redact(self.vault.decrypt(reply.encrypted_body),secrets) if reply else "",
                    "priority":e.priority,"priority_label":PRIORITIES.get(e.priority,"NORMAL"),
                    "notification":e.notification_status,"pending_decision":pending.id if pending else None})
            if query:
                normalized=query.casefold()
                if "late" in normalized:alerts=[e for e in alerts if re.search(r'late|tard|depart|partir',e["request"]+e["summary"],re.I)]
            alerts.sort(key=lambda e:{"critical":0,"urgent":1,"high":1,"normal":2,"low":3}.get(e["priority"],2))
            return alerts

    async def propose(self,action):
        if action.decision not in {"accept","refuse","reply"}:raise ValueError("Décision invalide.")
        async with self.db.lock("escalation:"+str(action.escalation_id)):
            with self.db.session() as session:
                e=session.get(Escalation,action.escalation_id)
                if not e or e.status!="open":raise ValueError("Cette alerte n'est plus ouverte.")
                if not e.guesty_conversation_id:raise ValueError("Aucune conversation vérifiée : clôturez l'alerte manuellement sans réponse.")
                text=(action.response_text or "").strip()
                if not text:
                    if action.decision=="reply":raise ValueError("Écrivez votre réponse.")
                    text="Votre demande a été acceptée par l’hôte." if action.decision=="accept" else "Après vérification avec l’hôte, nous ne pouvons pas accepter cette demande. Merci de votre compréhension."
                if len(text)>8000:raise ValueError("Réponse trop longue.")
                pending=session.scalar(select(EscalationDecision).where(EscalationDecision.escalation_id==e.id,EscalationDecision.status=="pending"))
                if pending:pending.status="superseded"
                row=EscalationDecision(escalation_id=e.id,decision=action.decision,encrypted_response=self.vault.encrypt(text),
                    test_mode=self.simulation(session,e),expires_at=time.time()+900)
                session.add(row);session.commit()
                return {"status":"pending_decision","decision_id":row.id,"decision":row.decision,"response":text,
                    "financial":bool(re.search(r'rembours|refund|compens|reduc|discount',self.vault.decrypt(e.encrypted_summary),re.I)),
                    "message":"Confirmez cette décision et la réponse exacte au voyageur."}

    async def confirm(self,decision_id,accept):
        with self.db.session() as session:
            row=session.get(EscalationDecision,decision_id)
            if not row:raise ValueError("Décision introuvable.")
            escalation_id=row.escalation_id
        async with self.db.lock("escalation:"+escalation_id):
            with self.db.session() as session:
                row=session.get(EscalationDecision,decision_id)
                e=session.get(Escalation,escalation_id)
                if row.status!="pending" or row.expires_at<time.time() or e.status!="open":raise ValueError("Décision expirée ou déjà traitée.")
                if not accept:
                    row.status="rejected";session.commit()
                    return {"status":"rejected","message":"Décision annulée; l'alerte reste ouverte."}
                if row.test_mode!=self.simulation(session,e):raise ValueError("Le mode d'envoi a changé. Proposez à nouveau la décision.")
                conversation_id=e.guesty_conversation_id
            # Same conversation lock used by the guest worker: no simultaneous guest/manager send.
            async with self.db.lock("conversation:"+conversation_id):
                with self.db.session() as session:
                    row=session.get(EscalationDecision,decision_id);e=session.get(Escalation,escalation_id)
                    simulation=self.simulation(session,e)
                    if session.scalar(select(SentMessage).where(SentMessage.conversation_id==conversation_id,SentMessage.status.in_(["sending","uncertain"]))):
                        raise ValueError("Un envoi incertain bloque cette conversation; vérifiez Guesty.")
                    text=self.vault.decrypt(row.encrypted_response)
                    message=SentMessage(batch_key=fingerprint("manager-decision:"+row.id),conversation_id=conversation_id,
                        body_hash=fingerprint(text),encrypted_body=self.vault.encrypt(text),test_mode=simulation,
                        status="simulated" if simulation else "draft")
                    session.add(message);session.flush()
                    row.message_id=message.id;row.status="simulated" if simulation else "approved"
                    session.add(AuditLog(property_id=e.property_id,source="manager:decision",encrypted_change=self.vault.encrypt(
                        {"escalation_id":e.id,"decision":row.decision,"response":text,"test_mode":simulation})))
                    if simulation:
                        e.status,e.resolved_at="resolved",time.time();session.commit()
                        return {"status":"simulated","message":"Décision enregistrée en mode test. Aucun message envoyé.","response":text}
                    event=session.scalar(select(Event).where(Event.conversation_id==conversation_id).order_by(Event.received_at.desc()))
                    payload=self.vault.decrypt(event.encrypted_payload) if event else {}
                    module=payload.get("message",{}).get("module")
                    module={"type":module} if isinstance(module,str) else module
                    if not module or module.get("type") not in {"airbnb2","email","sms","whatsapp"}:raise ValueError("Canal de réponse non vérifié.")
                    # Verify destination again immediately before a real send.
                    conv=await self.guesty.conversation(conversation_id)
                    if conv.get("_id")!=conversation_id or conv.get("conversationWith","Guest")!="Guest":raise ValueError("Conversation non vérifiée.")
                    if e.reservation_id:
                        reservation=await self.guesty.reservation(e.reservation_id)
                        if reservation.get("conversationId")!=conversation_id:raise ValueError("Réservation et conversation différentes.")
                    message.status="sending";session.commit();message_id=message.id
                try:
                    post_id=await self.guesty.send(conversation_id,text,{k:v for k,v in module.items() if k in {"type","to","cc","bcc"}})
                except LiveSendsBlocked:
                    with self.db.session() as session:
                        msg=session.get(SentMessage,message_id);msg.status,msg.test_mode="simulated",True
                        decision=session.get(EscalationDecision,decision_id);decision.status="simulated"
                        escalation=session.get(Escalation,escalation_id);escalation.status,escalation.resolved_at="resolved",time.time();session.commit()
                    return {"status":"simulated","message":"Envoi bloqué par le mode test ou la sécurité serveur. AI WOULD REPLY.","response":text}
                except Exception:
                    with self.db.session() as session:
                        session.get(SentMessage,message_id).status="uncertain"
                        session.get(EscalationDecision,decision_id).status="uncertain"
                        session.commit()
                    return {"status":"uncertain","message":"Résultat de l'envoi incertain. Aucun renvoi automatique; vérifiez la discussion Guesty."}
                with self.db.session() as session:
                    message=session.get(SentMessage,message_id);message.status,message.guesty_message_id="sent",post_id
                    e=session.get(Escalation,escalation_id);e.status,e.resolved_at="resolved",time.time()
                    session.get(EscalationDecision,decision_id).status="sent"
                    session.commit()
                return {"status":"sent","message":"Décision enregistrée et réponse envoyée."}
