from alembic import context
from app.core.config import Settings
from app.db.models import Base
from app.db.session import Database
config = context.config
settings = Settings()
url = settings.database_url.replace("postgres://", "postgresql+psycopg://", 1).replace("postgresql://", "postgresql+psycopg://", 1)
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    db = Database(url)
    with db.engine.connect() as conn:
        if conn.dialect.name == "sqlite":
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.commit()
        context.configure(connection=conn, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction(): context.run_migrations()
    db.engine.dispose()
