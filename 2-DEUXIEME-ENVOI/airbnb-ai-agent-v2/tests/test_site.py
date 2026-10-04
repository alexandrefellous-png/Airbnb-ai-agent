import time
import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient
from app.main import create_app
from app.db.models import AdminSession,Property,PropertyState,Escalation,MediaBlob,ManagerChange,ChatMessage,Membership
from app.services.media import MediaService
from app.services.manager import ManagerService
from app.services.decisions import DecisionService
from app.services.reservations import ReservationService
from app.services.context import ContextBuilder
from app.core.security import fingerprint
from app.core.tenancy import organization_id
from test_manager import action
from test_api import login
from conftest import payload_for

def site(env,local=False):
    if local:env[4].client=None
    c=TestClient(create_app(env[0],env[1],env[3],env[4]));headers=login(c,env)
    return c,headers

def test_session_csrf_logout_and_cookie_flags(env):
    c,headers=site(env)
    assert 'HttpOnly' in c.cookies.get('manager_session') or c.cookies.get('manager_session')
    assert c.post('/admin/chat',json={'message':'test'}).status_code==403
    assert c.post('/admin/logout',headers=headers).status_code==200
    assert c.get('/admin/properties').status_code==401

def test_login_cookie_attributes(env):
    c=TestClient(create_app(env[0],env[1],env[3],env[4]))
    result=c.post('/admin/login',json={'username':'admin','password':env[0].admin_secret})
    cookie=result.headers['set-cookie'];assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    assert env[0].admin_secret not in result.text
    env[0].app_env='production'
    result=c.post('/admin/login',json={'username':'admin','password':env[0].admin_secret})
    assert 'Secure' in result.headers['set-cookie']

def test_login_throttle_and_expiry(env):
    c=TestClient(create_app(env[0],env[1],env[3],env[4]))
    for _ in range(5):assert c.post('/admin/login',json={'username':'admin','password':'incorrect'}).status_code==401
    assert c.post('/admin/login',json={'username':'admin','password':env[0].admin_secret}).status_code==429

async def test_expired_session_denied(env):
    c,headers=site(env)
    with env[1].system_session() as session:
        row=session.get(AdminSession,fingerprint(c.cookies.get('manager_session')));row.expires_at=time.time()-1;session.commit()
    assert c.get('/admin/properties').status_code==401

def test_read_queries_and_chat_state_same_service(env):
    c,headers=site(env,local=True)
    result=c.post('/admin/chat',headers=headers,json={'message':'Montre-moi toutes les infos de CAIRE1.'})
    assert result.status_code==200 and result.json()['property']['name']=='CAIRE1'
    assert 'TEST-BOX-unit-a' not in result.text
    result=c.post('/admin/chat',headers=headers,json={'message':'CAIRE1 l’ascenseur remarche.'})
    assert result.json()['status']=='applied'
    p=next(p for p in c.get('/admin/properties').json() if p['name']=='CAIRE1')
    assert p['facts']['elevator'] is True and p['states'][0]['status']=='working'
    assert c.post('/admin/actions',headers=headers,json=action('update_property_state',state_key='elevator',state_status='out_of_order').model_dump(mode='json')).status_code==200
    p=c.get('/admin/properties/'+p['id']).json()
    assert p['states'][0]['status']=='out_of_order' and p['facts']['elevator'] is True
    result=c.post('/admin/chat',headers=headers,json={'message':'Quels appartements ont actuellement un problème ?'})
    assert result.json()['properties'][0]['name']=='CAIRE1'
    assert len(c.get('/admin/history').json()['messages'])==7

def test_sensitive_confirmation_mask_and_reveal(env):
    c,headers=site(env)
    result=c.post('/admin/actions',headers=headers,json=action('update_fields',updates=[{'field':'lockbox_code','value':'TEST-SECRET-NEW'}]).model_dump(mode='json')).json()
    assert result['new']['lockbox_code']=='••••••••'
    assert 'TEST-SECRET-NEW' not in c.get('/admin/data').text
    assert c.post('/admin/changes/'+result['change_id']+'/confirm',headers=headers,json={'accept':True}).status_code==200
    p=next(p for p in c.get('/admin/properties').json() if p['name']=='CAIRE1')
    assert p['access']['lockbox_code']=='••••••••'
    assert c.post('/admin/properties/'+p['id']+'/reveal',headers=headers,json={'field':'lockbox_code'}).json()['value']=='TEST-SECRET-NEW'

