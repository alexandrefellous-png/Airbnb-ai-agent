import os, time, html, re, logging
from typing import Any
import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("airbnb-agent")

app = FastAPI(title="Airbnb AI Agent")
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

GUESTY_CLIENT_ID = os.environ.get("GUESTY_CLIENT_ID", "")
GUESTY_CLIENT_SECRET = os.environ.get("GUESTY_CLIENT_SECRET", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6")
TEST_MODE = os.environ.get("TEST_MODE", "true").lower() == "true"

GUESTY_BASE = "https://open-api.guesty.com/v1"
TOKEN_URL = "https://open-api.guesty.com/oauth2/token"

_token = {"value": None, "expires_at": 0}

# IMPORTANT:
# Add each Guesty listing ID here. Unknown listings are NEVER auto-answered.
PROPERTIES = {
    # Replace this placeholder after we identify the real Guesty listing ID.
    "6a908557b01e820012493069": {
        "name": "31 rue du Caire",
        "address": "31 rue du Caire, 75002 Paris",
        "check_in": "16:00",
        "check_out": "10:00",
        "facts": [
            "Appartement au 1er étage.",
            "Prendre le deuxième escalier après la cour.",
            "Immeuble sans ascenseur.",
            "Nous pouvons aider les voyageurs avec leurs bagages lors du check-in.",
            "2 chambres.",
            "2 salles de bains.",
            "1 toilette (WC) au total.",
            "Cuisine entièrement équipée.",
            "Climatisation dans tout l'appartement.",
            "Boîte à clés sécurisée à l'entrée de l'immeuble, dans la boîte aux lettres.",
            "Code de la boîte à clés : C2613.",
            "Le Wi-Fi est communiqué automatiquement via la messagerie Airbnb.",
            "Une vidéo d'accès à l'appartement existe dans les ressources internes."
        ],
        "arrival_instructions": (
            "Adresse : 31 rue du Caire, 75002 Paris. "
            "À l'entrée de l'immeuble, la boîte à clés sécurisée se trouve dans la boîte aux lettres "
            "(code C2613). Après être entré, traverser la cour puis emprunter le deuxième escalier. "
            "L'appartement est au 1er étage."
        )
    }
}

SYSTEM_RULES = """
Tu es l'assistant de messagerie d'un hôte Airbnb à Paris.

RÈGLES ABSOLUES
- Tu réponds UNIQUEMENT après un message entrant d'un voyageur.
- Réponds uniquement à partir des informations du logement fournies dans le contexte.
- N'invente JAMAIS une information. Si l'information manque ou si tu as un doute, retourne exactement: ESCALATE
- Pour remboursement, annulation, réduction, compensation, litige, paiement, modification de réservation,
  urgence médicale/sécurité, menace, plainte sérieuse ou demande inhabituelle: retourne exactement ESCALATE.
- Ne promets jamais un early check-in ou late check-out si le contexte ne l'autorise pas explicitement.
- Ne révèle jamais les informations d'un autre logement.
- Ne récite pas toute la fiche: réponds seulement à la question posée.
- Les messages automatiques Airbnb existent déjà. Tu ne les déclenches pas et tu ne les répètes pas sans raison.
  Si le voyageur demande explicitement une information déjà envoyée (ex. Wi-Fi), tu peux répondre avec ce que le contexte autorise.

STYLE
- Réponse chaleureuse, naturelle, sympathique, assez courte.
- Commence normalement par: "Bonjour [Prénom], merci pour votre message :)"
- Utilise le prénom si disponible.
- Termine naturellement, par exemple "Au plaisir de vous recevoir !" ou
  "N'hésitez pas si vous avez besoin de quoi que ce soit :)"
- Réponds dans la langue du voyageur quand elle est évidente.
- Pas de ton robotique, pas de longues listes sauf si des étapes d'accès sont nécessaires.
"""

def clean_message(body: str) -> str:
    if not body:
        return ""
    body = html.unescape(body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body

async def guesty_token() -> str:
    now = time.time()
    if _token["value"] and now < _token["expires_at"] - 3600:
        return _token["value"]

    if not GUESTY_CLIENT_ID or not GUESTY_CLIENT_SECRET:
        raise RuntimeError("Guesty credentials missing")

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            TOKEN_URL,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "scope": "open-api",
                "client_id": GUESTY_CLIENT_ID,
                "client_secret": GUESTY_CLIENT_SECRET,
            },
        )
        r.raise_for_status()
        data = r.json()

    _token["value"] = data["access_token"]
    _token["expires_at"] = now + int(data.get("expires_in", 86400))
    return _token["value"]

async def guesty_get(path: str, params=None) -> Any:
    token = await guesty_token()
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.get(
            f"{GUESTY_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params,
        )
        r.raise_for_status()
        return r.json()

