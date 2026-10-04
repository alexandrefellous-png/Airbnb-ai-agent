import copy
import time
from datetime import datetime,timezone,timedelta
import pytest
from sqlalchemy import select,func,delete
from app.core.tenancy import organization_scope
from app.db.models import Property,PropertySync,PropertyKnowledge,PropertyAccess,GuestyMapping,Organization,Escalation,AIObservation
from app.services.listing_sync import ListingSyncService
from app.services.knowledge import KnowledgeService
from app.services.manager import ManagerService
from app.services.context import ContextBuilder
from app.services.learning import LearningService
from app.main import create_app
from app.schemas.actions import GuestReply
from conftest import payload_for
from test_manager import action

def listing(lid,name):
    return {'_id':lid,'nickname':name,'active':True,'address':{'full':'Adresse synthétique'},'timezone':'Europe/Paris',
        'defaultCheckInTime':'16:00','defaultCheckOutTime':'10:00','accommodates':4,'bedrooms':2,'amenities':['Elevator','Washer'],
        'wifiName':'synthetic-network','wifiPassword':'synthetic-password'}

def sync_service(env,rows):
    env[0].guesty_client_id='synthetic'
    async def listings():return copy.deepcopy(rows)
    env[3].listings=listings
    return ListingSyncService(env[1],env[2],env[3],env[0])

async def test_full_discovery_resync_rename_no_duplicates_and_memory_preserved(env):
    rows=[listing('listing-a','CAIRE1'),listing('listing-b','!RUE31'),listing('listing-new','New')]
    service=sync_service(env,rows)
    result=await service.sync()
    assert result['state']=='ready' and result['created']==1
    with env[1].session() as session:
        prop=session.scalar(select(Property).where(Property.guesty_listing_id=='listing-a'));pid=prop.id
        original=env[2].decrypt(session.get(PropertyAccess,pid).encrypted_data)
    manager=ManagerService(env[1],env[2],env[4],env[3])
    await manager.propose(action('update_fields',updates=[{'field':'trash_location','value':'Local au fond de la cour'}]))
    rows[0]['nickname']='CAIRE RENOMMÉ';rows[0]['accommodates']=6
    rows[0]['amenities']=['Washer']
    result=await service.sync(force=True)
    assert result['state']=='ready' and result['created']==0 and result['updated']==1
    with env[1].session() as session:
        assert session.scalar(select(func.count()).select_from(Property))==3
        prop=session.get(Property,pid)
        assert prop.name=='CAIRE RENOMMÉ' and prop.facts['capacity']==6
        assert prop.facts['trash_location']=='Local au fond de la cour'
        assert env[2].decrypt(session.get(PropertyAccess,pid).encrypted_data)==original
        memory=KnowledgeService(env[2]).effective(KnowledgeService(env[2]).rows(session,pid))
        assert memory['trash_location']['source']=='manager'
        assert memory['elevator']['source']=='manager'  # prior explicit knowledge retained
    assert not env[3].send_calls

async def test_inactive_missing_and_failed_snapshot_never_delete_knowledge(env):
    rows=[listing('listing-a','CAIRE1'),listing('listing-b','!RUE31')]
    service=sync_service(env,rows);await service.sync()
    rows[0]['active']=False;rows.pop()
    assert (await service.sync(force=True))['missing']==1
    with env[1].session() as session:
        statuses={r.external_id:r.status for r in session.scalars(select(PropertySync))}
        assert statuses=={'listing-a':'inactive','listing-b':'missing'}
        assert session.scalar(select(func.count()).select_from(Property))==2
    async def fails():raise RuntimeError('network')
    env[3].listings=fails
    assert (await service.sync(force=True))['state']=='error'
    with env[1].session() as session:assert {r.external_id:r.status for r in session.scalars(select(PropertySync))}==statuses

async def test_same_name_distinct_ids_and_different_tenants(env):
    rows=[listing('new-a','Identical'),listing('new-b','Identical')]
    service=sync_service(env,rows)
    assert (await service.sync())['created']==2
    with env[1].system_session() as session:
        org=Organization(name='Other');session.add(org);session.commit();oid=org.id
    with organization_scope(oid):
        assert (await service.sync())['created']==2
        with env[1].session() as session:assert len(set(session.scalars(select(Property.name))))==2

