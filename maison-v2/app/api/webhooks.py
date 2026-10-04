import json
from fastapi import APIRouter, HTTPException, Request
from svix.webhooks import Webhook, WebhookVerificationError

router = APIRouter()

@router.post("/guesty/webhook/{organization_id}")
async def webhook(organization_id: str, request: Request):
    from app.core.tenancy import organization_id as current_organization
    try:
        if current_organization() != organization_id: raise PermissionError()
    except PermissionError:
        raise HTTPException(401, "Connexion webhook invalide")
    raw = await request.body()
    if len(raw) > 1_000_000:
        raise HTTPException(413, "Événement trop volumineux")
    settings = request.app.state.settings
    if settings.guesty_webhook_secret:
        try:
            Webhook(settings.guesty_webhook_secret).verify(raw, dict(request.headers))
        except (WebhookVerificationError, ValueError):
            raise HTTPException(401, "Signature Svix invalide") from None
    elif settings.app_env == "production":
        raise HTTPException(401, "Secret webhook requis")
    else:
        # Unsigned simulation is only accessible through authenticated admin routes.
        raise HTTPException(401, "Configurez GUESTY_WEBHOOK_SECRET ou utilisez /admin/simulate")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict): raise ValueError()
        result = request.app.state.queue.enqueue(payload, request.headers.get("svix-id"))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "Payload invalide") from None
    return result

@router.post("/guesty/webhook")
async def unscoped_webhook():
    raise HTTPException(401, "Utilisez l'URL webhook propre au workspace")
