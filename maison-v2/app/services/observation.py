"""Read Guesty inbox locally and prepare TEST replies without any external send."""
import time
from datetime import datetime,timezone
from sqlalchemy import select
from app.core.security import fingerprint
from app.db.models import ManagerWorkspace,Organization,Property,Event
from app.guesty.client import verified_sender

class ObservationService:
    def __init__(self,db,vault,settings,guesty,queue,processor):
        self.db,self.vault,self.settings,self.guesty,self.queue,self.processor=db,vault,settings,guesty,queue,processor

    def read(self,key):
        with self.db.session() as session:
            row=session.get(ManagerWorkspace,key)
            return self.vault.decrypt(row.encrypted_data) if row else {}

    def write(self,key,value):
        with self.db.session() as session:
            row=session.get(ManagerWorkspace,key)
            if row:row.encrypted_data,row.updated_at=self.vault.encrypt(value),time.time()
            else:session.add(ManagerWorkspace(id=key,encrypted_data=self.vault.encrypt(value)))
            session.commit()

    def status(self):
        status=self.read('observation:status')
        return {**status,'enabled':self.settings.observation_enabled,'interval':self.settings.observation_interval_seconds,
                'guesty_connected':bool(self.settings.guesty_client_id),'openai_connected':bool(self.settings.openai_api_key)}

    def configure(self,enabled):
        from app.core.tenancy import organization_id
        from app.db.models import AuditLog
        with self.db.system_session() as session:
            org=session.get(Organization,organization_id())
            org.settings={**org.settings,'observation_enabled':enabled};session.commit()
        with self.db.session() as session:
            session.add(AuditLog(source='settings:observation',encrypted_change=self.vault.encrypt({'enabled':enabled,'always_test':True})))
            session.commit()

    async def sync(self):
        if not self.settings.guesty_client_id:raise ValueError('Connectez Guesty pour afficher les conversations.')
        async with self.db.lock('observation_sync'):
            previous=self.read('observation:status')
            # Prevent browser refreshes and duplicate app workers from exhausting API quotas.
            if previous.get('next_sync_at',0)>time.time():return self.status()
            now=time.time()
            state={**previous,'state':'syncing','started_at':now,'next_sync_at':now+self.settings.observation_interval_seconds,'error':None}
            self.write('observation:status',state)
            imported,generated,skipped=0,0,0
            try:
                # Recent modified conversations first; additional pages rotate so a large
                # portfolio is scanned without an unbounded burst of API requests.
                first=await self.guesty.conversations(25)
                rows=list(first['conversations']);cursor=previous.get('scan_cursor')
                if cursor:
                    page=await self.guesty.conversations(25,cursor)
                    rows.extend(page['conversations']);next_cursor=page['cursor']
                else:next_cursor=first['cursor']
                seen=set()
                with self.db.session() as session:
                    property_names={p.guesty_listing_id:p.name for p in session.scalars(select(Property)) if p.guesty_listing_id}
                for summary in rows:
                    cid=summary['_id']
                    if cid in seen or summary.get('conversationWith','Guest')!='Guest':continue
                    seen.add(cid)
                    posts=await self.guesty.posts(cid,25)
                    posts=[p for p in posts if p.get('module',{}).get('type') not in {'note','log'}]
                    metas=summary.get('meta',{}).get('reservations',[])
                    reservation_id=metas[0].get('_id') if len(metas)==1 else None
                    listing_id=metas[0].get('listing',{}).get('_id') if len(metas)==1 else None
                    guest_name=summary.get('meta',{}).get('guest',{}).get('fullName','Voyageur')
                    item={'conversation_id':cid,'guest_name':guest_name,'property_name':property_names.get(listing_id,'Logement à associer'),
                        'reservation_id':reservation_id,'listing_id':listing_id,'synced_at':time.time(),
                        'posts':[{'id':p.get('_id'),'body':p.get('body',''),'sender':verified_sender(p),'created_at':p.get('createdAt')} for p in posts]}
                    self.write('oc:'+fingerprint(cid)[:32],item);imported+=1
                    guests=[p for p in posts if verified_sender(p)=='guest' and p.get('body','').strip()]
                    if not guests:skipped+=1;continue
                    newest=guests[-1]
                    try:stamp=datetime.fromisoformat(newest['createdAt'].replace('Z','+00:00'))
                    except (KeyError,ValueError,TypeError):skipped+=1;continue
                    if not stamp.tzinfo or time.time()-stamp.timestamp()>self.settings.max_event_age_hours*3600 or stamp.timestamp()>time.time()+300:
                        skipped+=1;continue
                    if generated>=5 or not self.settings.openai_api_key:continue
                    mid=newest.get('_id')
                    if not mid:skipped+=1;continue
                    with self.db.session() as session:
                        existing=session.scalar(select(Event).where(Event.message_key==fingerprint(mid)))
                        if existing:continue
                    payload={'event':'reservation.messageReceived','reservationId':reservation_id,
                        'conversation':{'_id':cid,'conversationWith':'Guest','language':summary.get('language','fr')},
                        'message':{'_id':mid,'type':'fromGuest','body':newest['body'],'createdAt':newest['createdAt'],'module':newest.get('module')},
                        '__observation_only':True}
                    queued=self.queue.enqueue(payload)
                    if queued['status']=='pending':
                        async with self.db.lock('conversation:'+cid):
                            await self.processor.process([queued['event_id']])
                        generated+=1
                state.update(state='ready',last_sync_at=time.time(),imported=imported,generated=generated,
                    skipped=skipped,scan_cursor=next_cursor,error=None)
                self.write('observation:status',state)
                return self.status()
            except Exception as exc:
                from app.guesty.errors import GuestyError
                message=f'Guesty temporairement indisponible (HTTP {exc.status}).' if isinstance(exc,GuestyError) else 'Certaines conversations n’ont pas pu être vérifiées. Réessayez dans une minute.'
                state.update(state='error',error=message,last_attempt_at=time.time())
                self.write('observation:status',state)
                return self.status()

    def inbox(self):
        with self.db.session() as session:
            values=[self.vault.decrypt(row.encrypted_data) for row in session.scalars(select(ManagerWorkspace).where(ManagerWorkspace.id.like('oc:%')))]
            rows=sorted(values,key=lambda item:max((post.get('created_at') or '' for post in item.get('posts',[])),default=''),reverse=True)
            return rows[:100]

    async def tick(self):
        if self.settings.observation_enabled:await self.sync()