async def test_natural_chat_extracts_multiple_fields_confirms_and_retains_property(env):
    service=ManagerService(env[1],env[2],env[4],env[3])
    env[4].action=action('update_fields',updates=[{'field':'floor','value':'1er étage'},{'field':'building_code','value':'TEST-7531'},{'field':'lockbox_location','value':'Tout à gauche'}])
    result=await service.chat('CAIRE1 est au premier étage. Code immeuble TEST-7531. Boîte à clés tout à gauche.')
    assert result['status']=='pending_confirmation'
    await service.confirm(result['change_id'],True)
    with env[1].session() as session:
        a=session.scalar(select(Property).where(Property.name=='CAIRE1'))
        b=session.scalar(select(Property).where(Property.name=='!RUE31'))
        data=env[2].decrypt(session.get(PropertyAccess,a.id).encrypted_data)
        assert data['floor']=='1er étage' and data['building_code']=='TEST-7531'
        assert env[2].decrypt(session.get(PropertyAccess,b.id).encrypted_data)['building_code']!='TEST-7531'
    env[4].action=action('update_fields',updates=[{'field':'trash_location','value':'Dans la cour'}])
    await service.chat('CAIRE1 : les poubelles sont dans la cour.')
    env[4].action=action('update_fields',updates=[{'field':'trash_location','value':'Dans le local à droite'}])
    await service.chat('Les poubelles de CAIRE1 sont maintenant dans le local à droite.')
    with env[1].session() as session:
        rows=list(session.scalars(select(PropertyKnowledge).where(PropertyKnowledge.property_id==a.id,PropertyKnowledge.key=='trash_location',PropertyKnowledge.source=='manager')))
        assert len(rows)==1 and env[2].decrypt(rows[0].encrypted_value)=='Dans le local à droite'

async def test_temporary_knowledge_expires_and_uncertain_inference_never_becomes_fact(env):
    payload=payload_for(env)
    svc=ManagerService(env[1],env[2],env[4],env[3])
    until=datetime.now(timezone.utc)+timedelta(hours=1)
    await svc.propose(action('update_fields',updates=[{'field':'trash_location','value':'Bacs provisoires dans la cour'}],knowledge_type='temporary',valid_until=until))
    with env[1].session() as session:
        p=session.scalar(select(Property).where(Property.name=='CAIRE1'))
        KnowledgeService(env[2]).put(session,p,'luggage_policy','Invented guess','inferred',confidence=.4);session.commit()
    builder=ContextBuilder(env[1],env[3],env[2],env[0])
    context,_=await builder.build(payload)
    assert context.property_facts['trash_location']=='Bacs provisoires dans la cour'
    assert 'luggage_policy' not in context.property_facts
    context,_=await builder.build(payload,now=until.timestamp()+1)
    assert 'trash_location' not in context.property_facts

async def test_fresh_guesty_relationship_resolves_synced_listing_without_guessing_ids(env):
    payload=payload_for(env)
    service=sync_service(env,[listing('listing-a','CAIRE1'),listing('listing-b','!RUE31')]);await service.sync()
    with env[1].session() as session:
        p=session.scalar(select(Property).where(Property.name=='CAIRE1'))
        session.execute(delete(GuestyMapping).where(GuestyMapping.property_id==p.id,GuestyMapping.kind.in_(['unit','unit_type'])));session.commit()
    context,_=await ContextBuilder(env[1],env[3],env[2],env[0]).build(payload)
    assert context.property_name=='CAIRE1' and context.resolution_reason is None

