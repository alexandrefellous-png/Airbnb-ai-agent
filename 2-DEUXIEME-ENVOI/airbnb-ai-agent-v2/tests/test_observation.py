import time
import pytest
from sqlalchemy import select
from app.guesty.client import verified_sender,GuestyClient
from app.db.models import Organization,AIObservation,Event,GuestyConnection
from app.core.tenancy import organization_scope,organization_id
from app.services.observation import ObservationService
from app.main import create_app
from conftest import payload_for

@pytest.mark.parametrize('post,sender',[
    ({'sentBy':'guest'},'guest'),({'sentBy':'host'},'user'),
    ({'from':{'type':'guest'}},'guest'),({'from':{'type':'user'}},'user'),
    ({'from':{'type':'user'},'sentBy':'guest'},'unknown'),
    ({'from':{'type':'guest'},'sentBy':'host'},'unknown'),
    ({'sentBy':'thirdParty'},'unknown'),({'from':{'type':'unknown'},'sentBy':'guest'},'unknown')])
def test_documented_source_representations_and_conflicts(post,sender):
    assert verified_sender(post)==sender


def observation_app(env):
    env[0].openai_api_key='synthetic-not-a-real-key'
    with env[1].session() as session:
        session.add(GuestyConnection(encrypted_credentials=env[2].encrypt({'guesty_client_id':'synthetic','guesty_client_secret':'synthetic'})));session.commit()
    payload=payload_for(env,text='Is there a washing machine?')
    cid=payload['conversation']['_id']
    # FakeGuesty stores conversations as a dictionary; keep it before method replacement.
    rows=env[3].conversations
    async def conversations(limit=25,cursor=None):return {'conversations':[rows[cid]],'cursor':None}
    env[3].conversations=conversations
    # Its conversation() method also needs the retained dictionary.
    async def conversation(value):return rows[value]
    env[3].conversation=conversation
    app=create_app(env[0],env[1],env[3],env[4])
    return app,payload


async def test_real_message_observation_generates_test_preview_and_deduplicates(env):
    app,payload=observation_app(env)
    result=await app.state.observation.sync()
    assert result['state']=='ready' and result['generated']==1 and result['imported']==1
    assert app.state.observation.inbox()[0]['posts'][-1]['sender']=='guest'
    with env[1].session() as session:
        previews=list(session.scalars(select(AIObservation)))
        assert len(previews)==1 and previews[0].effective_test
    app.state.observation.write('observation:status',{'next_sync_at':0})
    result=await app.state.observation.sync()
    assert result['generated']==0 and not env[3].send_calls


async def test_read_only_observation_cannot_send_even_if_workspace_and_server_are_live(env):
    app,payload=observation_app(env)
    env[0].allow_live_sends=True
    with env[1].system_session() as session:
        org=session.get(Organization,organization_id());org.agent_mode='live';session.commit()
    result=await app.state.observation.sync()
    assert result['generated']==1 and not env[3].send_calls
    with env[1].session() as session:assert session.scalar(select(AIObservation)).effective_test


async def test_unknown_author_is_visible_but_never_generates_a_reply(env):
    app,payload=observation_app(env)
    posts=env[3].histories[payload['conversation']['_id']]
    posts[-1]['from']={'type':'unknown'};posts[-1]['sentBy']='thirdParty'
    result=await app.state.observation.sync()
    assert result['state']=='ready' and result['generated']==0
    assert app.state.observation.inbox()


async def test_observation_data_is_tenant_isolated(env):
    app,payload=observation_app(env)
    await app.state.observation.sync()
    with env[1].system_session() as session:
        other=Organization(name='Foreign');session.add(other);session.commit();oid=other.id
    with organization_scope(oid):
        assert app.state.observation.inbox()==[]
        assert not app.state.observation.status()['enabled']
