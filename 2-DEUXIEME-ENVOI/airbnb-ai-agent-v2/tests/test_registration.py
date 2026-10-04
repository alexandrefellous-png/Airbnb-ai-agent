from sqlalchemy import select,func
from fastapi.testclient import TestClient
from app.main import create_app
from app.db.models import Organization,User,Membership,Property
from app.core.passwords import verify_password
from app.core.tenancy import organization_scope
from test_site import site

BODY={'email':'new-owner@example.com','password':'my-own-secure-password-123','workspace_name':'Nouvelle conciergerie'}

def test_registration_creates_owner_session_and_isolated_empty_test_workspace(env):
    c=TestClient(create_app(env[0],env[1],env[3],env[4]))
    result=c.post('/admin/register',json=BODY)
    assert result.status_code==201
    assert BODY['password'] not in result.text and 'HttpOnly' in result.headers['set-cookie']
    identity=c.get('/admin/session').json()
    assert identity['role']=='owner' and identity['agent_mode']=='test'
    assert c.get('/admin/properties').json()==[]
    assert c.get('/admin/settings').json()['guesty_connected'] is False
    with env[1].system_session() as session:
        user=session.scalar(select(User).where(User.email==BODY['email']))
        assert user.password_hash!=BODY['password'] and verify_password(BODY['password'],user.password_hash)
        membership=session.get(Membership,(identity['organization_id'],user.id))
        assert membership.role=='owner'
    assert c.post('/admin/login',json={'username':BODY['email'],'password':BODY['password']}).status_code==200


def test_duplicate_account_and_weak_or_invalid_signup_do_not_create_workspaces(env):
    c=TestClient(create_app(env[0],env[1],env[3],env[4]))
    assert c.post('/admin/register',json=BODY).status_code==201
    with env[1].system_session() as session:before=session.scalar(select(func.count()).select_from(Organization))
    assert c.post('/admin/register',json=BODY).status_code==409
    assert c.post('/admin/register',json={**BODY,'email':'invalid'}).status_code==422
    assert c.post('/admin/register',json={**BODY,'password':'short'}).status_code==422
    with env[1].system_session() as session:assert session.scalar(select(func.count()).select_from(Organization))==before


def test_signup_rejects_cross_origin_and_limits_account_creation(env):
    c=TestClient(create_app(env[0],env[1],env[3],env[4]))
    assert c.post('/admin/register',headers={'Origin':'https://foreign.example'},json=BODY).status_code==403
    for i in range(5):assert c.post('/admin/register',json={**BODY,'email':f'owner{i}@example.com'}).status_code==201
    assert c.post('/admin/register',json={**BODY,'email':'owner6@example.com'}).status_code==429


def test_login_exposes_registration_link_and_registration_page(env):
    c=TestClient(create_app(env[0],env[1],env[3],env[4]))
    assert '/admin/register' in c.get('/admin/login').text
    assert 'register-form' in c.get('/admin/register').text
