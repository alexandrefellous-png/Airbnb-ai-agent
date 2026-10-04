import asyncio
import logging
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy import select
from app.core.tenancy import organization_scope
from app.db.models import Organization
from app.services.session_auth import SessionService, COOKIE
from app.services.organization_settings import OrganizationSettings
from app.services.properties import PropertyService
from app.services.reservations import ReservationService
from app.services.media import MediaService
from app.services.decisions import DecisionService
from app.services.chat_history import ChatHistory
from app.services.agent_mode import AgentModeService
from app.services.training import TrainingService
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.webhooks import router as webhook_router
from app.core.config import Settings
from app.core.security import Vault
from app.db.session import Database
from app.guesty.auth import TokenService
from app.guesty.client import GuestyClient
from app.services.calendar import CalendarService
from app.services.context import ContextBuilder
from app.services.escalation import EscalationService, NotificationService
from app.services.guest_agent import GuestAgent
from app.services.manager import ManagerService
from app.services.openai_service import AIService
from app.services.processor import Processor
from app.services.queue import EventQueue
from app.services.style import StyleService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

async def work(app):
    while True:
        try:
            with app.state.db.system_session() as session:
                ids = list(session.scalars(select(Organization.id).where(Organization.status == "active")))
            for oid in ids:
                with organization_scope(oid):
                    try: await app.state.processor.tick()
                    except Exception as exc: logging.getLogger("agent.worker").error("TENANT WORKER ERROR class=%s", type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.getLogger("agent.worker").error("WORKER ERROR class=%s", type(exc).__name__)
        await asyncio.sleep(1)

async def notify_work(app):
    while True:
        try:
            with app.state.db.system_session() as session:
                ids = list(session.scalars(select(Organization.id).where(Organization.status == "active")))
            for oid in ids:
                with organization_scope(oid):
                    await app.state.notifications.tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.getLogger("agent.worker").error("NOTIFIER ERROR class=%s", type(exc).__name__)
        await asyncio.sleep(5)

async def observe_work(app):
    while True:
        try:
            with app.state.db.system_session() as session:
                ids=list(session.scalars(select(Organization.id).where(Organization.status=="active")))
            for oid in ids:
                with organization_scope(oid):
                    try: await app.state.observation.tick()
                    except Exception as exc: logging.getLogger("agent.observation").error("TENANT OBSERVER ERROR class=%s",type(exc).__name__)
        except asyncio.CancelledError:raise
        except Exception as exc:
            logging.getLogger("agent.observation").error("OBSERVER ERROR class=%s",type(exc).__name__)
        await asyncio.sleep(10)

async def sync_listings_work(app):
    while True:
        try:
            with app.state.db.system_session() as session:
                ids=list(session.scalars(select(Organization.id).where(Organization.status=='active')))
            for oid in ids:
                with organization_scope(oid):
                    try:await app.state.listing_sync.sync(enrich=True)
                    except Exception as exc:logging.getLogger('agent.sync').error('LISTING SYNC ERROR class=%s',type(exc).__name__)
        except asyncio.CancelledError:raise
        except Exception as exc:logging.getLogger('agent.sync').error('SYNC WORKER ERROR class=%s',type(exc).__name__)
        await asyncio.sleep(15)

def create_app(settings=None, db=None, guesty=None, ai=None):
    settings = settings or Settings()
    db = db or Database(settings.database_url)
    vault = Vault(settings.token_encryption_key)
    base_settings = settings
    settings = OrganizationSettings(settings, db, vault)
    http = httpx.AsyncClient(timeout=25)
    guesty = guesty or GuestyClient(TokenService(settings, db, vault, http), http, settings)
    ai = ai or AIService(settings)

    @asynccontextmanager
    async def lifespan(app):
        if base_settings.app_env == "production":
            from sqlalchemy import text
            with db.system_session() as session:
                role=session.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
                if role.rolsuper or role.rolbypassrls:
                    raise RuntimeError("Production requires a PostgreSQL role without SUPERUSER or BYPASSRLS")
        tasks = [asyncio.create_task(work(app)), asyncio.create_task(notify_work(app)), asyncio.create_task(observe_work(app)),asyncio.create_task(sync_listings_work(app))] if settings.worker_enabled else []
        app.state.worker_tasks = tasks
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            try: await task
            except asyncio.CancelledError: pass
        await http.aclose()
        if hasattr(ai, "close"):
            await ai.close()
        db.engine.dispose()

    app = FastAPI(title="Airbnb AI Employee V2", lifespan=lifespan, docs_url=None if settings.app_env == "production" else "/docs")
    app.state.settings, app.state.db, app.state.vault = settings, db, vault
    app.state.guesty, app.state.ai = guesty, ai
    app.state.sessions = SessionService(db, vault, base_settings)
    app.state.properties = PropertyService(db, vault)
    app.state.reservations = ReservationService(db, vault, settings)
    app.state.media = MediaService(db, vault, settings)
    app.state.decisions = DecisionService(db, vault, settings, guesty)
    app.state.history = ChatHistory(db, vault)
    app.state.manager = ManagerService(db, vault, ai, guesty)
    from app.services.listing_sync import ListingSyncService
    app.state.listing_sync=ListingSyncService(db,vault,guesty,settings,ai)
    app.state.manager.listing_sync=app.state.listing_sync
    app.state.manager.media = app.state.media
    app.state.manager.reservations = app.state.reservations
    app.state.manager.decisions = app.state.decisions
    app.state.modes = AgentModeService(db,vault,settings)
    app.state.manager.modes = app.state.modes
    app.state.training = TrainingService(db,vault,ai)
    app.state.manager.training = app.state.training
    app.state.queue = EventQueue(db, vault, settings)
    app.state.escalations = EscalationService(db, vault)
    app.state.style = StyleService(db, vault, ai)
    from app.services.google_drive import DriveService
    app.state.drive = DriveService(db, vault, settings)
    from app.services.property_media import PropertyMediaService
    app.state.property_media = PropertyMediaService(db, vault, app.state.media, app.state.drive)
    app.state.notifications = NotificationService(db, settings, http)
    app.state.builder = ContextBuilder(db, guesty, vault, settings)
    app.state.processor = Processor(db, vault, settings, guesty, app.state.builder,
        CalendarService(guesty), GuestAgent(ai), app.state.escalations)
    app.state.processor.training = app.state.training
    from app.services.observation import ObservationService
    app.state.observation = ObservationService(db,vault,settings,guesty,app.state.queue,app.state.processor)
    @app.middleware("http")
    async def secure_headers(request, call_next):
        identity = app.state.sessions.verify(request.cookies.get(COOKIE)) if request.url.path.startswith("/admin") and request.url.path != "/admin/login" else None
        oid = identity["organization_id"] if identity else None
        if request.url.path.startswith("/guesty/webhook/"):
            candidate = request.url.path.rsplit("/",1)[-1]
            with db.system_session() as session:
                org = session.get(Organization, candidate)
                oid = org.id if org and org.status == "active" else None
        request.state.identity = identity
        if oid:
            with organization_scope(oid,identity):
                response = await call_next(request)
        else:
            response = await call_next(request)
        if request.url.path.startswith("/admin"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.include_router(auth_router)
    app.include_router(admin_router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent/"static"), name="static")
    app.include_router(webhook_router)

    @app.get("/health")
    def health():
        from sqlalchemy import text
        if base_settings.worker_enabled and any(task.done() for task in getattr(app.state, "worker_tasks", [])):
            raise HTTPException(503, "Agent worker unavailable")
        with db.system_session() as session:
            session.execute(text("SELECT 1"))
        return {"ok": True, "allow_live_sends": base_settings.allow_live_sends}

    return app

app = create_app()
