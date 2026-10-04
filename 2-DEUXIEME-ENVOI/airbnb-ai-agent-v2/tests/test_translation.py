import pytest
from sqlalchemy import select
from app.services.translation import TranslationService,FrenchBatch,FrenchText
from app.db.models import Organization,ManagerWorkspace
from app.core.tenancy import organization_scope
from test_site import site

async def test_translation_cached_encrypted_and_tenant_isolated(env):
    calls=[]
    async def parse(schema,instructions,data):
        calls.append(data)
        return FrenchBatch(translations=[FrenchText(text='Pouvons-nous arriver à 13h ?') for _ in data['messages']])
    env[4].parse=parse
    service=TranslationService(env[1],env[2],env[4])
    source=['Can we arrive at 1pm?']
    assert await service.translate(source)==['Pouvons-nous arriver à 13h ?']
    await service.translate(source)
    assert len(calls)==1 and source==['Can we arrive at 1pm?']
    with env[1].session() as session:
        row=session.scalar(select(ManagerWorkspace).where(ManagerWorkspace.id.like('tr:%')))
        assert 'Pouvons' not in row.encrypted_data
    with env[1].system_session() as session:
        org=Organization(name='Other');session.add(org);session.commit();oid=org.id
    with organization_scope(oid):await service.translate(source)
    assert len(calls)==2 and not env[3].send_calls

async def test_incomplete_translation_is_not_cached(env):
    async def parse(*args):return FrenchBatch(translations=[])
    env[4].parse=parse
    with pytest.raises(ValueError):await TranslationService(env[1],env[2],env[4]).translate(['Hello'])
    with env[1].session() as session:assert not session.scalar(select(ManagerWorkspace).where(ManagerWorkspace.id.like('tr:%')))

def test_translation_auth_bounds_and_failure_preserve_original(env):
    client,headers=site(env)
    assert client.post('/admin/translations',json={'texts':['Hello']}).status_code==403
    assert client.post('/admin/translations',headers=headers,json={'texts':['x'*12001]}).status_code==422
    assert client.post('/admin/translations',headers=headers,json={'texts':['Hello']}).status_code==503
    assert not env[3].send_calls
