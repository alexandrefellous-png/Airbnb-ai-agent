import json
import base64
import datetime
import pytest
from sqlalchemy import select,delete,update,insert,text
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient
from svix.webhooks import Webhook
from app.core.tenancy import organization_scope,organization_id
from app.db.models import Property,PropertyAccess,GuestyConnection,OAuthToken,StyleProfile,User,ChatMessage,ManagerWorkspace,MediaAsset,Escalation,Membership
from app.services.organization_settings import OrganizationSettings
from app.services.manager import ManagerService,ManagerError
from app.services.media import MediaService
from scripts.create_workspace import provision
from app.main import create_app
from test_api import login
from test_manager import action
from conftest import payload_for

@pytest.fixture
def second(env):
    oid=provision(env[1],env[2],'Other company','other@example.test','other-test-password-12345')
    with organization_scope(oid),env[1].session() as session:
        p=Property(name='CAIRE1',guesty_listing_id='listing-a',address='Other address',timezone='Europe/Paris',check_in='16:00',check_out='10:00')
        session.add(p);session.flush();pid=p.id
        session.add(PropertyAccess(property_id=pid,encrypted_data=env[2].encrypt({'lockbox_code':'OTHER-COMPANY-SECRET'})))
        session.add(ChatMessage(role='user',encrypted_content=env[2].encrypt({'message':'OTHER-PRIVATE-HISTORY'})))
        session.add(ManagerWorkspace(id='onboarding',encrypted_data=env[2].encrypt({'name':'Other draft'})))
        session.commit()
    return oid,pid

def test_missing_context_fails_closed(env):
    with organization_scope('') if False else organization_scope('temporary'):
        pass
    from app.core import tenancy
    token=tenancy._current.set(None)
    try:
        with pytest.raises(PermissionError):env[1].session()
    finally:tenancy._current.reset(token)

def test_queries_get_counts_and_repeated_context_switches(env,second):
    db=env[1];own=organization_id();other,pid=second
    for oid,expected in [(own,2),(other,1),(own,2),(other,1)]:
        with organization_scope(oid),db.session() as session:
            rows=session.scalars(select(Property)).all()
            assert len(rows)==expected and all(p.organization_id==oid for p in rows)
            if oid==own:
                assert session.get(Property,pid) is None
                assert session.get(Property,{'id':pid,'organization_id':other}) is None
                assert session.get(Property,(pid,other)) is None
                assert session.scalar(select(ChatMessage)) is None
                assert session.get(ManagerWorkspace,'onboarding') is None
            else:assert session.get(ManagerWorkspace,'onboarding') is not None

def test_foreign_child_and_forged_ownership_fail(env,second):
    db=env[1];other,pid=second
    with db.session() as session:
        session.add(PropertyAccess(property_id=pid,encrypted_data=env[2].encrypt({'door':'foreign'})))
        with pytest.raises(IntegrityError):session.commit()
    with db.session() as session:
        session.add(Property(name='forged',organization_id=other))
        with pytest.raises(PermissionError):session.commit()
    with db.session() as session:
        prop=session.scalar(select(Property));prop.organization_id=other
        with pytest.raises(PermissionError):session.commit()

def test_bulk_update_delete_scoped_and_raw_insert_blocked(env,second):
    db=env[1];other,pid=second
    with db.session() as session:
        result=session.execute(update(Property).values(address='Own changed'));assert result.rowcount==2
        session.execute(delete(ChatMessage));session.commit()
    with organization_scope(other),db.session() as session:
        assert session.get(Property,pid).address=='Other address'
        assert session.scalar(select(ChatMessage)) is not None
    with db.session() as session:
        with pytest.raises(PermissionError):session.execute(text('SELECT * FROM properties'))
        with pytest.raises(PermissionError):session.execute(insert(Property).values(name='forged',organization_id=other))
        with pytest.raises(PermissionError):session.execute(update(Property).values(organization_id=other))
        with pytest.raises(PermissionError):session.bulk_insert_mappings(Property,[{'name':'forged','organization_id':other}])
        with pytest.raises(PermissionError):session.scalars(select(User)).all()

