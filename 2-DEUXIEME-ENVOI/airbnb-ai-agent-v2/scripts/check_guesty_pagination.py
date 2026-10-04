"""Read-only pagination diagnosis; no guest bodies or credentials are printed."""
import asyncio
import httpx
from sqlalchemy import select
from app.core.config import Settings
from app.core.security import Vault
from app.core.tenancy import organization_scope
from app.db.session import Database
from app.db.models import Organization
from app.services.organization_settings import OrganizationSettings
from app.guesty.auth import TokenService
from app.guesty.client import GuestyClient,BASE

async def main():
    base=Settings();db=Database(base.database_url);vault=Vault(base.token_encryption_key);settings=OrganizationSettings(base,db,vault)
    with db.system_session() as session:ids=list(session.scalars(select(Organization.id)))
    async with httpx.AsyncClient(timeout=30) as http:
        for oid in ids:
            with organization_scope(oid):
                if not settings.guesty_client_id:continue
                client=GuestyClient(TokenService(settings,db,vault,http),http,settings)
                rows=await client.conversations(3)
                row=next(r for r in rows['conversations'] if len(r.get('meta',{}).get('reservations',[]))==1)
                rid=row['meta']['reservations'][0]['_id'];token=await client.tokens.get()
                for query in [[('reservationIds',rid)],[('reservationIds[]',rid)]]:
                    response=await http.get(BASE+'/reservations-v3',params=query,headers={'Authorization':'Bearer '+token})
                    if response.status_code!=200:
                        body=response.json();message=body.get('message') or body.get('error') or body.get('errors')
                        print({'parameter':query[0][0],'status':response.status_code,'error':vault.redact(str(message),[token,settings.guesty_client_secret])[:1000]},flush=True)
                    else:
                        data=response.json()
                        print({'parameter':query[0][0],'status':200,'response_type':type(data).__name__,'count':len(data),'keys':list(data[0]) if isinstance(data,list) and data else list(data) if isinstance(data,dict) else []},flush=True)
                return

if __name__=='__main__':asyncio.run(main())
