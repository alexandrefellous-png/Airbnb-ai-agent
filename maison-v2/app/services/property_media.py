"""Explicit property uploads and durable Drive links; never treat 'yes' as a URL."""
import time
from app.db.models import Property, PropertyAccess, MediaAsset, AuditLog
from app.services.onboarding import OnboardingService
from app.services.google_drive import DriveError

FIELDS={'access_video':'access_video','access_photos':'access_photo','arrival_guide':'arrival_guide'}

class PropertyMediaService:
    def __init__(self, db, vault, media, drive):
        self.db,self.vault,self.media,self.drive=db,vault,media,drive

    def answer(self, property_id, field, available):
        if field not in FIELDS:raise ValueError('Question média inconnue.')
        with self.db.session() as session:
            prop=session.get(Property,property_id)
            if not prop:raise ValueError('Logement introuvable.')
            access=session.get(PropertyAccess,property_id)
            values=self.vault.decrypt(access.encrypted_data) if access else {}
            config=dict(prop.onboarding_data or {});pending=dict(config.get('media_requested',{}))
            if available:
                pending[field]=True
                if values.get(field) is False:values.pop(field)
            else:
                from sqlalchemy import select
                assigned=session.scalar(select(MediaAsset.id).where(MediaAsset.property_id==prop.id,MediaAsset.kind==FIELDS[field],MediaAsset.status=='assigned'))
                if assigned or (values.get(field) and values[field] is not False):
                    raise ValueError('Un média est déjà enregistré. Retirez-le explicitement avant d’indiquer Aucun.')
                pending.pop(field,None);values[field]=False
            config['media_requested']=pending
            config['deferred']=[k for k in config.get('deferred',[]) if k!=field]
            origins=dict(config.get('field_sources',{}));origins[field]='manual';config['field_sources']=origins
            prop.onboarding_data=config;prop.version+=1
            from app.services.knowledge import KnowledgeService
            from app.db.models import PropertyKnowledge
            from sqlalchemy import select
            if not available:KnowledgeService(self.vault).put(session,prop,field,False)
            elif (known:=session.scalar(select(PropertyKnowledge).where(PropertyKnowledge.property_id==prop.id,PropertyKnowledge.key==field,PropertyKnowledge.source=='manager'))):known.active=False
            if access:access.encrypted_data=self.vault.encrypt(values)
            else:session.add(PropertyAccess(property_id=property_id,encrypted_data=self.vault.encrypt(values)))
            session.add(AuditLog(source='onboarding:media_answer',encrypted_change=self.vault.encrypt({'property_id':property_id,'field':field,'available':available})))
            session.commit()
            return {'status':'upload_required' if available else 'saved','property_id':property_id,
                'field':field,'kind':FIELDS[field], 'message':'Ajoutez maintenant le fichier pour ce logement.' if available else 'Absence de média enregistrée. Vous pourrez en ajouter plus tard.'}

    async def upload(self, property_id, name, data, kind, share_guest=False):
        if kind not in FIELDS.values():raise ValueError('Type de média d’arrivée invalide.')
        async with self.db.lock('property_media:'+property_id):
            result=await self.media.upload(name,data,kind,property_id)
            with self.db.session() as session:
                prop=session.get(Property,property_id)
                if not prop:raise ValueError('Logement introuvable.')
                asset=session.get(MediaAsset,result['id'])
                asset.property_id,asset.status=property_id,'assigned'
                field=next(k for k,v in FIELDS.items() if v==kind)
                config=dict(prop.onboarding_data or {});pending=dict(config.get('media_requested',{}));pending.pop(field,None)
                config['media_requested']=pending;prop.onboarding_data=config;prop.version+=1
                session.add(AuditLog(source='media:assign',encrypted_change=self.vault.encrypt({'media_id':asset.id,'property_id':property_id,'explicit_upload_confirmation':True})))
                session.commit()
            if not self.drive.summary()['connected']:
                return {**result,'status':'assigned','drive_status':'not_connected','message':'Fichier enregistré dans ce logement. Connectez Google Drive pour créer le lien.'}
            try:
                return {**result,**await self.publish(result['id'],share_guest),'status':'assigned'}
            except DriveError as exc:
                return {**result,'status':'assigned','drive_status':'retry','message':str(exc)+' Cliquez sur Créer le lien Drive pour réessayer.'}

    async def publish(self, asset_id, share_guest=False):
        with self.db.session() as session:
            asset=session.get(MediaAsset,asset_id)
            if not asset or asset.status!='assigned' or not asset.property_id:raise ValueError('Média affecté au logement requis.')
            property_id,kind=asset.property_id,asset.kind
            if kind not in FIELDS.values():raise ValueError('Média d’arrivée requis.')
        data,metadata=await self.media.download(asset_id)
        result=await self.drive.upload(asset_id,property_id,metadata['name'],metadata['type'],data,share_guest)
        # Only guest-readable links are ever placed in the guest agent's access context.
        if result['shared_with_guests']:
            async with self.db.lock('manager_changes'):
                with self.db.session() as session:
                    prop=session.get(Property,property_id);access=session.get(PropertyAccess,property_id)
                    values=self.vault.decrypt(access.encrypted_data) if access else {}
                    field=next(k for k,v in FIELDS.items() if v==kind)
                    if field=='access_photos':
                        old=values.get(field,[]);old=old if isinstance(old,list) else [old] if isinstance(old,str) and old.startswith('https://') else []
                        values[field]=list(dict.fromkeys([*old,result['drive_url']]))
                    else:values[field]=result['drive_url']
                    from app.services.knowledge import KnowledgeService
                    KnowledgeService(self.vault).put(session,prop,field,values[field],reference=asset_id)
                    if access:access.encrypted_data=self.vault.encrypt(values)
                    else:session.add(PropertyAccess(property_id=property_id,encrypted_data=self.vault.encrypt(values)))
                    prop.version+=1
                    session.add(AuditLog(source='media:drive_shared',encrypted_change=self.vault.encrypt({'media_id':asset_id,'property_id':property_id,'guest_readable':True})))
                    session.commit()
        return {**result,'drive_status':'ready','message':'Fichier stocké dans Drive et lien ajouté au logement.'+(' Accessible aux voyageurs disposant du lien.' if result['shared_with_guests'] else ' Le lien reste privé.')}
