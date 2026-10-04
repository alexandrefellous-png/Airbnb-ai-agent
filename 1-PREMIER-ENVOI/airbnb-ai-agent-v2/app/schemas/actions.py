from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class FieldUpdate(StrictModel):
    field: str
    value: str

class ManagerAction(StrictModel):
    intent: Literal["create_property", "update_property_state", "update_fields", "add_note", "map_id", "resolve_escalation", "onboarding", "clarify", "get_property", "list_properties", "list_issues", "list_escalations", "list_stays", "activate_property", "archive_property", "attach_media", "decide_escalation", "set_agent_mode", "set_style_preference", "deactivate_property", "defer_onboarding", "resume_onboarding"]
    property: str | None
    updates: list[FieldUpdate]
    state_key: str | None
    state_status: Literal["working", "out_of_order", "unavailable", "scheduled"] | None
    valid_until: datetime | None
    note: str | None
    mapping_kind: Literal["listing", "unit", "unit_type", "last_stay_listing"] | None
    external_id: str | None
    escalation_id: str | None
    confidence: float = Field(ge=0, le=1)
    message: str
    media_id: str | None = None
    media_kind: str | None = None
    decision: Literal["accept", "refuse", "reply"] | None = None
    response_text: str | None = None
    query: str | None = None
    agent_mode: Literal["test", "live"] | None = None
    style_preference: str | None = None
    knowledge_type: Literal['permanent','temporary'] = 'permanent'

class GuestPlan(StrictModel):
    availability_requested: bool
    requested_check_in: str | None
    requested_check_out: str | None
    manager_required: bool
    priority: Literal["critical", "urgent", "normal", "low", "high"]
    reason: str | None
    missing_fields: list[str] = Field(default_factory=list,max_length=10)

class GuestReply(StrictModel):
    text: str
    escalation_required: bool
    escalation_summary: str | None
    priority: Literal["critical", "urgent", "normal", "low", "high"]

class StyleResult(StrictModel):
    profile: str

class StyleTraits(StrictModel):
    length: Literal["short", "balanced", "detailed"]
    tone: Literal["warm", "neutral", "formal"]
    emojis: Literal["none", "few", "frequent"]
    greeting: Literal["brief", "casual", "none"]
    closing: Literal["brief", "warm", "none"]
    smiley: bool
    avoid_repetition: bool
    direct_answers: bool