async def test_credentials_and_token_style_collision_isolated(env,second):
    db,vault=env[1],env[2];own=organization_id();other,_=second
    settings=OrganizationSettings(env[0],db,vault)
    for oid,secret in [(own,'OWN-SECRET'),(other,'OTHER-SECRET')]:
        with organization_scope(oid),db.session() as session:
            session.add(GuestyConnection(encrypted_credentials=vault.encrypt({'guesty_client_id':'same-client-key','guesty_client_secret':secret})))
            session.add(OAuthToken(id='same-token-key',encrypted_token=vault.encrypt(secret),expires_at=10))
            session.add(StyleProfile(id='host',profile=secret));session.commit()
    for oid,secret in [(own,'OWN-SECRET'),(other,'OTHER-SECRET'),(own,'OWN-SECRET')]:
        with organization_scope(oid),db.session() as session:
            assert settings.guesty_client_secret==secret
            assert vault.decrypt(session.get(OAuthToken,'same-token-key').encrypted_token)==secret
            assert session.get(StyleProfile,'host').profile==secret

async def test_foreign_confirmation_and_media_cannot_be_read_or_attached(env,second):
    own=organization_id();other,pid=second;manager=ManagerService(env[1],env[2],env[4],env[3]);media=MediaService(env[1],env[2],env[0])
    with organization_scope(other):
        pending=await manager.propose(action('update_fields',updates=[{'field':'address','value':'Other new'}]))
        asset=await media.upload('other.pdf',b'%PDF-1.7\nOTHER-DOCUMENT','document')
    with pytest.raises(ManagerError):await manager.confirm(pending['change_id'],True)
    with pytest.raises(ValueError):await media.download(asset['id'])
    manager.media=media
    pending=await manager.propose(action('attach_media',media_id=asset['id'],media_kind='document'))
    with pytest.raises(ManagerError):await manager.confirm(pending['change_id'],True)

def test_api_idor_session_role_and_guesty_configuration_isolation(env,second,monkeypatch):
    from app.guesty.client import GuestyClient
    async def verify(self, method, path, **kwargs):
        assert method == "GET" and path == "/listings"
        return {"results": []}
    monkeypatch.setattr(GuestyClient, "request", verify)
    own=organization_id();other,pid=second
    c=TestClient(create_app(env[0],env[1],env[3],env[4]));headers=login(c,env)
    assert c.get('/admin/properties/'+pid).status_code==404
    assert c.post('/admin/properties/'+pid+'/reveal',headers=headers,json={'field':'lockbox_code'}).status_code==404
    assert 'OTHER-PRIVATE-HISTORY' not in c.get('/admin/history').text
    assert 'OTHER-COMPANY-SECRET' not in c.get('/admin/data').text
    assert c.post('/admin/settings/guesty',headers=headers,json={'client_id':'own-id','client_secret':'OWN-API-SECRET'}).status_code==200
    assert 'OWN-API-SECRET' not in c.get('/admin/settings').text
    with organization_scope(other),env[1].session() as session:assert session.scalar(select(GuestyConnection)) is None
    c2=TestClient(c.app)
    assert c2.post('/admin/login',json={'username':'other@example.test','password':'other-test-password-12345','workspace':own}).status_code==401
    assert c2.post('/admin/login',json={'username':'other@example.test','password':'other-test-password-12345','workspace':other}).status_code==200
    assert c2.get('/admin/session').json()['organization_id']==other
    assert len(c2.get('/admin/properties').json())==1
    with env[1].system_session() as session:
        membership=session.scalar(select(Membership).where(Membership.organization_id==own));session.delete(membership);session.commit()
    assert c.get('/admin/properties').status_code==401

def test_cross_workspace_webhook_signature_rejected(env,second):
    other,_=second;own=organization_id();secret='whsec_'+base64.b64encode(b'Own signing key 32 bytes long!!!!').decode()
    with env[1].session() as session:session.add(GuestyConnection(encrypted_credentials=env[2].encrypt({'guesty_webhook_secret':secret})));session.commit()
    other_secret='whsec_'+base64.b64encode(b'Other signing key 32 bytes long!!').decode()
    with organization_scope(other),env[1].session() as session:session.add(GuestyConnection(encrypted_credentials=env[2].encrypt({'guesty_webhook_secret':other_secret})));session.commit()
    payload=json.dumps(payload_for(env));now=datetime.datetime.now(datetime.timezone.utc)
    headers={'svix-id':'unique-delivery','svix-timestamp':str(int(now.timestamp())),'svix-signature':Webhook(secret).sign('unique-delivery',now,payload),'Content-Type':'application/json'}
    c=TestClient(create_app(env[0],env[1],env[3],env[4]))
    assert c.post('/guesty/webhook/'+other,content=payload,headers=headers).status_code==401
    assert c.post('/guesty/webhook/'+own,content=payload,headers=headers).status_code==200
