import time
from collections import defaultdict
from sqlalchemy import select
from app.core.security import nested_strings
from app.services.onboarding import OnboardingService
from app.db.models import GuestyMapping,MediaAsset,ManagerWorkspace,Property,PropertyAccess,PropertyState,PropertyWifi,PropertyKnowledge,PropertySync
from app.services.knowledge import KnowledgeService

SECRET_FIELDS={'building_code','lockbox_code','wifi_password'}

class PropertyService:
    def __init__(self,db,vault):
        self.db,self.vault=db,vault

    def find(self,session,name):
        if not name:raise ValueError('Précisez le nom du logement.')
        rows=[p for p in session.scalars(select(Property)) if p.name.casefold()==name.casefold() or p.name.lstrip('!').casefold()==name.casefold()]
        if len(rows)!=1:raise ValueError('Logement inconnu ou ambigu : utilisez son nom exact.')
        return rows[0]

    def secrets(self,session):
        values=[value for model in [PropertyAccess,PropertyWifi]
                for row in session.scalars(select(model))
                for value in nested_strings(self.vault.decrypt(row.encrypted_data))]
        from app.services.manager import ACCESS_FIELDS,WIFI_FIELDS
        values += [v for row in session.scalars(select(PropertyKnowledge).where(PropertyKnowledge.key.in_(ACCESS_FIELDS|WIFI_FIELDS))) for v in nested_strings(self.vault.decrypt(row.encrypted_value))]
        return values

    def view(self,session,p,related=None):
        access=related['access'] if related is not None else session.get(PropertyAccess,p.id)
        wifi=related['wifi'] if related is not None else session.get(PropertyWifi,p.id)
        a=self.vault.decrypt(access.encrypted_data) if access else {}
        w=self.vault.decrypt(wifi.encrypted_data) if wifi else {}
        now=time.time()
        state_rows=related['states'] if related is not None else session.scalars(select(PropertyState).where(PropertyState.property_id==p.id))
        states=[{'key':s.key,'status':s.status,'note':s.note,'valid_until':s.valid_until}
                for s in state_rows if s.valid_from<=now and (s.valid_until is None or s.valid_until>now)]
        media_rows=related['media'] if related is not None else list(session.scalars(select(MediaAsset).where(MediaAsset.property_id==p.id,MediaAsset.status=='assigned')))
        media=[]
        for item in media_rows:
            key='dm:'+item.id.replace('-','')
            link=related['drive_links'].get(key) if related is not None else session.get(ManagerWorkspace,key)
            drive=self.vault.decrypt(link.encrypted_data) if link else {}
            media.append({'id':item.id,'name':self.vault.decrypt(item.encrypted_name),'kind':item.kind,
                'size':item.size,'content_type':item.content_type,'url':'/admin/media/'+item.id,
                'drive':{'url':drive.get('url'),'shared':bool(drive.get('shared'))} if drive else {}})
        mappings=related['mappings'] if related is not None else session.scalars(select(GuestyMapping).where(GuestyMapping.property_id==p.id))
        memory=KnowledgeService(self.vault)
        knowledge=memory.effective(related['knowledge'] if related is not None else memory.rows(session,p.id))
        from app.services.manager import ACCESS_FIELDS,WIFI_FIELDS
        facts=dict(p.facts)
        for key,item in knowledge.items():
            if key in ACCESS_FIELDS:a[key]=item['value']
            elif key in WIFI_FIELDS:w[key]=item['value']
            elif not key.startswith('state:') and key not in {'name','address','check_in','check_out','timezone','guesty_listing_id'}:facts[key]=item['value']
        sync=related['sync'] if related is not None else session.get(PropertySync,p.id)
        return {'id':p.id,'name':p.name,'guesty_name':p.guesty_name,'listing_id':p.guesty_listing_id,
            'sync':{'status':sync.status,'synced_at':sync.synced_at} if sync else None,
            'knowledge':{key:{**item,'value':'••••••••' if key in SECRET_FIELDS else item['value']} for key,item in knowledge.items()},
            'address':p.address,'timezone':p.timezone,'check_in':p.check_in,'check_out':p.check_out,
            'facts':facts,'states':states,'onboarding_step':p.onboarding_step,'is_active':p.is_active and (sync is None or sync.status=='synced'),
            'archived':bool(p.archived_at),'version':p.version,'status':p.status,'activated_at':p.activated_at,
            'deactivated_at':p.deactivated_at,'is_billable':p.is_billable,
            'configuration':OnboardingService(self.vault).progress(session,p,related),
            'access':{k:'••••••••' if k in SECRET_FIELDS else v for k,v in a.items()},
            'wifi':{k:'••••••••' if k in SECRET_FIELDS else v for k,v in w.items()},
            'configured_secrets':[k for data in [a,w] for k,v in data.items() if k in SECRET_FIELDS and v],
            'access_configured':bool(a),'wifi_configured':bool(w.get('wifi_network')),
            'video_configured':bool(a.get('access_video') or any(m['kind']=='access_video' for m in media)),
            'media':media,'mappings':[{'kind':m.kind,'id':m.external_id} for m in mappings]}

    def list(self,issues_only=False):
        # Bounded scoped queries regardless of property count.
        with self.db.session() as session:
            properties=list(session.scalars(select(Property).order_by(Property.name)))
            access={row.property_id:row for row in session.scalars(select(PropertyAccess))}
            wifi={row.property_id:row for row in session.scalars(select(PropertyWifi))}
            groups={name:defaultdict(list) for name in ['states','media','mappings','knowledge']}
            for name,model in [('states',PropertyState),('media',MediaAsset),('mappings',GuestyMapping),('knowledge',PropertyKnowledge)]:
                query=select(model)
                if name=='media':query=query.where(MediaAsset.status=='assigned')
                for row in session.scalars(query):groups[name][row.property_id].append(row)
            drive_links={row.id:row for row in session.scalars(select(ManagerWorkspace).where(ManagerWorkspace.id.like('dm:%')))}
            syncs={row.property_id:row for row in session.scalars(select(PropertySync))}
            rows=[self.view(session,p,{'access':access.get(p.id),'wifi':wifi.get(p.id),
                'states':groups['states'][p.id],'media':groups['media'][p.id],
                'mappings':groups['mappings'][p.id],'drive_links':drive_links,'knowledge':groups['knowledge'][p.id],'sync':syncs.get(p.id)}) for p in properties]
            if issues_only:rows=[p for p in rows if any(s['status'] in {'out_of_order','unavailable'} for s in p['states'])]
            return rows

    def detail(self,property_id):
        with self.db.session() as session:
            prop=session.get(Property,property_id)
            if not prop:raise ValueError('Logement introuvable.')
            return self.view(session,prop)

    def reveal(self,property_id,field):
        if field not in SECRET_FIELDS:raise ValueError('Champ non autorisé.')
        with self.db.session() as session:
            prop=session.get(Property,property_id)
            if not prop:raise ValueError('Logement introuvable.')
            model=PropertyWifi if field=='wifi_password' else PropertyAccess
            row=session.get(model,property_id)
            return self.vault.decrypt(row.encrypted_data).get(field,'') if row else ''
