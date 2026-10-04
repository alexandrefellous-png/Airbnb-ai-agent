"""Import a private catalogue into an explicitly selected workspace, without overwriting existing properties."""
import argparse
import json
from contextlib import nullcontext
from pathlib import Path
from sqlalchemy import select
from app.core.config import Settings
from app.core.security import Vault
from app.core.tenancy import organization_scope
from app.db.models import AuditLog,GuestyMapping,Property,PropertyAccess,PropertyState,PropertyWifi
from app.db.session import Database
from app.services.listings import ListingService

def bootstrap(path,settings=None,organization_id=None):
    settings=settings or Settings();db=Database(settings.database_url);vault=Vault(settings.token_encryption_key)
    rows=json.loads(Path(path).read_text())
    scope=organization_scope(organization_id) if organization_id else nullcontext()
    with scope,db.session() as session:
        for data in rows:
            if session.scalar(select(Property).where(Property.name==data['name'])):continue
            origins={k:'private_import' for k in set(data.get('facts',{}))|set(data.get('access',{}))|set(data.get('wifi',{}))|{'name','guesty_listing_id','address','timezone','check_in','check_out'}}
            prop=Property(name=data['name'],guesty_listing_id=data.get('guesty_listing_id'),address=data.get('address',''),timezone=data.get('timezone',''),
                check_in=data.get('check_in',''),check_out=data.get('check_out',''),facts=data.get('facts',{}),rules=data.get('rules',{}),
                is_active=False,status='onboarding',onboarding_step='identity',onboarding_data={'field_sources':origins})
            session.add(prop);session.flush()
            if prop.guesty_listing_id:session.add(GuestyMapping(property_id=prop.id,kind='listing',external_id=prop.guesty_listing_id,source='private_bootstrap'))
            for mapping in data.get('mappings',[]):
                if mapping['kind'] not in {'unit','unit_type','last_stay_listing'}:raise ValueError('Mapping non autorisé')
                session.add(GuestyMapping(property_id=prop.id,kind=mapping['kind'],external_id=mapping['external_id'],source='explicit_private_mapping'))
            for model,key in [(PropertyAccess,'access'),(PropertyWifi,'wifi')]:
                if data.get(key):session.add(model(property_id=prop.id,encrypted_data=vault.encrypt(data[key])))
            for state in data.get('states',[]):session.add(PropertyState(property_id=prop.id,**state))
            ListingService().record(session,prop,None,'onboarding','private_bootstrap')
            session.add(AuditLog(property_id=prop.id,source='bootstrap',encrypted_change=vault.encrypt({'old':{},'new':data})))
        session.commit()
    db.engine.dispose()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('catalogue');parser.add_argument('--organization-id',required=True)
    args=parser.parse_args();bootstrap(args.catalogue,organization_id=args.organization_id)
    print('Catalogue importé ; aucune donnée sensible affichée.')
