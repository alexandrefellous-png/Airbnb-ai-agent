import logging
import time
import httpx
from app.core.security import fingerprint
from app.db.models import OAuthToken
from app.guesty.errors import GuestyError, retry_timestamp

log = logging.getLogger("agent.guesty")

class TokenService:
    def __init__(self, settings, db, vault, http: httpx.AsyncClient):
        self.settings, self.db, self.vault, self.http = settings, db, vault, http

    @property
    def key(self):
        return fingerprint((self.settings.guesty_client_id or "unconfigured") + ":" + fingerprint(self.settings.guesty_client_secret or ""))

    async def get(self, rejected: str | None = None) -> str:
        async with self.db.lock("oauth:" + self.key):
            with self.db.session() as session:
                row = session.get(OAuthToken, self.key)
                if row is None:
                    row = OAuthToken(id=self.key)
                    session.add(row)
                    session.commit()
                now = time.time()
                current = self.vault.decrypt(row.encrypted_token) if row.encrypted_token else None
                if current and row.expires_at > now + 60 and current != rejected:
                    log.info("GUESTY TOKEN: cached")
                    return current
                if row.retry_at > now:
                    raise GuestyError("oauth", 429, row.retry_at)
                if not self.settings.guesty_client_id or not self.settings.guesty_client_secret:
                    raise GuestyError("oauth_credentials_missing", 503)
                # Persist a cooldown before making a request: crashes cannot create token storms.
                row.retry_at = now + 60
                session.commit()
                log.info("GUESTY TOKEN: refreshing")
                try:
                    response = await self.http.post(
                        "https://open-api.guesty.com/oauth2/token",
                        data={"grant_type": "client_credentials", "scope": "open-api",
                              "client_id": self.settings.guesty_client_id,
                              "client_secret": self.settings.guesty_client_secret},
                        headers={"Accept": "application/json"})
                except httpx.HTTPError:
                    raise GuestyError("oauth_transport", 503, row.retry_at) from None
                if response.status_code == 429:
                    row.retry_at = retry_timestamp(response.headers)
                    session.commit()
                    log.warning("GUESTY TOKEN 429 - retry at %.0f", row.retry_at)
                    raise GuestyError("oauth", 429, row.retry_at)
                if response.status_code != 200:
                    raise GuestyError("oauth", response.status_code, row.retry_at)
                data = response.json()
                if not isinstance(data.get("access_token"), str) or int(data.get("expires_in", 0)) <= 60:
                    raise GuestyError("oauth_contract", 502, row.retry_at)
                row.encrypted_token = self.vault.encrypt(data["access_token"])
                row.expires_at = time.time() + int(data["expires_in"])
                row.retry_at = 0
                session.commit()
                return data["access_token"]