async def guesty_post(path: str, payload: dict) -> Any:
    token = await guesty_token()
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(
            f"{GUESTY_BASE}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

def deep_find(obj: Any, keys: set[str]):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and v:
                if isinstance(v, dict):
                    for subkey in ("_id", "id"):
                        if v.get(subkey):
                            return v[subkey]
                else:
                    return v
        for v in obj.values():
            found = deep_find(v, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = deep_find(v, keys)
            if found:
                return found
    return None

async def get_reservation(reservation_id: str) -> dict:
    data = await guesty_get("/reservations-v3", params=[("reservationIds", reservation_id)])
    if isinstance(data, list):
        return data[0] if data else {}
    for key in ("data", "results", "reservations"):
        if isinstance(data, dict) and isinstance(data.get(key), list) and data[key]:
            return data[key][0]
    return data if isinstance(data, dict) else {}

def extract_listing_id(reservation: dict):
    return deep_find(reservation, {"listingId", "listing"})

def extract_conversation_id(reservation: dict, payload: dict):
    cid = deep_find(reservation, {"conversationId", "conversation"})
    if cid:
        return cid
    conv = payload.get("conversation") or {}
    return conv.get("_id") or conv.get("id")

def extract_guest_name(reservation: dict, payload: dict) -> str:
    conv = payload.get("conversation") or {}
    meta = conv.get("meta") or {}
    name = meta.get("guestName")
    if name:
        return str(name).split()[0]
    guest = reservation.get("guest") if isinstance(reservation, dict) else None
    if isinstance(guest, dict):
        for k in ("firstName", "fullname", "fullName"):
            if guest.get(k):
                return str(guest[k]).split()[0]
    return "Bonjour"

async def latest_guest_message(conversation_id: str, payload: dict) -> str:
    # Prefer fetching fresh conversation posts, as Guesty recommends.
    try:
        posts = await guesty_get(
            f"/communication/conversations/{conversation_id}/posts",
            params={"sort": "-createdAt", "limit": 20},
        )
        candidates = posts if isinstance(posts, list) else (
            posts.get("data") or posts.get("results") or posts.get("posts") or []
        )
        for p in candidates:
            if not isinstance(p, dict):
                continue
            typ = str(p.get("type", "")).lower()
            sent_by = str(p.get("sentBy", "")).lower()
            if "guest" in typ or sent_by == "guest" or typ == "fromthirdparty":
                return clean_message(p.get("body", ""))
    except Exception:
        log.exception("Could not fetch conversation posts; falling back to webhook payload")

    msg = payload.get("message") or {}
    return clean_message(msg.get("body", ""))

def generate_reply(first_name: str, property_data: dict, guest_message: str) -> str:
    context = f"""
VOYAGEUR
Prénom: {first_name}

LOGEMENT
Nom: {property_data['name']}
Adresse: {property_data['address']}
Check-in: à partir de {property_data['check_in']}
Check-out: avant {property_data['check_out']}
Informations fiables:
- """ + "\n- ".join(property_data["facts"]) + f"""

Instructions d'accès fiables:
{property_data['arrival_instructions']}

MESSAGE DU VOYAGEUR
{guest_message}
"""
    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        instructions=SYSTEM_RULES,
        input=context,
    )
    return response.output_text.strip()

async def send_reply(conversation_id: str, reply: str, incoming_module: Any):
    # In TEST_MODE nothing is sent to the guest.
    if TEST_MODE:
        log.warning("TEST_MODE reply for %s: %s", conversation_id, reply)
        return

    module = incoming_module if isinstance(incoming_module, dict) else {"type": "platform"}
    await guesty_post(
        f"/communication/conversations/{conversation_id}/send-message",
        {"module": module, "body": reply},
    )

async def process_message(payload: dict):
    try:
        if payload.get("event") != "reservation.messageReceived":
            return

        msg = payload.get("message") or {}
        msg_type = str(msg.get("type", "")).lower()

        # Only inbound guest-ish messages. Never react to our own sent messages.
        if msg_type and msg_type not in {"fromguest", "fromthirdparty"}:
            log.info("Ignored message type: %s", msg_type)
            return

        reservation_id = payload.get("reservationId")
        if not reservation_id:
            log.warning("No reservationId; ignored")
            return

        reservation = await get_reservation(reservation_id)
        listing_id = extract_listing_id(reservation)

        if listing_id not in PROPERTIES:
            log.warning("Unknown listing %s: NO AUTO REPLY", listing_id)
            return

        conversation_id = extract_conversation_id(reservation, payload)
        if not conversation_id:
            log.warning("No conversation ID: NO AUTO REPLY")
            return

        guest_message = await latest_guest_message(conversation_id, payload)
        if not guest_message:
            log.warning("Empty guest message: ignored")
            return

        first_name = extract_guest_name(reservation, payload)
        reply = generate_reply(first_name, PROPERTIES[listing_id], guest_message)

        if reply == "ESCALATE":
            log.warning("ESCALATE reservation=%s message=%s", reservation_id, guest_message)
            return

        await send_reply(conversation_id, reply, msg.get("module"))
    except Exception:
        log.exception("Message processing failed")

@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "airbnb-ai-agent",
        "test_mode": TEST_MODE,
        "known_properties": len(PROPERTIES),
    }

@app.get("/health")
async def health():
    return {"ok": True, "test_mode": TEST_MODE}

@app.post("/guesty/webhook")
async def guesty_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    # Return 2xx quickly; process in background.
    background_tasks.add_task(process_message, payload)
    return {"received": True}
