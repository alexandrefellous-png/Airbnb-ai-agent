"""Migrate the cloud database, refusing a role that bypasses tenant isolation."""
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.core.config import Settings
from app.core.security import Vault
from app.db.session import Database
from app.db.models import TENANT_MODELS


def main():
    settings = Settings()
    if settings.app_env != "production":
        raise RuntimeError("This command requires APP_ENV=production")
    Vault(settings.token_encryption_key)  # Fail before migration if the key is malformed.
    db = Database(settings.database_url)
    try:
        with db.engine.connect() as conn:
            role = conn.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
            if role.rolsuper or role.rolbypassrls:
                raise RuntimeError("Use a PostgreSQL role without SUPERUSER or BYPASSRLS")
        command.upgrade(Config("alembic.ini"), "head")
        with db.engine.connect() as conn:
            tables = dict(conn.execute(text("SELECT relname,relrowsecurity AND relforcerowsecurity FROM pg_class JOIN pg_namespace ON pg_namespace.oid=relnamespace WHERE nspname='public' AND relkind='r'")).all())
            if not all(tables.get(model.__tablename__) for model in TENANT_MODELS):
                raise RuntimeError("Tenant isolation is incomplete; deployment stopped")
        print("Database migrated; forced tenant isolation verified. No messages sent.")
    finally:
        db.engine.dispose()


if __name__ == "__main__":
    main()
