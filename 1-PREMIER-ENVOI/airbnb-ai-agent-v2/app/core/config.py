import os
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("private/local.env", ".env"), extra="ignore")
    app_env: str = "development"
    database_url: str = "sqlite:///./agent.db"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    guesty_client_id: str = ""
    guesty_client_secret: str = ""
    guesty_webhook_secret: str = ""
    admin_secret: str = ""
    token_encryption_key: str = ""
    test_mode: bool = True  # legacy local setup; database workspace mode is authoritative
    allow_live_sends: bool = False
    debounce_seconds: int = 12
    access_before_hours: int = 24
    access_after_hours: int = 0
    worker_enabled: bool = True
    manager_notification_webhook_url: str = ""
    history_limit: int = 100
    max_event_age_hours: int = 48
    session_hours: int = 12
    media_storage: str = "database"
    media_max_mb: int = 25
    media_s3_bucket: str = ""
    media_s3_region: str = "eu-west-3"
    media_s3_endpoint: str = ""
    public_base_url: str = ""
    observation_enabled: bool = False
    observation_interval_seconds: int = 60
    google_client_id: str = ""
    google_client_secret: str = ""



    @model_validator(mode="after")
    def validate_runtime(self):
        if self.debounce_seconds < 0 or self.access_before_hours < 0 or self.access_after_hours < 0:
            raise ValueError("Durées négatives interdites")
        if self.app_env == "production":
            if not self.public_base_url:
                self.public_base_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
            if not self.database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
                raise ValueError("PostgreSQL requis en production")
            if not self.token_encryption_key or (self.admin_secret and len(self.admin_secret) < 32):
                raise ValueError("Clé de chiffrement et mot de passe initial robuste si fourni requis")
        if self.media_storage not in {"database", "s3"} or not 1 <= self.media_max_mb <= 100:
            raise ValueError("Stockage média ou limite invalide")
        if self.media_storage == "s3" and not self.media_s3_bucket:
            raise ValueError("MEDIA_S3_BUCKET requis")
        if not 1 <= self.session_hours <= 48:
            raise ValueError("SESSION_HOURS doit être entre 1 et 48")
        if (not self.test_mode or self.allow_live_sends) and self.app_env != "production":
            raise ValueError("Les envois réels sont réservés à APP_ENV=production")
        return self
