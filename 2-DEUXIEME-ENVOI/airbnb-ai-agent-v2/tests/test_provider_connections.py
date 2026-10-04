import pytest
from sqlalchemy import select
from app.db.models import GuestyConnection, ManagerWorkspace
from app.guesty.client import GuestyClient
from app.guesty.errors import GuestyError
from app.services.provider_connections import ProviderConnections
from app.services.organization_settings import OrganizationSettings
from app.services.openai_service import AIService
from test_site import site


def test_connect_guesty_verifies_before_replacing_credentials(env, monkeypatch):
    c, headers = site(env)
    async def good(self, method, path, **kwargs):
        assert method == 'GET' and path == '/listings'
        return {'results': []}
    monkeypatch.setattr(GuestyClient, 'request', good)
    assert c.post('/admin/settings/guesty', headers=headers, json={'client_id':'synthetic-new','client_secret':'SYNTHETIC-SECRET'}).status_code == 200
    async def bad(self, *args, **kwargs):
        raise GuestyError('oauth', 401)
    monkeypatch.setattr(GuestyClient, 'request', bad)
    assert c.post('/admin/settings/guesty', headers=headers, json={'client_id':'invalid','client_secret':'INVALID'}).status_code == 422
    s = OrganizationSettings(env[0], env[1], env[2])
    assert s.guesty_client_id == 'synthetic-new'
    assert 'SYNTHETIC-SECRET' not in c.get('/admin/settings').text


def test_openai_connection_encrypted_and_applied_without_restart(env, monkeypatch):
    class Client:
        def __init__(self, **kwargs): self.models = self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def retrieve(self, model): return {'id': model}
    monkeypatch.setattr('app.services.provider_connections.AsyncOpenAI', Client)
    c, headers = site(env)
    s = OrganizationSettings(env[0], env[1], env[2])
    ai = AIService(s)
    assert ai.client is None
    secret = 'SYNTHETIC-OPENAI-KEY-FOR-TESTS'
    response = c.post('/admin/settings/openai', headers=headers, json={'api_key':secret})
    assert response.status_code == 200 and secret not in response.text
    assert s.openai_api_key == secret and ai.client is not None
    assert c.get('/admin/settings').json()['openai_connected'] is True
    with env[1].session() as session:
        assert secret not in session.get(ManagerWorkspace, 'provider:openai').encrypted_data


def test_failed_openai_validation_preserves_previous_configuration(env, monkeypatch):
    from openai import APIConnectionError
    import httpx
    class Client:
        def __init__(self, **kwargs): self.models = self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def retrieve(self, model):
            raise APIConnectionError(request=httpx.Request('GET', 'https://api.openai.com/v1/models/test'))
    monkeypatch.setattr('app.services.provider_connections.AsyncOpenAI', Client)
    with env[1].session() as session:
        session.add(ManagerWorkspace(id='provider:openai', encrypted_data=env[2].encrypt({'api_key':'SYNTHETIC-OLD-OPENAI-KEY'})))
        session.commit()
    c, headers = site(env)
    assert c.post('/admin/settings/openai', headers=headers, json={'api_key':'SYNTHETIC-INVALID-OPENAI-KEY'}).status_code == 422
    assert OrganizationSettings(env[0], env[1], env[2]).openai_api_key == 'SYNTHETIC-OLD-OPENAI-KEY'
