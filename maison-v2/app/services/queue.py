import json
import time
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.core.security import fingerprint
from app.db.models import Event

class EventQueue:
    def __init__(self, db, vault, settings):
        self.db, self.vault, self.settings = db, vault, settings

    def enqueue(self, payload, delivery_id=None):
        if payload.get("event") != "reservation.messageReceived":
            return {"status": "ignored"}
        message = payload.get("message", {})
        conversation = payload.get("conversation", {})
        if conversation.get("conversationWith", "Guest") != "Guest" or message.get("type") != "fromGuest":
            # fromThirdParty may be Airbnb/support mail, so requires manual classification.
            return {"status": "ignored", "reason": "not_verified_guest_message"}
        if not isinstance(message.get("body"), str) or not message["body"].strip():
            return {"status": "ignored", "reason": "empty_message"}
        cid = conversation.get("_id")
        identity = message.get("_id") or message.get("id") or message.get("postId")
        semantic = json.dumps({"conversation": cid, "reservation": payload.get("reservationId"),
            "createdAt": message.get("createdAt"), "body": message["body"]}, sort_keys=True)
        message_key = fingerprint(str(identity) if identity else semantic)
        event_id = fingerprint(delivery_id or message_key)
        status, reason = "pending", ""
        if message.get("createdAt"):
            try:
                dt = datetime.fromisoformat(message["createdAt"].replace("Z", "+00:00"))
                if dt.tzinfo is None or time.time()-dt.timestamp() > self.settings.max_event_age_hours*3600:
                    status, reason = "quarantined", "historical_message"
            except ValueError:
                status, reason = "quarantined", "invalid_message_timestamp"
        with self.db.session() as session:
            session.add(Event(id=event_id, message_key=message_key, conversation_id=cid,
                encrypted_payload=self.vault.encrypt(payload), due_at=time.time()+self.settings.debounce_seconds,
                status=status, error=reason))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return {"status": "duplicate"}
        return {"status": status, "event_id": event_id}
