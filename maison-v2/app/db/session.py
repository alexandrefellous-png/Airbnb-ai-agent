import asyncio
import hashlib
from contextlib import asynccontextmanager
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session, with_loader_criteria
from app.core.tenancy import organization_id
from app.db.models import TENANT_MODELS
from sqlalchemy.pool import StaticPool

class TenantSession(Session):
    def bulk_save_objects(self, *args, **kwargs):
        raise PermissionError("Bulk writes bypass ownership validation")
    def bulk_insert_mappings(self, *args, **kwargs):
        raise PermissionError("Bulk writes bypass ownership validation")
    def bulk_update_mappings(self, *args, **kwargs):
        raise PermissionError("Bulk writes bypass ownership validation")
    def get(self, entity, ident, **kwargs):
        if organization_id()!=self.info["organization_id"]:
            raise PermissionError("Tenant session cannot change organization")
        if entity in TENANT_MODELS:
            tenant = self.info["organization_id"]
            if not isinstance(ident, (tuple, dict)):
                names = [column.name for column in entity.__mapper__.primary_key]
                ident = {name: tenant if name == "organization_id" else ident for name in names}
            elif isinstance(ident, dict) and ident.get("organization_id", tenant) != tenant:
                return None
            elif isinstance(ident, tuple):
                names = [column.name for column in entity.__mapper__.primary_key]
                if ident[names.index("organization_id")] != tenant:
                    return None
        return super().get(entity, ident, **kwargs)

@event.listens_for(TenantSession, "after_begin")
def database_tenant_context(session, transaction, connection):
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT set_config('app.organization_id', :tenant, true)"), {"tenant":session.info["organization_id"]})

@event.listens_for(TenantSession, "do_orm_execute")
def scope_queries(state):
    tenant = state.session.info["organization_id"]
    if organization_id()!=tenant:raise PermissionError("Tenant session cannot change organization")
    if not state.is_orm_statement:
        raise PermissionError("Raw SQL is forbidden in tenant sessions")
    if any(mapper.class_ not in TENANT_MODELS for mapper in state.all_mappers):
        raise PermissionError("Global reads forbidden in tenant sessions")
    if state.is_insert:
        raise PermissionError("Use validated ORM objects for tenant inserts")
    if state.is_select:
        for model in TENANT_MODELS:
            state.statement = state.statement.options(with_loader_criteria(model, model.organization_id == tenant, include_aliases=True))
    elif state.is_update or state.is_delete:
        model = state.bind_mapper.class_ if state.bind_mapper else None
        if model not in TENANT_MODELS:
            raise PermissionError("Global mutations forbidden in tenant sessions")
        state.statement = state.statement.where(model.organization_id == tenant)
        if state.is_update and any(getattr(key, "name", str(key)) == "organization_id" for key in getattr(state.statement, "_values", {})):
            raise PermissionError("Ownership cannot be changed")

@event.listens_for(TenantSession, "before_flush")
def scope_writes(session, _, __):
    tenant = session.info["organization_id"]
    if organization_id()!=tenant:raise PermissionError("Tenant session cannot change organization")
    for row in session.new | session.dirty | session.deleted:
        if type(row) not in TENANT_MODELS:
            raise PermissionError("Global writes forbidden in tenant sessions")
        if row in session.new and hasattr(row,"manager"):
            from app.core.tenancy import manager_identity
            actor=manager_identity()
            row.manager=actor["user_id"] if actor else "system"
        if row in session.new and row.organization_id is None:
            row.organization_id = tenant
        if row.organization_id != tenant:
            raise PermissionError("Cross-organization write forbidden")

class Database:
    def __init__(self, url: str):
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def pragmas(conn, _):
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=WAL")
        self.system_session = sessionmaker(self.engine, expire_on_commit=False)
        self.tenant_factory = sessionmaker(self.engine, class_=TenantSession, expire_on_commit=False)
        self.locks = {}

    def session(self):
        return self.tenant_factory(info={"organization_id": organization_id()})

    @asynccontextmanager
    async def lock(self, name: str):
        # PostgreSQL session advisory lock covers all processes and survives transaction commits.
        name = organization_id() + ":" + name
        key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
        if self.engine.dialect.name == "postgresql":
            conn = self.engine.connect()
            try:
                while not conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar():
                    await asyncio.sleep(0.1)
                yield
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                conn.commit()
                conn.close()
        else:
            # SQLite is local development only: one application process.
            async with self.locks.setdefault(name, asyncio.Lock()):
                yield
