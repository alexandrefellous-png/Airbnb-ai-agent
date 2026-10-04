import secrets
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from app.services.session_auth import COOKIE, SessionError

router = APIRouter(prefix="/admin")
TEMPLATES = Path(__file__).parents[1]/"templates"

class LoginInput(BaseModel):
    username: str = Field(default="admin", max_length=250)
    password: str = Field(min_length=1, max_length=500)
    workspace: str | None = Field(default=None, max_length=36)

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.app.state.sessions.verify(request.cookies.get(COOKIE)):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse((TEMPLATES/"login.html").read_text())

@router.post("/login")
async def login(body: LoginInput, request: Request):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Origine non autorisée")
    try:
        token = await request.app.state.sessions.login(body.username, body.password, request.client.host if request.client else "unknown", body.workspace)
    except SessionError as exc:
        raise HTTPException(429 if "tentatives" in str(exc) else 401, str(exc)) from None
    response = __import__("fastapi.responses", fromlist=["JSONResponse"]).JSONResponse({"ok": True})
    response.set_cookie(COOKIE, token, max_age=request.app.state.settings.session_hours*3600,
        httponly=True, secure=request.app.state.settings.app_env == "production", samesite="strict", path="/admin")
    return response


class RegisterInput(BaseModel):
    email: str = Field(min_length=5, max_length=250, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=12, max_length=500)
    workspace_name: str = Field(min_length=2, max_length=160)

@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if request.app.state.sessions.verify(request.cookies.get(COOKIE)):
        return RedirectResponse("/admin", status_code=303)
    return HTMLResponse((TEMPLATES/"register.html").read_text())

@router.post("/register")
async def register(body: RegisterInput, request: Request):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Origine non autorisée")
    if not body.workspace_name.strip():
        raise HTTPException(422, "Indiquez le nom de votre conciergerie.")
    try:
        token = await request.app.state.sessions.register(body.email, body.password, body.workspace_name, request.client.host if request.client else "unknown")
    except SessionError as exc:
        raise HTTPException(429 if "Trop" in str(exc) else 409, str(exc)) from None
    from fastapi.responses import JSONResponse
    response = JSONResponse({"ok":True, "next":"/admin"}, status_code=201)
    response.set_cookie(COOKIE, token, max_age=request.app.state.settings.session_hours*3600,
        httponly=True, secure=request.app.state.settings.app_env=="production", samesite="strict", path="/admin")
    return response


@router.get("/drive/callback", response_class=HTMLResponse)
def drive_callback_page():
    # A Google redirect cannot carry a Strict cookie. The same-origin landing page
    # sends an authenticated CSRF-protected POST to finish the flow instead.
    return HTMLResponse((TEMPLATES/"drive_callback.html").read_text())


def authenticate(request: Request):
    identity = request.app.state.sessions.verify(request.cookies.get(COOKIE))
    if not identity:
        if request.method == "GET" and request.url.path == "/admin":
            raise HTTPException(303, headers={"Location": "/admin/login"})
        raise HTTPException(401, "Connectez-vous pour continuer.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if identity["role"] == "viewer" and request.url.path != "/admin/translations":
            raise HTTPException(403, "Votre rôle permet uniquement la consultation.")
        if (request.url.path.startswith("/admin/settings") and not request.url.path.startswith("/admin/settings/style")) and identity["role"] not in {"owner", "admin"}:
            raise HTTPException(403, "Un administrateur du workspace est requis.")
        csrf = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(csrf.encode(), identity["csrf"].encode()):
            raise HTTPException(403, "Session de formulaire invalide. Rechargez la page.")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Origine non autorisée")
    return identity
