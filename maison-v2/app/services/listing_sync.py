"""Complete paginated Guesty inventory -> stable local IDs and enriched memory."""
import time
from datetime import time as clocktime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.core.security import fingerprint
from app.db.models import Property,PropertySync,PropertyKnowledge,PropertyAccess,PropertyWifi,GuestyMapping,ManagerWorkspace,AuditLog
from app.services.knowledge import KnowledgeService
from app.services.listings import ListingService

AMENITIES={'Elevator':'elevator','Air conditioning':'air_conditioning','Washer':'washing_machine','Dryer':'dryer',
           'Dishwasher':'dishwasher','TV':'tv','Oven':'oven','Microwave':'microwave','Heating':'heating','Kitchen':'kitchen'}

def listing_values(data):
    values={}
    for source,target in [('wifiName','wifi_network'),('wifiPassword','wifi_password'),('houseManual','house_manual'),('parkingInstructions','parking_instructions'),('trashCollectedOn','trash_collection')]:
        if isinstance(data.get(source),str) and data[source].strip():values[target]=data[source]
    for source,target in [('accommodates','capacity'),('bedrooms','bedrooms'),('beds','beds'),('bathrooms','bathrooms')]:
        value=data.get(source)
        if isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0:values[target]=value
    for source,target in [('defaultCheckInTime','check_in'),('defaultCheckOutTime','check_out')]:
        value=data.get(source)
        if isinstance(value,str):
            try:
                parsed=clocktime.fromisoformat(value)
                if len(value)==5 and parsed.tzinfo is None:values[target]=value
            except ValueError:pass
    if data.get('timezone'):
        try:ZoneInfo(data['timezone']);values['timezone']=data['timezone']
        except (ValueError,TypeError,KeyError):pass
    address=data.get('address',{}).get('full') if isinstance(data.get('address'),dict) else None
    if isinstance(address,str) and address.strip():values['address']=address
    amenities=data.get('amenities')
    if isinstance(amenities,list) and all(isinstance(a,str) for a in amenities):
        values['amenities']=amenities
        values['equipment']=' · '.join(amenities)
        # Absence from a list is not proof that an amenity does not exist.
        for label,field in AMENITIES.items():
            if label.casefold() in {a.casefold() for a in amenities}:values[field]=True
    absent=data.get('amenitiesNotIncluded')
    if isinstance(absent,list) and all(isinstance(a,str) for a in absent):
        for label,field in AMENITIES.items():
            if label.casefold() in {a.casefold() for a in absent} and field not in values:values[field]=False
    return values

