"""Human Guesty imports require explicit certification; AI approvals never enter this path."""
import time
from sqlalchemy import select
from app.core.security import fingerprint
from app.db.models import SentMessage,StyleProfile,HumanStyleExample
from app.services.training import TrainingService

class StyleService:
    def __init__(self,db,vault,ai):
        self.db,self.vault,self.ai=db,vault,ai
        self.training=TrainingService(db,vault,ai)

    async def learn(self,conversation_id,posts,approved_ids=None):
        approved=set(approved_ids or [])
        with self.db.session() as session:
            sent=session.scalars(select(SentMessage)).all()
            own_ids={m.guesty_message_id for m in sent}
            own_hashes={m.body_hash for m in sent}
            profile=session.get(StyleProfile,'host')
            learned=set(profile.source_message_ids if profile else [])
            examples=[p for p in posts if p.get('_id') in approved and p['_id'] not in own_ids|learned
                and p.get('from',{}).get('type')=='user' and p.get('isAutomatic') is not True
                and p.get('module',{}).get('type') not in {'note','log'}
                and fingerprint(p.get('body','')) not in own_hashes]
        if not examples:return {'learned':0,'message':'Aucune réponse humaine certifiée à apprendre.'}
        actor=self.training.actor()
        async with self.db.lock('style_import'):
            with self.db.session() as session:
                for post in examples[-30:]:
                    source_id=fingerprint(conversation_id+':'+post['_id'])
                    if not session.scalar(select(HumanStyleExample).where(HumanStyleExample.source=='guesty_human',HumanStyleExample.source_id==source_id)):
                        session.add(HumanStyleExample(source='guesty_human',source_id=source_id,human_user_id=actor,
                            encrypted_body=self.vault.encrypt(post.get('body','')),body_hash=fingerprint(post.get('body',''))))
                row=session.get(StyleProfile,'host')
                if not row:row=StyleProfile(id='host',profile='');session.add(row)
                row.source_message_ids=list(set(row.source_message_ids or [])|{p['_id'] for p in examples[-30:]});row.updated_at=time.time();session.commit()
            result=await self.training.learn()
            with self.db.session() as session:
                row=session.get(StyleProfile,'host');row.profile=self.training.style_prompt();session.commit()
        return {'learned':len(examples[-30:]),'summary':result.get('summary',[])}
