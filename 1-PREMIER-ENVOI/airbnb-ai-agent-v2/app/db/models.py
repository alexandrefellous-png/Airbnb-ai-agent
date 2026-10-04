import time
import uuid
from sqlalchemy import Boolean, Float, ForeignKey, ForeignKeyConstraint, CheckConstraint, Integer, JSON, String, Text, UniqueConstraint, Column
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def uid():
    return str(uuid.uuid4())

class Base(DeclarativeBase):
    pass

class TenantOwned:
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), primary_key=True)

class Property(TenantOwned, Base):
    __tablename__ = "properties"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    guesty_listing_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    address: Mapped[str] = mapped_column(Text, default="")
    timezone: Mapped[str] = mapped_column(String(80), default="")
    check_in: Mapped[str] = mapped_column(String(5), default="")
    check_out: Mapped[str] = mapped_column(String(5), default="")
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    onboarding_step: Mapped[str] = mapped_column(String(40), default="identity")
    guesty_name: Mapped[str] = mapped_column(String(250), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    archived_at: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="active")
    activated_at: Mapped[float | None] = mapped_column(Float)
    deactivated_at: Mapped[float | None] = mapped_column(Float)
    is_billable: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarding_data: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class PropertyAccess(TenantOwned, Base):
    __tablename__ = "property_access"
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), primary_key=True)
    encrypted_data: Mapped[str] = mapped_column(Text)

class PropertyWifi(TenantOwned, Base):
    __tablename__ = "property_wifi"
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), primary_key=True)
    encrypted_data: Mapped[str] = mapped_column(Text)

class PropertyState(TenantOwned, Base):
    __tablename__ = "property_states"
    __table_args__ = (UniqueConstraint("property_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"))
    key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text, default="")
    valid_from: Mapped[float] = mapped_column(Float, default=time.time)
    valid_until: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)

class GuestyMapping(TenantOwned, Base):
    __tablename__ = "guesty_id_mappings"
    __table_args__ = (UniqueConstraint("kind", "external_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"))
    kind: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(120))

class OAuthToken(TenantOwned, Base):
    __tablename__ = "oauth_tokens"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    encrypted_token: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[float] = mapped_column(Float, default=0)
    retry_at: Mapped[float] = mapped_column(Float, default=0)

class Event(TenantOwned, Base):
    __tablename__ = "incoming_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_key: Mapped[str] = mapped_column(String(64), unique=True)
    conversation_id: Mapped[str | None] = mapped_column(String(120), index=True)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    received_at: Mapped[float] = mapped_column(Float, default=time.time)
    due_at: Mapped[float] = mapped_column(Float, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(160), default="")

class SentMessage(TenantOwned, Base):
    __tablename__ = "agent_sent_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    batch_key: Mapped[str] = mapped_column(String(64), unique=True)
    conversation_id: Mapped[str] = mapped_column(String(120), index=True)
    guesty_message_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    body_hash: Mapped[str] = mapped_column(String(64))
    encrypted_body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="draft")
    test_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class Escalation(TenantOwned, Base):
    __tablename__ = "escalations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    dedup_key: Mapped[str] = mapped_column(String(160), unique=True)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"))
    guesty_conversation_id: Mapped[str | None] = mapped_column(String(120))
    reservation_id: Mapped[str | None] = mapped_column(String(120))
    guest_name: Mapped[str] = mapped_column(String(160), default="")
    encrypted_summary: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    status: Mapped[str] = mapped_column(String(20), default="open")
    notification_status: Mapped[str] = mapped_column(String(20), default="pending")
    notification_attempts: Mapped[int] = mapped_column(Integer, default=0)
    notification_due: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    resolved_at: Mapped[float | None] = mapped_column(Float)

class ManagerChange(TenantOwned, Base):
    __tablename__ = "manager_changes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"))
    manager: Mapped[str] = mapped_column(String(80), default="admin")
    encrypted_action: Mapped[str] = mapped_column(Text)
    encrypted_old: Mapped[str] = mapped_column(Text)
    encrypted_new: Mapped[str] = mapped_column(Text)
    expected_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float)

class AuditLog(TenantOwned, Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"))
    manager: Mapped[str] = mapped_column(String(80), default="admin")
    source: Mapped[str] = mapped_column(String(80))
    encrypted_change: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class StyleProfile(TenantOwned, Base):
    __tablename__ = "style_profiles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default="host")
    profile: Mapped[str] = mapped_column(Text)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    secret_version: Mapped[str] = mapped_column(String(64))
    encrypted_csrf: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class LoginLimit(Base):
    __tablename__ = "login_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[float] = mapped_column(Float, default=time.time)
    blocked_until: Mapped[float] = mapped_column(Float, default=0)

class ChatMessage(TenantOwned, Base):
    __tablename__ = "manager_chat_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    role: Mapped[str] = mapped_column(String(20))
    encrypted_content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)

class ManagerWorkspace(TenantOwned, Base):
    __tablename__ = "manager_workspace"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    encrypted_data: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)

class ReservationSnapshot(TenantOwned, Base):
    __tablename__ = "reservation_snapshots"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(120), index=True)
    check_in: Mapped[str | None] = mapped_column(String(40))
    check_out: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    encrypted_context: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)

class EscalationDecision(TenantOwned, Base):
    __tablename__ = "escalation_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    escalation_id: Mapped[str] = mapped_column(ForeignKey("escalations.id"), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    encrypted_response: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    test_mode: Mapped[bool] = mapped_column(Boolean)
    manager: Mapped[str] = mapped_column(String(80), default="admin")
    expires_at: Mapped[float] = mapped_column(Float)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("agent_sent_messages.id"))