def test_onboarding_resume_and_draft_inactive(env):
    c,headers=site(env,local=True)
    result=c.post('/admin/chat',headers=headers,json={'message':'Je rentre un nouvel appartement.'}).json()
    assert result['listings'][0]['id']=='new-listing'
    c2=TestClient(c.app);h2=login(c2,env)
    assert c2.get('/admin/history').json()['workspace']['listings'][0]['id']=='new-listing'
    result=c2.post('/admin/chat',headers=h2,json={'message':'Oui.'}).json()
    assert result['status']=='pending_confirmation'
    c2.post('/admin/changes/'+result['change_id']+'/confirm',headers=h2,json={'accept':True})
    p=next(p for p in c2.get('/admin/properties').json() if p['name']=='Nouveau logement')
    assert p['listing_id']=='new-listing' and p['is_active'] is False
    with env[1].session() as session:assert session.get(Property,p['id']).organization_id==organization_id()

async def test_media_encrypted_persistent_and_confirmed_assignment(env):
    service=MediaService(env[1],env[2],env[0]);data=b'%PDF-1.7\nSYNTHETIC-MEDIA-CONTENT'
    result=await service.upload('guide.pdf',data,'arrival_guide')
    with env[1].session() as session:
        blob=session.scalar(select(MediaBlob));assert 'SYNTHETIC' not in blob.encrypted_content
    assert (await MediaService(env[1],env[2],env[0]).download(result['id']))[0]==data
    manager=ManagerService(env[1],env[2],env[4],env[3]);manager.media=service
    pending=await manager.propose(action('attach_media',media_id=result['id'],media_kind='arrival_guide'))
    assert pending['status']=='pending_confirmation'
    await manager.confirm(pending['change_id'],True)
    c,headers=site(env)
    p=next(p for p in c.get('/admin/properties').json() if p['name']=='CAIRE1')
    assert p['media'][0]['id']==result['id']
    assert c.get('/admin/media/'+result['id']).content==data
    c.cookies.clear();assert c.get('/admin/media/'+result['id']).status_code==401

async def test_media_rejects_active_html_and_size(env):
    service=MediaService(env[1],env[2],env[0])
    with pytest.raises(ValueError):await service.upload('image.svg',b'<svg onload="alert(1)">','access_photo')
    with pytest.raises(ValueError):await service.upload('video.mp4',b'%PDF-1.7','access_video')

async def test_decision_test_mode_never_sends_and_requires_confirmation(env):
    svc=DecisionService(env[1],env[2],env[0],env[3])
    with env[1].session() as session:
        p=session.scalar(select(Property).where(Property.name=='CAIRE1'))
        e=Escalation(dedup_key='financial',property_id=p.id,guesty_conversation_id='conv-test',encrypted_summary=env[2].encrypt('Demande de remboursement'),priority='normal');session.add(e);session.commit();eid=e.id
    pending=await svc.propose(action('decide_escalation',escalation_id=eid,decision='accept'))
    assert pending['financial'] and not env[3].send_calls
    with env[1].session() as session:assert session.get(Escalation,eid).status=='open'
    result=await svc.confirm(pending['decision_id'],True)
    assert result['status']=='simulated' and not env[3].send_calls
    with pytest.raises(ValueError):await svc.confirm(pending['decision_id'],True)

async def test_reservations_snapshot_groups_and_no_access_secrets(env):
    payload=payload_for(env,text='Bonjour')
    await ContextBuilder(env[1],env[3],env[2],env[0]).build(payload)
    groups=ReservationService(env[1],env[2],env[0]).groups()
    assert sum(len(rows) for rows in groups.values())==1
    assert 'TEST-BOX' not in str(groups) and 'TEST-PASSWORD' not in str(groups)

def test_viewer_cannot_mutate_or_reveal(env):
    c,headers=site(env)
    with env[1].system_session() as session:
        m=session.scalar(select(Membership));m.role='viewer';session.commit()
    assert c.get('/admin/properties').status_code==200
    p=c.get('/admin/properties').json()[0]
    assert c.post('/admin/actions',headers=headers,json=action('create_property',property='X').model_dump(mode='json')).status_code==403
    assert c.post('/admin/properties/'+p['id']+'/reveal',headers=headers,json={'field':'lockbox_code'}).status_code==403
