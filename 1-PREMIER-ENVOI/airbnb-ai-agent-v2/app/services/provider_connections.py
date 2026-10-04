"""Encrypted, tenant-scoped provider configuration with read-only verification."""
import time
from sqlalchemy import select
from app.db.models import ManagerWorkspace, GuestyConnection, AuditLog
from app.guesty.auth import TokenService
from app.guesty.client import GuestyClient
from app.guesty.errors import GuestyError, ContractError
from openai import AsyncOpenAI
import httpx

class CandidateSettings:
    def __init__(self, base, values):
        self.base, self.values = base, values
    def __getattr__(self, name):
        return self.values[name] if name in self.values else getattr(self.base, name)

class ProviderConnections:
    def __init__(self, db, vault, settings):
        self.db, self.vault, self.settings = db, vault, settings

    def save_metadata(self, session, provider, data):
        row = session.get(ManagerWorkspace, 'provider:' + provider)
        encrypted = self.vault.encrypt(data)
        if row:
            row.encrypted_data, row.updated_at = encrypted, time.time()
        else:
            session.add(ManagerWorkspace(id='provider:' + provider, encrypted_data=encrypted))

    async def guesty(self, credentials):
        async with self.db.lock('guesty_configuration'):
            candidate = CandidateSettings(self.settings, credentials)
            async with httpx.AsyncClient(timeout=25) as http:
                client = GuestyClient(TokenService(candidate, self.db, self.vault, http), http, candidate)
                result = await client.request('GET', '/listings', params={'limit':1})
                if not isinstance(result, dict) or not isinstance(result.get('results'), list):
                    raise ContractError('listings_expected_results')
            with self.db.session() as session:
                row = session.scalar(select(GuestyConnection).where(GuestyConnection.enabled.is_(True)))
                encrypted = self.vault.encrypt(credentials)
                if row:
                    row.encrypted_credentials = encrypted
                else:
                    session.add(GuestyConnection(encrypted_credentials=encrypted))
                self.save_metadata(session, 'guesty', {'verified_at':time.time()})
                session.add(AuditLog(source='settings:guesty', encrypted_change=self.vault.encrypt({'connection_verified':True})))
                session.commit()

    async def openai(self, api_key):
        async with self.db.lock('openai_configuration'):
            async with AsyncOpenAI(api_key=api_key, timeout=25, max_retries=0) as client:
                await client.models.retrieve(self.settings.openai_model)
            with self.db.session() as session:
                self.save_metadata(session, 'openai', {'api_key':api_key, 'verified_at':time.time()})
                session.add(AuditLog(source='settings:openai', encrypted_change=self.vault.encrypt({'connection_verified':True})))
                session.commit()
