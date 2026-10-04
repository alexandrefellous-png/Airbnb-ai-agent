import time
from sqlalchemy import select
from app.db.models import ChatMessage, ManagerWorkspace

class ChatHistory:
    def __init__(self, db, vault):
        self.db, self.vault = db, vault

    def append(self, role, content):
        with self.db.session() as session:
            row = ChatMessage(role=role, encrypted_content=self.vault.encrypt(content))
            session.add(row); session.commit()
            return row.id

    def list(self, limit=100):
        with self.db.session() as session:
            rows = session.scalars(select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(limit)).all()
            return [{"id": m.id, "role": m.role, "content": self.vault.decrypt(m.encrypted_content), "created_at": m.created_at} for m in reversed(rows)]

    def workspace(self):
        with self.db.session() as session:
            row = session.get(ManagerWorkspace, "onboarding")
            return self.vault.decrypt(row.encrypted_data) if row else {}

    def set_workspace(self, data):
        with self.db.session() as session:
            row = session.get(ManagerWorkspace, "onboarding")
            if not row:
                row = ManagerWorkspace(id="onboarding", encrypted_data=self.vault.encrypt(data)); session.add(row)
            row.encrypted_data, row.updated_at = self.vault.encrypt(data), time.time()
            session.commit()