async def test_unknown_answer_never_invented_asked_once_then_learned_and_resumed(env):
    payload=payload_for(env,text='Where should we put the garbage?')
    env[4].reply=GuestReply(text='Invented: outside the green door.',escalation_required=False,escalation_summary=None,priority='normal')
    app=create_app(env[0],env[1],env[3],env[4])
    queued=app.state.queue.enqueue(payload);await app.state.processor.process([queued['event_id']])
    with env[1].session() as session:
        observation=session.scalar(select(AIObservation))
        assert 'green door' not in env[2].decrypt(observation.encrypted_original)
        questions=list(session.scalars(select(Escalation).where(Escalation.dedup_key.like('learn:%'))))
        assert len(questions)==1 and 'poubelles' in env[2].decrypt(questions[0].encrypted_summary)
    context,_=await app.state.builder.build(payload)
    LearningService(env[1],env[2]).ask(context,['trash_location'])
    with env[1].session() as session:assert session.scalar(select(func.count()).select_from(Escalation).where(Escalation.dedup_key.like('learn:%')))==1
    await app.state.manager.propose(action('update_fields',updates=[{'field':'trash_location','value':'Dans le local de la cour'}]))
    env[4].reply=GuestReply(text='The bins are in the courtyard storage room.',escalation_required=False,escalation_summary=None,priority='normal')
    result=await LearningService(env[1],env[2]).resume(app.state)
    assert len(result)==1 and result[0]['status']=='simulated'
    context,_=await app.state.builder.build(payload)
    assert not LearningService(env[1],env[2]).missing(context,['Where is the garbage?'],['trash_location'])
    assert not env[3].send_calls

async def test_synced_wifi_never_sent_to_inquiry_and_local_override_survives(env):
    service=sync_service(env,[listing('listing-a','CAIRE1'),listing('listing-b','!RUE31')]);await service.sync()
    payload=payload_for(env,status='inquiry')
    context,_=await ContextBuilder(env[1],env[3],env[2],env[0]).build(payload)
    assert context.wifi=={} and 'wifi_password' not in context.property_facts
    manager=ManagerService(env[1],env[2],env[4],env[3])
    result=await manager.propose(action('update_fields',updates=[{'field':'wifi_password','value':'NEW-MANAGER-PASSWORD'}]))
    await manager.confirm(result['change_id'],True);await service.sync(force=True)
    payload=payload_for(env,status='confirmed',suffix='new')
    context,_=await ContextBuilder(env[1],env[3],env[2],env[0]).build(payload)
    assert context.wifi['wifi_password']=='NEW-MANAGER-PASSWORD'

async def test_guesty_document_extraction_requires_verbatim_evidence_and_is_cached(env):
    from app.services.guesty_knowledge import GuestyKnowledgeReader,QuotedFacts,QuotedFact
    calls=[]
    async def parse(schema,prompt,data):
        calls.append(data)
        return QuotedFacts(facts=[
            QuotedFact(field='trash_location',value='local dans la cour',document='houseManual',quote='Les poubelles sont dans le local dans la cour.'),
            QuotedFact(field='lockbox_code',value='INVENTED-999',document='houseManual',quote='Le code est INVENTED-999.'),
            QuotedFact(field='organization_id',value='cour',document='houseManual',quote='cour')])
    env[4].parse=parse
    reader=GuestyKnowledgeReader(env[1],env[2],env[4])
    data=listing('new','Synthetic');data['houseManual']='Les poubelles sont dans le local dans la cour.'
    result=await reader.read(data)
    assert set(result)=={'trash_location'}
    assert await reader.read(data)==result and len(calls)==1

async def test_late_checkout_explicit_permission_without_relaxing_safety(env):
    from app.services.policies import guard_reply
    from app.schemas.actions import GuestPlan
    payload=payload_for(env)
    svc=ManagerService(env[1],env[2],env[4],env[3])
    await svc.propose(action('update_fields',updates=[{'field':'late_checkout_until','value':'14:00'}]))
    context,_=await ContextBuilder(env[1],env[3],env[2],env[0]).build(payload)
    assert context.property_rules['late_checkout_until']=='14:00'
    assert context.property_facts['check_out']=='10:00'
    plan=GuestPlan(availability_requested=False,requested_check_in=None,requested_check_out=None,manager_required=True,priority='normal',reason='late checkout')
    reply=GuestReply(text='Oui, vous pouvez partir à 14h.',escalation_required=True,escalation_summary='late',priority='normal')
    assert not guard_reply(context,['Puis-je partir à 14h ?'],plan,reply,'fr').escalation_required
    reply.text='Démontez l’appareil.'
    assert guard_reply(context,['Puis-je partir à 14h ?'],plan,reply,'fr').escalation_required
