from sqlalchemy import select
from app.core.tenancy import organization_id
from app.db.models import GuestyConnection, Organization, ManagerWorkspace

class OrganizationSettings:
    """Resolve organization-specific integrations at use time, without shared credential caches."""
    CREDENTIALS = {"guesty_client_id", "guesty_client_secret", "guesty_webhook_secret"}
    ORGANIZATION_OPTIONS = {"openai_model", "access_before_hours", "access_after_hours", "manager_notification_webhook_url", "observation_enabled"}
    def __init__(self, base, db, vault):
        self.base, self.db, self.vault = base, db, vault
    @property
    def agent_mode(self):
        with self.db.system_session() as session:
            org=session.get(Organization,organization_id())
            return org.agent_mode if org and org.agent_mode in {"test","live"} else "test"
    @property
    def test_mode(self):
        return self.agent_mode != "live" or not self.base.allow_live_sends

    def __getattr__(self, name):
        if name == "openai_api_key":
            with self.db.session() as session:
                row = session.get(ManagerWorkspace, "provider:openai")
                return self.vault.decrypt(row.encrypted_data).get("api_key", "") if row else self.base.openai_api_key
        if name in self.CREDENTIALS:
            with self.db.session() as session:
                row = session.scalar(select(GuestyConnection).where(GuestyConnection.enabled.is_(True)))
                return self.vault.decrypt(row.encrypted_credentials).get(name, "") if row else ""
        if name in self.ORGANIZATION_OPTIONS:
            with self.db.system_session() as session:
                org = session.get(Organization, organization_id())
                value = org.settings.get(name) if org else None
                return value if value is not None else getattr(self.base, name)
        return getattr(self.base, name)
