"""Synthetic local demo used only for browser verification; never imports private owner data."""
import asyncio
import tempfile
from pathlib import Path
import sys
from cryptography.fernet import Fernet
from sqlalchemy import select
sys.path.insert(0,str(Path(__file__).parents[1]/'tests'))
from conftest import FakeAI,FakeGuesty,payload_for
from app.core.config import Settings
from app.core.security import Vault
from app.core.tenancy import organization_scope
from app.db.models import Base,Property,PropertyAccess,PropertyWifi,PropertyState,GuestyMapping,ListingLifecycle
from app.db.session import Database
from scripts.create_workspace import provision
from app.main import create_app

def fixture():
    target=Path(tempfile.mkdtemp(prefix='airbnb-browser-',dir='/private/tmp'))
    settings=Settings(_env_file=None,database_url='sqlite:///'+str(target/'demo.db'),token_encryption_key=Fernet.generate_key().decode(),admin_secret='browser-fixture-password-123456789',worker_enabled=False,debounce_seconds=0)
    db=Database(settings.database_url);Base.metadata.create_all(db.engine);vault=Vault(settings.token_encryption_key)
    oid=provision(db,vault,'Maison Conciergerie','admin',settings.admin_secret)
    guesty,ai=FakeGuesty(),FakeAI();ai.client=None
    async def listing(lid):return {'_id':lid,'nickname':'MARINE2','address':{'full':'12 rue des Étoiles, Paris'},'timezone':'Europe/Paris','defaultCheckInTime':'16:00','defaultCheckOutTime':'10:00','accommodates':4,'bedrooms':2,'bathrooms':1}
    guesty.listing=listing
    with organization_scope(oid),db.session() as session:
        for name,lid,unit,code in [('CAIRE1','listing-a','unit-a','SYNTHETIC-A'),('!RUE31','listing-b','unit-b','SYNTHETIC-B')]:
            p=Property(name=name,guesty_listing_id=lid,guesty_name='Appartement '+name,address='12 rue des Étoiles, Paris',timezone='Europe/Paris',check_in='16:00',check_out='10:00',onboarding_step='ready',status='active',is_active=True,facts={'capacity':4,'bedrooms':2,'bathrooms':1,'elevator':True,'air_conditioning':True,'water_heater_location':'Placard de la cuisine','electrical_panel_location':'Entrée'})
            session.add(p);session.flush()
            session.add(PropertyAccess(property_id=p.id,encrypted_data=vault.encrypt({'floor':'1er','stairs':'A','street_instructions':'Traversez la cour et prenez l’escalier A.','door':'Porte de droite','lockbox_location':'Devant la porte','lockbox_code':code})))
            session.add(PropertyWifi(property_id=p.id,encrypted_data=vault.encrypt({'wifi_network':'Maison-Guest','wifi_password':'SYNTHETIC-WIFI-'+name})))
            session.add(PropertyState(property_id=p.id,key='elevator',status='out_of_order' if name=='CAIRE1' else 'working'))
            for kind,external in [('listing',lid),('unit',unit),('unit_type','type-'+unit)]:session.add(GuestyMapping(property_id=p.id,kind=kind,external_id=external,source='synthetic_browser_fixture'))
            session.add(ListingLifecycle(property_id=p.id,canonical_listing_id=lid,to_status='active',source='synthetic_browser_fixture'))
        session.commit()
    env=(settings,db,vault,guesty,ai)
    with organization_scope(oid):
        payload=payload_for(env,text='Can we check in at 1pm?')
        ai.plan.manager_required=True;ai.plan.reason='Early check-in à confirmer par le manager.'
        ai.reply.text='Hi! I’ll check what we can do for you and get back to you shortly :)'
        app=create_app(settings,db,guesty,ai);app.state.queue.enqueue(payload);asyncio.run(app.state.processor.tick())
        cid=payload['conversation']['_id']
        app.state.observation.write('oc:synthetic',{'conversation_id':cid,'guest_name':'Voyageur fictif','property_name':'CAIRE1','posts':[{'id':'synthetic-post','body':'Can we check in at 1pm?','sender':'guest','created_at':payload['message']['createdAt']}]})
    return app

app=fixture()
