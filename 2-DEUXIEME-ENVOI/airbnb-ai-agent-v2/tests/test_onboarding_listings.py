import time
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.db.models import Property,ListingLifecycle,GuestyMapping
from app.services.manager import ManagerService
from app.services.onboarding import OnboardingService
from app.services.properties import PropertyService
from app.core.tenancy import organization_scope,organization_id
from test_manager import action
from test_tenancy import second

async def test_create_100_listings_without_code_change_and_counts(env,second):
    svc=ManagerService(env[1],env[2],env[4],env[3]);own=organization_id()
    for i in range(100):
        await svc.propose(action('create_property',property='Apartment-'+str(i)))
        result=await svc.propose(action('update_fields',property='Apartment-'+str(i),updates=[{'field':'guesty_listing_id','value':'verified-listing-'+str(i)},{'field':'address','value':'Synthetic address'},{'field':'timezone','value':'Europe/Paris'},{'field':'check_in','value':'16:00'},{'field':'check_out','value':'10:00'}]))
        await svc.confirm(result['change_id'],True)
        pending=await svc.propose(action('activate_property',property='Apartment-'+str(i)))
        await svc.confirm(pending['change_id'],True)
    assert svc.listings.counts(env[1])['active_listings']==102
    with organization_scope(second[0]):assert svc.listings.counts(env[1])['active_listings']==1

async def test_import_known_fields_question_groups_and_sources(env):
    svc=ManagerService(env[1],env[2],env[4],env[3])
    async def listing(lid):return {'_id':lid,'nickname':'NEW','address':{'full':'Known address'},'timezone':'Europe/Paris','defaultCheckInTime':'15:00','defaultCheckOutTime':'11:00','accommodates':4,'bedrooms':2,'bathrooms':1.5}
    env[3].listing=listing
    await svc.propose(action('onboarding'))
    pending=await svc.select_listing('new-listing','NEW')
    await svc.confirm(pending['change_id'],True)
    p=next(p for p in PropertyService(env[1],env[2]).list() if p['name']=='NEW')
    progress=p['configuration']
    assert p['facts']['capacity']==4 and p['facts']['bathrooms']==1.5
    assert 'address' not in progress['missing'] and 'capacity' not in progress['missing']
    assert progress['field_sources']['capacity']=='guesty'
    assert len(progress['questions'])<=3
    assert not progress['critical_missing'] and progress['configuration_status']=='ready'

async def test_defer_resume_and_activation_with_secondary_fields_missing(env):
    svc=ManagerService(env[1],env[2],env[4],env[3])
    await svc.propose(action('create_property',property='NEW'))
    await svc.propose(action('defer_onboarding',property='NEW'))
    p=next(p for p in PropertyService(env[1],env[2]).list() if p['name']=='NEW')
    assert p['configuration']['deferred'] and p['configuration']['critical_missing']
    svc=ManagerService(env[1],env[2],env[4],env[3])
    await svc.propose(action('resume_onboarding',property='NEW'))
    p=next(p for p in PropertyService(env[1],env[2]).list() if p['name']=='NEW')
    assert not p['configuration']['deferred']
    pending=await svc.propose(action('activate_property',property='NEW'))
    with pytest.raises(ValueError):await svc.confirm(pending['change_id'],True)
    update=await svc.propose(action('update_fields',property='NEW',updates=[{'field':'guesty_listing_id','value':'new-id'},{'field':'address','value':'Confirmed address'},{'field':'timezone','value':'Europe/Paris'},{'field':'check_in','value':'16:00'},{'field':'check_out','value':'10:00'}]))
    await svc.confirm(update['change_id'],True)
    pending=await svc.propose(action('activate_property',property='NEW'));await svc.confirm(pending['change_id'],True)
    p=next(p for p in PropertyService(env[1],env[2]).list() if p['name']=='NEW')
    assert p['status']=='active' and p['is_active'] and p['configuration']['missing']
    assert 'water_heater_location' not in p['facts']

async def test_activation_deactivation_history_and_historical_counts(env):
    svc=ManagerService(env[1],env[2],env[4],env[3])
    # Existing inventory is synthetic in this fixture; record a baseline before exercising transitions.
    with env[1].session() as session:
        p=session.scalar(select(Property).where(Property.name=='CAIRE1'));svc.listings.record(session,p,None,'active','fixture:initial');session.commit();pid=p.id
    baseline=time.time();assert svc.listings.counts(env[1],baseline)['active_listings']==1
    pending=await svc.propose(action('deactivate_property'));await svc.confirm(pending['change_id'],True)
    assert svc.listings.counts(env[1])['active_listings']==1
    with env[1].session() as session:
        p=session.get(Property,pid);assert not p.is_active and p.status=='inactive' and p.deactivated_at
        assert len(session.scalars(select(ListingLifecycle).where(ListingLifecycle.property_id==pid)).all())==2
    assert svc.listings.counts(env[1],baseline)['active_listings']==1
    pending=await svc.propose(action('activate_property'));await svc.confirm(pending['change_id'],True)
    assert svc.listings.counts(env[1])['active_listings']==2
    with env[1].session() as session:assert len(session.scalars(select(ListingLifecycle).where(ListingLifecycle.property_id==pid)).all())==3

async def test_duplicate_canonical_listing_cannot_be_counted_twice(env):
    svc=ManagerService(env[1],env[2],env[4],env[3])
    await svc.propose(action('create_property',property='DUPLICATE'))
    pending=await svc.propose(action('update_fields',property='DUPLICATE',updates=[{'field':'guesty_listing_id','value':'listing-a'}]))
    with pytest.raises(ValueError):await svc.confirm(pending['change_id'],True)
    assert svc.listings.counts(env[1])['active_listings']==2
    with env[1].session() as session:
        session.add(Property(name='Direct duplicate',guesty_listing_id='listing-a'))
        with pytest.raises(IntegrityError):session.commit()

def test_listing_cards_use_bounded_queries_for_many_properties(env):
    from sqlalchemy import event
    from app.services.properties import PropertyService
    with env[1].session() as session:
        for i in range(100):session.add(Property(name=f'PERFORMANCE-{i}',is_active=False,status='onboarding'))
        session.commit()
    queries=[]
    def count(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
    event.listen(env[1].engine,'before_cursor_execute',count)
    try:
        rows=PropertyService(env[1],env[2]).list()
    finally:
        event.remove(env[1].engine,'before_cursor_execute',count)
    assert len(rows)==102 and len(queries)==9  # two additional batched reads: sync + knowledge