class MediaAsset(TenantOwned, Base):
    __tablename__ = "media_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    encrypted_name: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    storage: Mapped[str] = mapped_column(String(20))
    object_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="unassigned")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class MediaBlob(TenantOwned, Base):
    __tablename__ = "media_blobs"
    object_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    encrypted_content: Mapped[str] = mapped_column(Text)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="active")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    agent_mode: Mapped[str] = mapped_column(String(10), default="test")
    mode_revision: Mapped[int] = mapped_column(Integer, default=1)
    subscription_reference: Mapped[str | None] = mapped_column(String(160))
    property_quota: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(250), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class Membership(Base):
    __tablename__ = "organization_memberships"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")

class GuestyConnection(TenantOwned, Base):
    __tablename__ = "guesty_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    encrypted_credentials: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class AgentModeChange(TenantOwned, Base):
    __tablename__ = "agent_mode_changes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    requested_mode: Mapped[str] = mapped_column(String(10))
    expected_revision: Mapped[int] = mapped_column(Integer)
    proposed_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[float] = mapped_column(Float)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class AIObservation(TenantOwned, Base):
    __tablename__ = "ai_observations"
    __table_args__ = (UniqueConstraint("sent_message_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    sent_message_id: Mapped[str] = mapped_column(ForeignKey("agent_sent_messages.id"))
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"), index=True)
    reservation_id: Mapped[str | None] = mapped_column(String(120))
    conversation_id: Mapped[str] = mapped_column(String(120), index=True)
    guest_name: Mapped[str] = mapped_column(String(160), default="")
    encrypted_received: Mapped[str] = mapped_column(Text)
    encrypted_original: Mapped[str] = mapped_column(Text)
    encrypted_context: Mapped[str] = mapped_column(Text)
    encrypted_escalation: Mapped[str] = mapped_column(Text)
    situation: Mapped[str] = mapped_column(String(50), default="general")
    effective_test: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class ResponseFeedback(TenantOwned, Base):
    __tablename__ = "response_feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    observation_id: Mapped[str] = mapped_column(ForeignKey("ai_observations.id"), index=True)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id"))
    human_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))
    encrypted_received: Mapped[str] = mapped_column(Text)
    encrypted_original: Mapped[str] = mapped_column(Text)
    encrypted_corrected: Mapped[str] = mapped_column(Text)
    encrypted_reason: Mapped[str] = mapped_column(Text)
    situation: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="validated")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class HumanStyleExample(TenantOwned, Base):
    __tablename__ = "human_style_examples"
    __table_args__ = (UniqueConstraint("source", "source_id"), CheckConstraint("source IN ('test_correction','manual_manager','guesty_human')", name="human_source_only"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[str] = mapped_column(String(160))
    human_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    encrypted_body: Mapped[str] = mapped_column(Text)
    body_hash: Mapped[str] = mapped_column(String(64))
    validated: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

class StylePreferences(TenantOwned, Base):
    __tablename__ = "style_preferences"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default="host")
    learned_traits: Mapped[dict] = mapped_column(JSON, default=dict)
    explicit_traits: Mapped[dict] = mapped_column(JSON, default=dict)
    source_example_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)

class ListingLifecycle(TenantOwned, Base):
    __tablename__ = "listing_lifecycle"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    canonical_listing_id: Mapped[str | None] = mapped_column(String(120))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    from_billable: Mapped[bool] = mapped_column(Boolean, default=False)
    to_billable: Mapped[bool] = mapped_column(Boolean, default=False)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    source: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)

class PropertySync(TenantOwned, Base):
    __tablename__ = 'property_sync'
    property_id: Mapped[str] = mapped_column(ForeignKey('properties.id'), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(120), unique=True)
    encrypted_snapshot: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default='synced')
    synced_at: Mapped[float] = mapped_column(Float, default=time.time)
    missing_since: Mapped[float | None] = mapped_column(Float)

class PropertyKnowledge(TenantOwned, Base):
    __tablename__ = 'property_knowledge'
    __table_args__ = (UniqueConstraint('property_id','key','source'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    property_id: Mapped[str] = mapped_column(ForeignKey('properties.id'), index=True)
    key: Mapped[str] = mapped_column(String(100))
    encrypted_value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30))
    source_reference: Mapped[str | None] = mapped_column(String(160))
    confidence: Mapped[float] = mapped_column(Float, default=1)
    knowledge_type: Mapped[str] = mapped_column(String(20), default='permanent')
    valid_from: Mapped[float] = mapped_column(Float, default=time.time)
    valid_until: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)

# Enforce ownership also in database constraints, including parent/child relationships.
GLOBAL_TABLES = {"organizations", "users", "organization_memberships", "login_limits", "admin_sessions"}
TENANT_MODELS = []
for mapper in list(Base.registry.mappers):
    model, table = mapper.class_, mapper.local_table
    if table.name in GLOBAL_TABLES:
        continue
    TENANT_MODELS.append(model)
    for constraint in list(table.constraints):
        if isinstance(constraint, UniqueConstraint):
            names = [column.name for column in constraint.columns]
            table.constraints.remove(constraint)
            table.append_constraint(UniqueConstraint("organization_id", *names))
    for constraint in list(table.foreign_key_constraints):
        elements = list(constraint.elements)
        target = elements[0].target_fullname.split('.')[0]
        if target in GLOBAL_TABLES:
            continue
        columns = [element.parent.name for element in elements]
        targets = [element.target_fullname for element in elements]
        table.constraints.remove(constraint)
        for element in elements:
            table.foreign_keys.discard(element)
            element.parent.foreign_keys.discard(element)
        table.append_constraint(ForeignKeyConstraint(["organization_id", *columns], [target+".organization_id", *targets]))
AdminSession.organization_id = mapped_column(String(36), ForeignKey("organizations.id"))
AdminSession.user_id = mapped_column(String(36), ForeignKey("users.id"))
