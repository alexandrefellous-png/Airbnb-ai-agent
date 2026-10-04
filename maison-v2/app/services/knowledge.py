"""Provenance and effective knowledge, layered over the existing encrypted stores."""
import time
from sqlalchemy import select
from app.db.models import PropertyKnowledge, PropertyAccess, PropertyWifi

# Guesty owns the operational listing facts; Maison owns additional instructions.
OPERATIONAL={'name','address','timezone','check_in','check_out','capacity','bedrooms','beds','bathrooms','amenities','equipment',
             'elevator','air_conditioning','washing_machine','dryer','dishwasher','tv','oven','microwave','heating','kitchen'}
RULE_FIELDS={'early_checkin_policy','late_checkout_policy','luggage_policy','early_checkin_from','late_checkout_until'}
EXTRA_FIELDS={'trash_location','heating_instructions','ac_instructions',*RULE_FIELDS}

class KnowledgeService:
    def __init__(self,vault):self.vault=vault

    def put(self,session,prop,key,value,source='manager',reference=None,confidence=1,kind='permanent',until=None):
        row=session.scalar(select(PropertyKnowledge).where(PropertyKnowledge.property_id==prop.id,PropertyKnowledge.key==key,PropertyKnowledge.source==source))
        if row and self.vault.decrypt(row.encrypted_value)==value and row.active and row.valid_until==until and row.knowledge_type==kind:
            return row
        if not row:
            row=PropertyKnowledge(property_id=prop.id,key=key,source=source);session.add(row)
        row.encrypted_value=self.vault.encrypt(value);row.source_reference=reference;row.confidence=confidence
        row.knowledge_type=kind;row.valid_from=row.updated_at=time.time();row.valid_until=until;row.active=True
        return row

    def effective(self,rows,now=None):
        now=time.time() if now is None else now
        chosen={}
        for row in rows:
            if not row.active or row.valid_from>now or (row.valid_until is not None and row.valid_until<=now):continue
            if row.source=='inferred' or row.confidence<0.9:continue
            priority=(4 if row.source=='guesty' and row.key in OPERATIONAL else {'manager':3,'conversation':2,'guesty':1}.get(row.source,0),row.updated_at)
            if row.key not in chosen or priority>chosen[row.key][0]:chosen[row.key]=(priority,row)
        return {key:{'value':self.vault.decrypt(row.encrypted_value),'source':row.source,'confidence':row.confidence,
                'knowledge_type':row.knowledge_type,'valid_until':row.valid_until,'updated_at':row.updated_at,'source_reference':row.source_reference}
                for key,(_,row) in chosen.items()}

    def rows(self,session,pid):
        return list(session.scalars(select(PropertyKnowledge).where(PropertyKnowledge.property_id==pid)))

    def backfill(self,session,prop):
        if (prop.onboarding_data or {}).get('memory_migrated'):return
        origins=(prop.onboarding_data or {}).get('field_sources',{})
        values={**prop.facts,**prop.rules}
        for model in (PropertyAccess,PropertyWifi):
            row=session.get(model,prop.id)
            if row:values.update(self.vault.decrypt(row.encrypted_data))
        for key,value in values.items():
            source='guesty' if origins.get(key,'').startswith('guesty') else 'manager'
            self.put(session,prop,key,value,source,'legacy:preserved')
        prop.onboarding_data={**(prop.onboarding_data or {}),'memory_migrated':True}
