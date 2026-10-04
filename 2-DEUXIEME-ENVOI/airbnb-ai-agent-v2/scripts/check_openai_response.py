"""One synthetic structured-response check; no guest information is sent."""
import asyncio
from pydantic import BaseModel,ConfigDict
from sqlalchemy import select
from app.core.config import Settings
from app.core.security import Vault
from app.core.tenancy import organization_scope
from app.db.session import Database
from app.db.models import Organization
from app.services.organization_settings import OrganizationSettings
from app.services.openai_service import AIService

class Probe(BaseModel):
    model_config=ConfigDict(extra='forbid')
    connected:bool

async def main():
    base=Settings();db=Database(base.database_url);vault=Vault(base.token_encryption_key);settings=OrganizationSettings(base,db,vault);ai=AIService(settings)
    with db.system_session() as session:ids=list(session.scalars(select(Organization.id)))
    try:
        for oid in ids:
            with organization_scope(oid):
                if not settings.openai_api_key:continue
                try:
                    result=await ai.parse(Probe,'Return connected=true. This is a synthetic connection check.',{'connection_check':True})
                    print({'openai_responses_verified':result.connected,'model':settings.openai_model})
                except Exception as exc:
                    print({'openai_responses_verified':False,'error_class':type(exc).__name__,'status':getattr(exc,'status_code',None),'code':getattr(exc,'code',None)})
    finally:await ai.close();db.engine.dispose()

if __name__=='__main__':asyncio.run(main())