class ListingSyncService:
    def __init__(self,db,vault,guesty,settings,ai=None):
        self.db,self.vault,self.guesty,self.settings=db,vault,guesty,settings
        self.memory=KnowledgeService(vault)
        from app.services.guesty_knowledge import GuestyKnowledgeReader
        self.reader=GuestyKnowledgeReader(db,vault,ai)

    def status(self):
        with self.db.session() as session:
            row=session.get(ManagerWorkspace,'sync:listings')
            return self.vault.decrypt(row.encrypted_data) if row else {'state':'waiting','count':0}

    def save_status(self,value):
        with self.db.session() as session:
            row=session.get(ManagerWorkspace,'sync:listings')
            if row:row.encrypted_data=self.vault.encrypt(value);row.updated_at=time.time()
            else:session.add(ManagerWorkspace(id='sync:listings',encrypted_data=self.vault.encrypt(value)))
            session.commit()

    async def reconcile_identity_alerts(self):
        from app.db.models import Escalation
        from app.services.context import ContextBuilder
        with self.db.session() as session:
            rows=[(e.id,e.reservation_id,e.guesty_conversation_id) for e in session.scalars(select(Escalation).where(Escalation.status=='open'))
                  if self.vault.decrypt(e.encrypted_summary) in {'unmapped_unit','unmapped_unit_type','unknown_listing','property_not_configured'}][:10]
        for eid,rid,cid in rows:
            if not rid or not cid:continue
            try:
                context,_=await ContextBuilder(self.db,self.guesty,self.vault,self.settings).build({'reservationId':rid,'conversation':{'_id':cid}})
                if context.resolution_reason:continue
                with self.db.session() as session:
                    row=session.get(Escalation,eid);row.status='resolved';row.resolved_at=time.time()
                    session.add(AuditLog(property_id=context.property_id,source='guesty:identity_resolved',encrypted_change=self.vault.encrypt({'escalation_id':eid,'fresh_relationship_verified':True})))
                    session.commit()
            except Exception:continue

    async def sync(self,force=False,enrich=False):
        if not self.settings.guesty_client_id:return {'state':'disconnected','count':0}
        async with self.db.lock('listing_sync'):
            previous=self.status();now=time.time()
            if not force and previous.get('next_sync_at',0)>now:return previous
            state={**previous,'state':'syncing','next_sync_at':now+300,'error':None}
            self.save_status(state)
            try:
                # Fetch every page successfully before changing any local inventory.
                listings=await self.guesty.listings()
                if not isinstance(listings,list):raise ValueError('Invalid inventory')
                ids=[p.get('_id') for p in listings if isinstance(p,dict)]
                if len(ids)!=len(listings) or any(not isinstance(i,str) or not i or len(i)>120 for i in ids) or len(set(ids))!=len(ids):
                    raise ValueError('Incomplete or duplicate inventory')
                extracted={}
                budget=[3 if enrich else 0]
                for data in listings:
                    try:extracted[data['_id']]=await self.reader.read(data,budget)
                    except Exception:extracted[data['_id']]={}
                async with self.db.lock('manager_changes'):
                    with self.db.session() as session:
                        local={p.guesty_listing_id:p for p in session.scalars(select(Property)) if p.guesty_listing_id}
                        occupied={p.name:p.guesty_listing_id for p in session.scalars(select(Property))}
                        created=updated=missing=0
                        for data in listings:
                            lid=data['_id'];prop=local.get(lid)
                            fresh=prop is None
                            raw_name=data.get('nickname') or data.get('title') or 'Logement '+lid
                            name=str(raw_name)[:120]
                            if name in occupied and occupied[name]!=lid:name=name[:100]+' · '+fingerprint(lid)[:12]
                            values={**{k:v['value'] for k,v in extracted[lid].items()},**listing_values(data)};values['name']=name
                            if fresh:
                                prop=Property(name=name,guesty_listing_id=lid,is_active=False,status='onboarding',is_billable=False)
                                session.add(prop);session.flush();local[lid]=prop;created+=1
                                session.add(GuestyMapping(property_id=prop.id,kind='listing',external_id=lid,source='guesty:inventory'))
                                ListingService().record(session,prop,None,'onboarding','guesty:sync')
                            self.memory.backfill(session,prop)
                            sync=session.get(PropertySync,prop.id)
                            old=self.vault.decrypt(sync.encrypted_snapshot) if sync else {}
                            changed=old!=data
                            if changed:updated+=not fresh
                            previous_fields=set((prop.onboarding_data or {}).get('guesty_fields',[]))
                            facts=dict(prop.facts)
                            # Removed Guesty values become unknown, never erase manager rows.
                            for key in previous_fields-set(values):
                                row=session.scalar(select(PropertyKnowledge).where(PropertyKnowledge.property_id==prop.id,PropertyKnowledge.key==key,PropertyKnowledge.source=='guesty'))
                                if row:row.active=False
                                if key in {'address','timezone','check_in','check_out'}:setattr(prop,key,'')
                                else:facts.pop(key,None)
                            for key,value in values.items():
                                reference=(lid+':'+extracted[lid][key]['document'])[:160] if key in extracted[lid] else lid
                                self.memory.put(session,prop,key,value,'guesty',reference,confidence=.99 if key in extracted[lid] else 1)
                                if key in {'name','address','timezone','check_in','check_out'}:setattr(prop,key,value)
                                else:
                                    from app.services.manager import ACCESS_FIELDS,WIFI_FIELDS
                                    if key not in ACCESS_FIELDS|WIFI_FIELDS:facts[key]=value
                            # Rebuild only provider-owned encrypted fields, retaining manager overrides.
                            session.flush()
                            effective=self.memory.effective(self.memory.rows(session,prop.id))
                            from app.services.manager import ACCESS_FIELDS,WIFI_FIELDS
                            for model,fields in ((PropertyWifi,WIFI_FIELDS),(PropertyAccess,ACCESS_FIELDS)):
                                row=session.get(model,prop.id)
                                content=self.vault.decrypt(row.encrypted_data) if row else {}
                                for field in fields:
                                    if field in effective:content[field]=effective[field]['value']
                                    elif field in previous_fields:content.pop(field,None)
                                if content:
                                    if row:row.encrypted_data=self.vault.encrypt(content)
                                    else:session.add(model(property_id=prop.id,encrypted_data=self.vault.encrypt(content)))
                                elif row:row.encrypted_data=self.vault.encrypt({})
                            occupied[prop.name]=lid
                            prop.guesty_name=str(raw_name)[:250];prop.facts=facts
                            prop.onboarding_data={**prop.onboarding_data,'guesty_fields':list(values),
                                'field_sources':{**prop.onboarding_data.get('field_sources',{}),**{key:'guesty' for key in values}}}
                            provider_inactive=data.get('active') is False or (isinstance(data.get('pms'),dict) and data['pms'].get('active') is False)
                            if not sync:sync=PropertySync(property_id=prop.id,external_id=lid);session.add(sync)
                            sync.status='inactive' if provider_inactive else 'synced';sync.synced_at=now;sync.missing_since=None
                            sync.encrypted_snapshot=self.vault.encrypt(data)
                            # New imported properties can answer with partial knowledge; access gates stay enforced.
                            if fresh and not provider_inactive:
                                ListingService().transition(session,prop,'active');prop.onboarding_step='ready'
                            if changed:prop.version+=1
                        for lid,prop in local.items():
                            if lid in ids:continue
                            sync=session.get(PropertySync,prop.id)
                            if not sync:sync=PropertySync(property_id=prop.id,external_id=lid,encrypted_snapshot=self.vault.encrypt({}));session.add(sync)
                            sync.status='missing';sync.missing_since=sync.missing_since or now;missing+=1
                        session.add(AuditLog(source='guesty:listing_sync',encrypted_change=self.vault.encrypt({'created':created,'updated':updated,'missing':missing,'count':len(ids)})))
                        session.commit()
                state.update(state='ready',last_sync_at=time.time(),count=len(ids),created=created,updated=updated,missing=missing,error=None)
                await self.reconcile_identity_alerts()
            except Exception:
                state.update(state='error',error='Synchronisation Guesty indisponible. Vos connaissances sont conservées.')
            self.save_status(state)
            return state
