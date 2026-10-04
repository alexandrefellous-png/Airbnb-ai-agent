import time
from sqlalchemy import select
from app.db.models import Escalation

class EscalationService:
    def __init__(self, db, vault):
        self.db, self.vault = db, vault

    def create(self, key, summary, context=None, priority="normal", conversation_id=None):
        with self.db.session() as session:
            current = session.scalar(select(Escalation).where(Escalation.dedup_key == key))
            if current:
                return current.id
            row = Escalation(dedup_key=key, property_id=context.property_id if context else None,
                guesty_conversation_id=context.conversation_id if context else conversation_id,
                reservation_id=context.reservation_id if context else None,
                guest_name=context.guest_name if context else "", encrypted_summary=self.vault.encrypt(summary),
                priority=priority)
            session.add(row)
            session.commit()
            return row.id

class NotificationService:
    def __init__(self, db, settings, http):
        self.db, self.settings, self.http = db, settings, http

    async def tick(self):
        # Notify using IDs only: never transmit guest secrets to an arbitrary notifier.
        async with self.db.lock("notifications"):
            with self.db.session() as session:
                rows = session.scalars(select(Escalation).where(Escalation.notification_status.in_(["pending", "failed"]),
                    Escalation.notification_due <= time.time()).limit(20)).all()
                for row in rows:
                    if self.settings.test_mode or not self.settings.manager_notification_webhook_url:
                        row.notification_status = "admin_only"
                        continue
                    row.notification_attempts += 1
                    try:
                        response = await self.http.post(self.settings.manager_notification_webhook_url,
                            json={"escalation_id": row.id, "priority": row.priority,
                                  "conversation_id": row.guesty_conversation_id})
                        response.raise_for_status()
                        row.notification_status = "sent"
                    except Exception:
                        row.notification_status = "failed"
                        row.notification_due = time.time() + min(3600, 30 * 2 ** min(row.notification_attempts, 7))
                session.commit()
