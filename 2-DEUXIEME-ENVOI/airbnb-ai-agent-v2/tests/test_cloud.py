from concurrent.futures import Future
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app


def test_render_public_url_only_default(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://maison-test.onrender.com/")
    options = dict(_env_file=None, app_env="production", database_url="postgresql://localhost/synthetic", token_encryption_key=Fernet.generate_key().decode())
    assert Settings(**options).public_base_url == "https://maison-test.onrender.com"
    assert Settings(**options, public_base_url="https://custom.example").public_base_url == "https://custom.example"
    assert Settings(_env_file=None).public_base_url == ""


def test_health_fails_when_background_agent_stops(env):
    settings, db, _, guesty, ai = env
    settings.worker_enabled = True
    app = create_app(settings, db, guesty, ai)
    task = Future()
    app.state.worker_tasks = [task]
    client = TestClient(app)
    assert client.get('/health').status_code == 200
    task.set_result(None)
    assert client.get('/health').status_code == 503
