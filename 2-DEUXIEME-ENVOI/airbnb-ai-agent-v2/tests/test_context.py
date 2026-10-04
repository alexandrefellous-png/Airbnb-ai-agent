import time
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from app.db.models import GuestyMapping, Property
from app.services.context import ContextBuilder, DestinationError
from app.services.property_resolver import ResolutionError
from conftest import payload_for

def builder(env):
    settings,db,vault,guesty,_=env
    return ContextBuilder(db,guesty,vault,settings)

@pytest.mark.parametrize("name,lid,other",[("CAIRE1","listing-a","!RUE31"),("!RUE31","listing-b","CAIRE1")])
async def test_exact_property_same_address(env,name,lid,other):
    payload=payload_for(env,name)
    context,_=await builder(env).build(payload)
    assert context.property_name==name and context.guesty_listing_id==lid
    assert context.property_name!=other

async def test_unknown_listing_never_falls_back(env):
    payload=payload_for(env)
    env[3].reservations[payload['reservationId']]['stay'][0]['unitId']='unknown'
    context,_=await builder(env).build(payload)
    assert context.property_id is None and context.resolution_reason=='unmapped_unit'

async def test_conflicting_ids_block(env):
    payload=payload_for(env)
    env[3].reservations[payload['reservationId']]['stay'][0]['unitId']='unit-b'
    context,_=await builder(env).build(payload)
    assert context.resolution_reason=='conflicting_property_ids'

async def test_no_unit_type_listing_assumption(env):
    payload=payload_for(env)
    env[3].reservations[payload['reservationId']]['stay'][0]['unitTypeId']='listing-a'
    context,_=await builder(env).build(payload)
    assert context.resolution_reason=='unmapped_unit_type'

async def test_destination_conflict_no_response(env):
    payload=payload_for(env)
    payload['conversation']['_id']='wrong-conversation'
    with pytest.raises(DestinationError): await builder(env).build(payload)

@pytest.mark.parametrize("name,door",[("CAIRE1","porte en face"),("!RUE31","porte gauche")])
async def test_authorized_correct_door(env,name,door):
    context,_=await builder(env).build(payload_for(env,name))
    assert context.access_authorized and context.access['door']==door

async def test_inquiry_no_access_or_wifi(env):
    payload=payload_for(env,status='inquiry',text='Quel est le code immeuble ?')
    env[3].histories[payload['conversation']['_id']][0]['body']='ancien code TEST-BUILDING-CODE'
    context,_=await builder(env).build(payload)
    assert context.access=={} and context.wifi=={} and not context.access_authorized
    assert 'TEST-BUILDING-CODE' not in str(context.model_dump())

async def test_confirmed_near_stay_code(env):
    context,_=await builder(env).build(payload_for(env))
    assert context.access['building_code']=='TEST-BUILDING-CODE'

async def test_confirmed_far_stay_no_access(env):
    context,_=await builder(env).build(payload_for(env,now=datetime.now(timezone.utc)+timedelta(days=7)))
    assert not context.access_authorized and context.access=={}
    assert context.wifi

@pytest.mark.parametrize("name,network",[("CAIRE1","Freebox-090283"),("!RUE31","Bbox-827DAB93")])
async def test_wifi_isolated(env,name,network):
    context,_=await builder(env).build(payload_for(env,name))
    assert context.wifi['wifi_network']==network

@pytest.mark.parametrize("name,heater",[("CAIRE1","cuisine au-dessus de la machine à laver"),("!RUE31","toilettes")])
async def test_hot_water_known_location(env,name,heater):
    context,_=await builder(env).build(payload_for(env,name,text="Plus d'eau chaude"))
    assert context.property_facts['water_heater_location']==heater

async def test_electricity_known_location(env):
    context,_=await builder(env).build(payload_for(env,text="Plus d'électricité"))
    assert context.property_facts['electrical_panel_location']=='entrée'

async def test_multi_stay_no_guess(env):
    payload=payload_for(env)
    env[3].reservations[payload['reservationId']]['stay']*=2
    context,_=await builder(env).build(payload)
    assert context.resolution_reason=='multi_stay_requires_manager_mapping'

async def test_dates_from_guesty_only(env):
    payload=payload_for(env,text='Je viens du 1 au 2 janvier')
    context,_=await builder(env).build(payload)
    stay=env[3].reservations[payload['reservationId']]['stay'][0]
    assert context.reservation_check_in[:10]==stay['checkInDateLocalized']

async def test_access_photo_links_are_redacted_from_history_outside_access_window(env):
    from app.db.models import PropertyAccess
    url='https://drive.google.com/file/d/synthetic-access-photo/view'
    with env[1].session() as session:
        prop=session.scalar(select(Property).where(Property.name=='CAIRE1'))
        access=session.get(PropertyAccess,prop.id)
        values=env[2].decrypt(access.encrypted_data);values['access_photos']=[url]
        access.encrypted_data=env[2].encrypt(values);session.commit()
    payload=payload_for(env,status='inquiry',text='Send the arrival photos')
    env[3].histories[payload['conversation']['_id']][0]['body']='Old photo: '+url
    context,_=await builder(env).build(payload)
    assert not context.access_authorized and url not in str(context.model_dump())

async def test_authorized_access_context_retains_correct_property_photo_link(env):
    from app.db.models import PropertyAccess
    url='https://drive.google.com/file/d/synthetic-access-photo/view'
    with env[1].session() as session:
        prop=session.scalar(select(Property).where(Property.name=='CAIRE1'))
        access=session.get(PropertyAccess,prop.id)
        values=env[2].decrypt(access.encrypted_data);values['access_photos']=[url]
        access.encrypted_data=env[2].encrypt(values);session.commit()
    context,_=await builder(env).build(payload_for(env))
    assert context.access_authorized and context.access['access_photos']==[url]
