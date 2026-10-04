"""Organization isolation and persistent manager site. Frozen schema snapshot."""
from alembic import op, context
import sqlalchemy as sa
from migrations.schema_organization_v1 import Base
revision = "d448551f37ec"
down_revision = "a703e913333a"
branch_labels = depends_on = None
LEGACY_ORGANIZATION = "81904942-6469-4f84-bba9-1a3692f71f5c"
LEGACY_TABLES = {"properties", "property_access", "property_wifi", "property_states", "guesty_id_mappings", "oauth_tokens", "incoming_events", "agent_sent_messages", "escalations", "manager_changes", "audit_logs", "style_profiles"}
CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s", "pk": "pk_%(table_name)s", "uq": "uq_%(table_name)s_%(column_0_name)s"}

def upgrade():
    if context.is_offline_mode():
        raise RuntimeError("This data-preserving migration requires an online database connection")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    originals = {name: sa.Table(name, sa.MetaData(naming_convention=CONVENTION), autoload_with=bind) for name in LEGACY_TABLES}
    # Remove old foreign keys first; composite ownership keys will replace them.
    for name, table in originals.items():
        with op.batch_alter_table(name, naming_convention=CONVENTION) as batch:
            for fk in table.foreign_key_constraints:
                batch.drop_constraint(fk.name, type_="foreignkey")
    for name in ["organizations", "users", "organization_memberships"]:
        Base.metadata.tables[name].create(bind)
    bind.execute(Base.metadata.tables["organizations"].insert().values(id=LEGACY_ORGANIZATION, name="Mon workspace", status="active", settings={}))
    for name in LEGACY_TABLES:
        current = sa.Table(name, sa.MetaData(naming_convention=CONVENTION), autoload_with=bind)
        target = Base.metadata.tables[name]
        with op.batch_alter_table(name, naming_convention=CONVENTION) as batch:
            batch.drop_constraint(current.primary_key.name, type_="primary")
            for unique in list(current.constraints):
                if isinstance(unique, sa.UniqueConstraint):
                    batch.drop_constraint(unique.name, type_="unique")
            batch.add_column(sa.Column("organization_id", sa.String(36), nullable=False, server_default=LEGACY_ORGANIZATION))
            if name == "properties":
                batch.add_column(sa.Column("guesty_name", sa.String(250), nullable=False, server_default=""))
                batch.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
                batch.add_column(sa.Column("archived_at", sa.Float(), nullable=True))
            batch.create_primary_key("pk_"+name, [column.name for column in target.primary_key.columns])
            for index, unique in enumerate(c for c in target.constraints if isinstance(c, sa.UniqueConstraint)):
                batch.create_unique_constraint("uq_"+name+"_tenant_"+str(index), [column.name for column in unique.columns])
    # Default was migration-only: future writes must supply an organization explicitly.
    for name in LEGACY_TABLES:
        target = Base.metadata.tables[name]
        with op.batch_alter_table(name, naming_convention=CONVENTION) as batch:
            batch.alter_column("organization_id", existing_type=sa.String(36), server_default=None)
            for index, fk in enumerate(target.foreign_key_constraints):
                batch.create_foreign_key("fk_"+name+"_tenant_"+str(index), fk.referred_table.name,
                    [e.parent.name for e in fk.elements], [e.column.name for e in fk.elements])
    for table in Base.metadata.sorted_tables:
        if table.name not in LEGACY_TABLES | {"organizations", "users", "organization_memberships"}:
            table.create(bind)
    if bind.dialect.name == "postgresql":
        for table in Base.metadata.sorted_tables:
            if "organization_id" not in table.c or table.name in {"organization_memberships","admin_sessions"}: continue
            name=table.name
            op.execute(sa.text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY'))
            op.execute(sa.text(f"CREATE POLICY organization_isolation ON \"{name}\" USING (organization_id = current_setting('app.organization_id', true)) WITH CHECK (organization_id = current_setting('app.organization_id', true))"))
    if bind.dialect.name == "sqlite":
        failures = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if failures:
            raise RuntimeError("Foreign key validation failed")

def downgrade():
    raise RuntimeError("Restore a reviewed database backup: dropping tenant ownership would destroy isolation")
