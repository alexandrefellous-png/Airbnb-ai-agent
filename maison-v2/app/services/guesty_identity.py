"""Confirm explicit V3 unit relationships to a freshly verified canonical listing."""
import time
import uuid
from sqlalchemy import select
from app.core.tenancy import manager_identity
from app.db.models import Property,GuestyMapping,ManagerWorkspace,AuditLog

class GuestyIdentityService:
    def __init__(self,db,vault,guesty):self.db,self.vault,self.guesty=db,vault,guesty

    async def propose(self,cid):
        identity=manager_identity()
        if not identity or identity['role'] not in {'owner','admin','manager'}:raise PermissionError('Accès manager requis.')
        conversation=await self.guesty.conversation(cid)
        metas=conversation.get('meta',{}).get('reservations',[])
        if len(metas)!=1:raise ValueError('Plusieurs réservations : une vérification manuelle est nécessaire.')
        rid=metas[0].get('_id');lid=metas[0].get('listing',{}).get('_id')
        if not isinstance(rid,str) or not isinstance(lid,str):raise ValueError('Les identifiants Guesty sont incomplets.')
        reservation=await self.guesty.reservation(rid)
        if reservation.get('_id')!=rid or reservation.get('conversationId')!=cid:raise ValueError('La réservation et la conversation ne correspondent pas.')
        stays=reservation.get('stay',[])
        if not isinstance(stays,list) or len(stays)!=1:raise ValueError('Séjour multi-logements : association automatique bloquée.')
        listing=await self.guesty.listing(lid)
        if listing.get('_id')!=lid:raise ValueError('Listing non vérifié.')
        pairs=[{'kind':kind,'external_id':stays[0][field]} for field,kind in [('unitId','unit'),('unitTypeId','unit_type')] if stays[0].get(field)]
        if not pairs or any(not isinstance(p['external_id'],str) for p in pairs):raise ValueError('Identifiants d’unité non exploitables.')
        async with self.db.lock('manager_changes'):
            with self.db.session() as session:
                prop=session.scalar(select(Property).where(Property.guesty_listing_id==lid))
                if not prop:raise ValueError('Importez d’abord ce logement depuis Guesty dans Logements → Ajouter.')
                for pair in pairs:
                    existing=session.scalar(select(GuestyMapping).where(GuestyMapping.kind==pair['kind'],GuestyMapping.external_id==pair['external_id']))
                    if existing and existing.property_id!=prop.id:raise ValueError('Un identifiant pointe vers un autre logement. Vérification manuelle requise.')
                change_id='im:'+uuid.uuid4().hex
                value={'property_id':prop.id,'property_name':prop.name,'guesty_name':listing.get('nickname') or listing.get('title') or prop.guesty_name,
                    'address':listing.get('address',{}).get('full') or prop.address,'listing_id':lid,
                    'reservation_id':rid,'conversation_id':cid,'pairs':pairs,'expected_version':prop.version,
                    'proposed_by':identity['user_id'],'expires_at':time.time()+900,'status':'pending'}
                session.add(ManagerWorkspace(id=change_id,encrypted_data=self.vault.encrypt(value)));session.commit()
                return {**value,'change_id':change_id}

    async def confirm(self,change_id,accept):
        identity=manager_identity()
        if not identity or identity['role'] not in {'owner','admin','manager'}:raise PermissionError('Accès manager requis.')
        async with self.db.lock('manager_changes'):
            with self.db.session() as session:
                row=session.get(ManagerWorkspace,change_id)
                if not row or not change_id.startswith('im:'):raise ValueError('Confirmation introuvable.')
                value=self.vault.decrypt(row.encrypted_data)
                if value['proposed_by']!=identity['user_id'] or value['status']!='pending' or value['expires_at']<time.time():raise ValueError('Confirmation invalide ou expirée.')
                prop=session.get(Property,value['property_id'])
                if not prop or prop.version!=value['expected_version'] or prop.guesty_listing_id!=value['listing_id']:raise ValueError('La fiche a changé. Relancez sa vérification.')
                if accept:
                    for pair in value['pairs']:
                        existing=session.scalar(select(GuestyMapping).where(GuestyMapping.kind==pair['kind'],GuestyMapping.external_id==pair['external_id']))
                        if existing and existing.property_id!=prop.id:raise ValueError('Association contradictoire. Aucun changement effectué.')
                        if not existing:session.add(GuestyMapping(property_id=prop.id,kind=pair['kind'],external_id=pair['external_id'],source='confirmed_guesty_v3_relationship'))
                    prop.version+=1
                value['status']='confirmed' if accept else 'cancelled';row.encrypted_data=self.vault.encrypt(value)
                session.add(AuditLog(source='guesty:identity_confirmation',property_id=prop.id,encrypted_change=self.vault.encrypt(value)))
                session.commit()
                return {'message':'Association vérifiée et enregistrée. Les prochains messages utiliseront la fiche de '+prop.name if accept else 'Association annulée.','status':value['status']}
