import base64
import datetime
import json
from fastapi.testclient import TestClient
from svix.webhooks import Webhook
from app.main import create_app
from app.core.config import Settings
from app.core.tenancy import organization_id
from app.db.models import GuestyConnection
from conftest import payload_for

def client(env):return TestClient(create_app(env[0],env[1],env[3],env[4]))
def login(c,env):
    assert c.post('/admin/login',json={'username':'admin','password':env[0].admin_secret}).status_code==200
    return {'X-CSRF-Token':c.get('/admin/session').json()['csrf']}

def test_admin_protected(env):
    c=client(env)
    assert c.get('/admin',follow_redirects=False).status_code==303
    assert c.get('/admin/data').status_code==401
    login(c,env)
    assert c.get('/admin').status_code==200
    assert c.get('/admin/data').status_code==200

def test_unsigned_webhook_refused(env):
    assert client(env).post('/guesty/webhook',json=payload_for(env)).status_code==401

def test_svix_signature_valid_and_invalid(env):
    secret='whsec_'+base64.b64encode(b'TEST-signing-key-32-characters!!!').decode()
    with env[1].session() as session:
        session.add(GuestyConnection(encrypted_credentials=env[2].encrypt({'guesty_webhook_secret':secret})));session.commit()
    payload=json.dumps(payload_for(env));now=datetime.datetime.now(datetime.timezone.utc)
    signature=Webhook(secret).sign('delivery',now,payload)
    headers={'svix-id':'delivery','svix-timestamp':str(int(now.timestamp())),'svix-signature':signature,'Content-Type':'application/json'}
    c=client(env);path='/guesty/webhook/'+organization_id()
    assert c.post(path,content=payload,headers=headers).status_code==200
    headers['svix-signature']='v1,invalid'
    assert c.post(path,content=payload,headers=headers).status_code==401

def test_manager_cross_origin_rejected(env):
    c=client(env);headers=login(c,env)
    assert c.post('/admin/chat',json={'message':'test'},headers={**headers,'Origin':'https://attacker.example'}).status_code==403

def test_guest_cannot_modify_configuration(env):
    assert client(env).post('/admin/chat',json={'message':'change code'}).status_code==401

def test_authenticated_simulation(env):
    c=client(env);headers=login(c,env)
    response=c.post('/admin/simulate',json=payload_for(env),headers=headers)
    assert response.status_code==200 and response.json()['status']=='pending'

def test_production_requires_postgres():
    import pytest
    with pytest.raises(ValueError):Settings(_env_file=None,app_env='production')

def test_local_real_sends_prohibited():
    import pytest
    with pytest.raises(ValueError):Settings(_env_file=None,test_mode=False)
