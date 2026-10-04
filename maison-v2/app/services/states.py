import time
from sqlalchemy import select
from app.db.models import PropertyState

class StateService:
    def apply(self, session, prop, action):
        state = session.scalar(select(PropertyState).where(PropertyState.property_id == prop.id, PropertyState.key == action.state_key))
        if not state:
            state = PropertyState(property_id=prop.id, key=action.state_key, status=action.state_status)
            session.add(state)
        state.status, state.note = action.state_status, action.note or ""
        state.valid_from, state.updated_at = time.time(), time.time()
        state.valid_until = action.valid_until.timestamp() if action.valid_until else None
