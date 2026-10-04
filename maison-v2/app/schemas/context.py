from pydantic import BaseModel, Field

class GuestContext(BaseModel):
    conversation_id: str
    conversation_type: str = "INQUIRY"
    reservation_id: str | None = None
    reservation_status: str | None = None
    guest_name: str = ""
    property_id: str | None = None
    guesty_listing_id: str | None = None
    property_name: str | None = None
    local_now: str = ""
    reservation_check_in: str | None = None
    reservation_check_out: str | None = None
    requested_check_in: str | None = None
    requested_check_out: str | None = None
    availability_status: str = "not_checked"
    access_authorized: bool = False
    property_facts: dict = Field(default_factory=dict)
    property_current_states: list[dict] = Field(default_factory=list)
    property_rules: dict = Field(default_factory=dict)
    property_notes: list[str] = Field(default_factory=list)
    access: dict = Field(default_factory=dict)
    wifi: dict = Field(default_factory=dict)
    recent_history: list[dict] = Field(default_factory=list)
    open_escalations: list[dict] = Field(default_factory=list)
    resolution_reason: str | None = None
