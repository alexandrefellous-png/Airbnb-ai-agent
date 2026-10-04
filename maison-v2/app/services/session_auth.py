import secrets
import time
import asyncio
from sqlalchemy import select, delete
from app.core.security import fingerprint
from app.core.passwords import verify_password, hash_password
from app.db.models import AdminSession, LoginLimit, User, Membership, Organization

COOKIE = "manager_session"
class SessionError(ValueError): pass

class SessionService:
    def __init__(self, db, vault, settings):
        self.db, self.vault, self.settings = db, vault, settings
        self._login_lock = asyncio.Lock()
        self._dummy_hash = hash_password("dummy-password-for-timing")

    async def login(self, username, password, address, workspace=None):
        # Database throttle is durable. PostgreSQL row lock coordinates application instances.
        key = fingerprint("login:"+address)
        async with self._login_lock:
            with self.db.system_session() as session:
                now = time.time()
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                insert = pg_insert if self.db.engine.dialect.name == "postgresql" else sqlite_insert
                session.execute(insert(LoginLimit).values(key=key, failures=0, window_start=now, blocked_until=0).on_conflict_do_nothing())
                row = session.scalar(select(LoginLimit).where(LoginLimit.key == key).with_for_update())
                if row.blocked_until > now: raise SessionError("Trop de tentatives. Réessayez dans quelques minutes.")
                if now-row.window_start > 900: row.failures, row.window_start = 0, now
                user = session.scalar(select(User).where(User.email == username.strip().casefold(), User.active.is_(True)))
                valid = verify_password(password, user.password_hash if user else self._dummy_hash)
                if not user or not valid:
                    row.failures += 1
                    if row.failures >= 5: row.blocked_until = now+900
                    session.commit()
                    raise SessionError("Identifiant ou mot de passe incorrect.")
                memberships = session.execute(select(Membership, Organization).join(Organization).where(Membership.user_id == user.id, Organization.status == "active")).all()
                choices = [(m,o) for m,o in memberships if not workspace or o.id == workspace]
                if len(choices) != 1:
                    raise SessionError("Précisez votre workspace." if len(choices) > 1 else "Accès au workspace indisponible.")
                membership, org = choices[0]
                row.failures, row.blocked_until = 0, 0
                token = secrets.token_urlsafe(48)
                session.add(AdminSession(token_hash=fingerprint(token), secret_version=fingerprint(user.password_hash), user_id=user.id,
                    organization_id=org.id, encrypted_csrf=self.vault.encrypt(secrets.token_urlsafe(32)), expires_at=now+self.settings.session_hours*3600))
                session.execute(delete(AdminSession).where(AdminSession.expires_at < now))
                session.commit()
                return token

    async def register(self, email, password, workspace_name, address):
        from sqlalchemy.exc import IntegrityError
        email = email.strip().casefold()
        key = fingerprint("registration:" + address)
        async with self._login_lock:
            with self.db.system_session() as session:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                insert = pg_insert if self.db.engine.dialect.name == "postgresql" else sqlite_insert
                now = time.time()
                session.execute(insert(LoginLimit).values(key=key, failures=0, window_start=now, blocked_until=0).on_conflict_do_nothing())
                rate = session.scalar(select(LoginLimit).where(LoginLimit.key==key).with_for_update())
                if now-rate.window_start > 3600:
                    rate.failures, rate.window_start = 0, now
                if rate.failures >= 5:
                    raise SessionError("Trop de créations de compte. Réessayez plus tard.")
                rate.failures += 1
                session.commit()
                if session.scalar(select(User.id).where(User.email==email)):
                    raise SessionError("Un compte utilise déjà cet email. Connectez-vous.")
                org = Organization(name=workspace_name.strip(), agent_mode="test")
                user = User(email=email, password_hash=hash_password(password))
                session.add_all([org,user]); session.flush()
                session.add(Membership(organization_id=org.id,user_id=user.id,role="owner"))
                token = secrets.token_urlsafe(48)
                session.add(AdminSession(token_hash=fingerprint(token), secret_version=fingerprint(user.password_hash), user_id=user.id,
                    organization_id=org.id, encrypted_csrf=self.vault.encrypt(secrets.token_urlsafe(32)), expires_at=now+self.settings.session_hours*3600))
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    raise SessionError("Un compte utilise déjà cet email. Connectez-vous.") from None
                return token

    def verify(self, token):
        if not token: return None
        with self.db.system_session() as session:
            row = session.get(AdminSession, fingerprint(token))
            if not row or row.expires_at < time.time(): return None
            user, org = session.get(User,row.user_id), session.get(Organization,row.organization_id)
            member = session.get(Membership, (row.organization_id,row.user_id))
            if not user or not user.active or not org or org.status != "active" or not member or member.role not in {"owner","admin","manager","viewer"}:
                return None
            if not secrets.compare_digest(row.secret_version, fingerprint(user.password_hash)): return None
            return {"manager": user.email, "user_id": user.id, "organization_id": org.id, "workspace": org.name,
                "role": member.role, "csrf": self.vault.decrypt(row.encrypted_csrf), "expires_at": row.expires_at}

    def logout(self, token):
        with self.db.system_session() as session:
            session.execute(delete(AdminSession).where(AdminSession.token_hash == fingerprint(token or "")))
            session.commit()
