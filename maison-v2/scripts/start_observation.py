"""Enable local read-only observation for connected workspaces; no message sends."""
import asyncio
from sqlalchemy import select
from app.core.config import Settings
from app.core.tenancy import organization_scope
from app.db.models import Organization,AIObservation
from app.main import create_app

async def main():
    settings=Settings(worker_enabled=False)
    if settings.allow_live_sends:raise RuntimeError('This local setup requires ALLOW_LIVE_SENDS=false')
    app=create_app(settings)
    async with app.router.lifespan_context(app):
        with app.state.db.system_session() as session:ids=list(session.scalars(select(Organization.id).where(Organization.status=='active')))
        for oid in ids:
            with organization_scope(oid):
                if not app.state.settings.guesty_client_id:continue
                if app.state.settings.agent_mode!='test':raise RuntimeError('Keep workspace TEST mode')
                app.state.observation.configure(True)
                print('Reading Guesty inbox in TEST...',flush=True)
                result=await app.state.observation.sync()
                print({key:result.get(key) for key in ['state','imported','generated','error','enabled']},flush=True)
                with app.state.db.session() as session:
                    rows=list(session.scalars(select(AIObservation)))
                    reasons=[app.state.vault.decrypt(row.encrypted_context).get('resolution_reason') for row in rows]
                    print({'stored_test_previews':len(rows),'resolution_reasons':reasons},flush=True)

if __name__=='__main__':asyncio.run(main())
