import pytest
import httpx
from sqlalchemy import select
from fastapi.testclient import TestClient
from app.main import create_app
from app.db.models import Organization,Membership,AgentModeChange,AuditLog,SentMessage
from app.core.tenancy import organization_id,organization_scope
from app.services.organization_settings import OrganizationSettings
from app.guesty.client import GuestyClient
from app.guesty.errors import LiveSendsBlocked
from test_api import login
from test_manager import action
from conftest import payload_for
from test_tenancy import second

def client(env):
    env[4].client=None
    c=TestClient(create_app(env[0],env[1],env[3],env[4]));return c,login(c,env)

def test_live_requires_confirmation_audit_and_server_gate(env,second):
    c,headers=client(env)
    result=c.post('/admin/chat',headers=headers,json={'message':'Passe l’agent en production.'}).json()
    assert result['status']=='pending_mode' and not result['live_allowed']
    assert c.get('/admin/session').json()['agent_mode']=='test'
    assert c.post('/admin/modes/'+result['mode_change_id']+'/confirm',headers=headers,json={'accept':True}).status_code==200
    identity=c.get('/admin/session').json()
    assert identity['agent_mode']=='live' and identity['test_mode'] and not identity['live_allowed']
    with env[1].session() as session:assert session.scalar(select(AuditLog).where(AuditLog.source=='manager:agent_mode')) is not None
    with env[1].system_session() as session:assert session.get(Organization,second[0]).agent_mode=='test'
    assert c.post('/admin/modes/'+result['mode_change_id']+'/confirm',headers=headers,json={'accept':True}).status_code==409
    result=c.post('/admin/chat',headers=headers,json={'message':'Remets l’agent en test.'}).json()
    assert result['agent_mode']=='test'

def test_manager_cannot_change_mode_even_via_direct_action(env):
    c,headers=client(env)
    with env[1].system_session() as session:
        member=session.scalar(select(Membership));member.role='manager';session.commit()
    result=c.post('/admin/chat',headers=headers,json={'message':'Active les réponses réelles.'})
    assert result.status_code==403
    result=c.post('/admin/actions',headers=headers,json=action('set_agent_mode',agent_mode='live').model_dump(mode='json'))
    assert result.status_code==403

def test_mode_cancel_and_expired_confirmation(env):
    c,headers=client(env)
    result=c.post('/admin/actions',headers=headers,json=action('set_agent_mode',agent_mode='live').model_dump(mode='json')).json()
    assert c.post('/admin/modes/'+result['mode_change_id']+'/confirm',headers=headers,json={'accept':False}).json()['status']=='rejected'
    assert c.get('/admin/session').json()['agent_mode']=='test'
    result=c.post('/admin/actions',headers=headers,json=action('set_agent_mode',agent_mode='live').model_dump(mode='json')).json()
    with env[1].session() as session:session.get(AgentModeChange,result['mode_change_id']).expires_at=0;session.commit()
    assert c.post('/admin/modes/'+result['mode_change_id']+'/confirm',headers=headers,json={'accept':True}).status_code==409

async def test_server_gate_blocks_guesty_at_last_boundary_without_http(env):
    calls=[]
    class Tokens:
        db=env[1]
        async def get(self,*args,**kwargs):raise AssertionError('No auth/network allowed when sends are blocked')
    settings=OrganizationSettings(env[0],env[1],env[2])
    with env[1].system_session() as session:session.get(Organization,organization_id()).agent_mode='live';session.commit()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:calls.append(req))) as http:
        guesty=GuestyClient(Tokens(),http,settings)
        with pytest.raises(LiveSendsBlocked):await guesty.send('conv','hello',{'type':'airbnb2'})
        with pytest.raises(LiveSendsBlocked):await guesty.request('POST','/communication/conversations/conv/send-message',payload={})
    assert calls==[]

async def test_live_workspace_allowed_and_test_workspace_blocked_mock_only(env,second):
    own=organization_id();other=second[0];env[0].allow_live_sends=True
    with env[1].system_session() as session:session.get(Organization,own).agent_mode='live';session.commit()
    calls=[]
    class Tokens:
        db=env[1]
        async def get(self,*args,**kwargs):return 'MOCK-TOKEN'
    def handler(req):calls.append(req);return httpx.Response(200,json={'data':{'_id':'mock-post','conversationId':'conv'}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        guesty=GuestyClient(Tokens(),http,OrganizationSettings(env[0],env[1],env[2]))
        assert await guesty.send('conv','hello',{'type':'airbnb2'})=='mock-post'
        with organization_scope(other):
            with pytest.raises(LiveSendsBlocked):await guesty.send('conv','hello',{'type':'airbnb2'})
    assert len(calls)==1

async def test_live_but_server_disabled_still_generates_preview(env):
    with env[1].system_session() as session:session.get(Organization,organization_id()).agent_mode='live';session.commit()
    app=create_app(env[0],env[1],env[3],env[4]);app.state.queue.enqueue(payload_for(env));await app.state.processor.tick()
    with env[1].session() as session:
        row=session.scalar(select(SentMessage));assert row.status=='simulated' and row.test_mode
    assert not env[3].send_calls
    c=TestClient(app);headers=login(c,env)
    assert c.get('/admin/previews').json()[0]['label']=='AI WOULD REPLY'
