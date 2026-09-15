import os
import time
import html
import re
import logging
from typing import Any

import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from openai import OpenAI


# ============================================================
# CONFIGURATION
# ============================================================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("airbnb-agent")

app = FastAPI()

GUESTY_CLIENT_ID = os.environ.get("GUESTY_CLIENT_ID", "")
GUESTY_CLIENT_SECRET = os.environ.get("GUESTY_CLIENT_SECRET", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# gpt-5.6 est actuellement un alias API valide.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6")

TEST_MODE = os.environ.get("TEST_MODE", "true").lower() == "true"

GUESTY_BASE = "https://open-api.guesty.com/v1"
TOKEN_URL = "https://open-api.guesty.com/oauth2/token"

RENDER_WEBHOOK_URL = (
    "https://airbnb-ai-agent-7neg.onrender.com/guesty/webhook"
)

openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# LOGEMENTS
# ============================================================

PROPERTIES = {
    "6a908557b01e820012493069": {
        "name": "31 rue du Caire",
        "address": "31 rue du Caire, 75002 Paris",

        "check_in": "16:00",
        "check_out": "10:00",

        "floor": "1er étage",
        "elevator": False,

        "access": (
            "Après être entré dans l'immeuble, traversez la cour. "
            "Prenez le deuxième escalier. "
            "L'appartement se trouve au 1er étage."
        ),

        "luggage": (
            "Nous pouvons aider les voyageurs avec leurs bagages "
            "au moment du check-in."
        ),

        "bedrooms": 2,
        "bathrooms": 2,
        "toilets": 1,

        "kitchen": "Cuisine entièrement équipée.",

        "air_conditioning": (
            "L'appartement est climatisé."
        ),

        "keybox": (
            "Une boîte à clés sécurisée se trouve à l'entrée "
            "de l'immeuble, au niveau des boîtes aux lettres."
        ),

        "keybox_code": "C2613",

        "wifi": (
            "Les informations Wi-Fi sont automatiquement "
            "communiquées au voyageur via Airbnb."
        ),
    }
}


# ============================================================
# REGLES DE L'AGENT
# ============================================================

SYSTEM_RULES = """
Tu es l'assistant de messagerie d'un hôte Airbnb à Paris.

Tu réponds UNIQUEMENT aux messages entrants des voyageurs.

STYLE :
- chaleureux
- naturel
- humain
- poli
- concis
- pas de langage robotique
- tu peux utiliser un smiley simple comme :)
- réponds dans la langue du voyageur
- évite les réponses inutilement longues

Exemple de ton :
"Bonjour Paul, merci pour votre message :) Le check-in est
possible à partir de 16h. Au plaisir de vous recevoir !"

IMPORTANT :
Tu dois utiliser uniquement les informations fournies dans
PROPERTY INFORMATION.

N'invente JAMAIS une information.

Si tu ne connais pas une réponse, réponds exactement :
ESCALATE

Tu peux répondre automatiquement aux questions simples comme :
- heure du check-in
- heure du check-out
- adresse
- accès à l'appartement
- étage
- ascenseur
- nombre de chambres
- nombre de salles de bain
- nombre de WC
- climatisation
- cuisine
- Wi-Fi
- bagages
- difficulté à trouver l'appartement

CAS SENSIBLES :
Réponds exactement ESCALATE si le voyageur parle de :
- remboursement
- annulation
- réduction
- remise
- compensation
- dédommagement
- litige
- paiement
- argent
- plainte sérieuse
- problème grave
- accident
- danger
- urgence médicale
- sécurité
- demande inhabituelle nécessitant une décision de l'hôte

Ne propose jamais spontanément un remboursement,
une réduction ou une compensation.

Ne répète pas automatiquement les informations Wi-Fi,
check-out ou autres messages automatiques sauf si le voyageur
pose explicitement la question.

Pour le code de boîte à clés :
ne le communique que si le contexte fourni indique
explicitement que le voyageur est autorisé à le recevoir.
Sinon réponds ESCALATE.
"""


# ============================================================
# TOKEN GUESTY
# ============================================================

_token_cache = {
    "token": None,
    "expires_at": 0,
}


async def guesty_token() -> str:

    now = time.time()

    if (
        _token_cache["token"]
        and now < _token_cache["expires_at"] - 60
    ):
        return _token_cache["token"]

    async with httpx.AsyncClient(timeout=30) as client:

        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "scope": "open-api",
                "client_id": GUESTY_CLIENT_ID,
                "client_secret": GUESTY_CLIENT_SECRET,
            },
            headers={
                "Content-Type":
                "application/x-www-form-urlencoded"
            },
        )

        response.raise_for_status()

        data = response.json()

    token = data["access_token"]

    expires_in = data.get("expires_in", 86400)

    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in

    return token


# ============================================================
# APPELS GUESTY
# ============================================================

async def guesty_get(
    path: str,
    params: Any = None,
):

    token = await guesty_token()

    async with httpx.AsyncClient(timeout=30) as client:

        response = await client.get(
            f"{GUESTY_BASE}{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        if response.status_code >= 400:
            log.error(
                "Guesty GET error %s - %s",
                response.status_code,
                response.text[:1000],
            )

        response.raise_for_status()

        return response.json()


async def guesty_post(
    path: str,
    body: dict,
):

    token = await guesty_token()

    async with httpx.AsyncClient(timeout=30) as client:

        response = await client.post(
            f"{GUESTY_BASE}{path}",
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

        if response.status_code >= 400:
            log.error(
                "Guesty POST error %s - %s",
                response.status_code,
                response.text[:1000],
            )

        response.raise_for_status()

        try:
            return response.json()
        except Exception:
            return {"status_code": response.status_code}


# ============================================================
# OUTILS
# ============================================================

def deep_find(obj: Any, keys: set[str]):

    if isinstance(obj, dict):

        for key, value in obj.items():

            if key in keys:

                if isinstance(value, str):
                    return value

                if isinstance(value, dict):

                    for id_key in (
                        "_id",
                        "id",
                        "listingId",
                        "reservationId",
                    ):
                        if value.get(id_key):
                            return value[id_key]

            result = deep_find(value, keys)

            if result:
                return result

    elif isinstance(obj, list):

        for item in obj:

            result = deep_find(item, keys)

            if result:
                return result

    return None


def clean_message(text: str) -> str:

    if not text:
        return ""

    text = html.unescape(text)

    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ============================================================
# RESERVATION
# ============================================================

async def get_reservation(
    reservation_id: str,
) -> dict:

    log.info(
        "Retrieving reservation %s",
        reservation_id,
    )

    # IMPORTANT :
    # Guesty attend reservationIds[].
    data = await guesty_get(
        "/reservations-v3",
        params=[
            (
                "reservationIds[]",
                reservation_id,
            )
        ],
    )

    log.info(
        "Reservation retrieved successfully"
    )

    if isinstance(data, list):

        if data:
            return data[0]

        return {}

    if isinstance(data, dict):

        for key in (
            "data",
            "results",
            "reservations",
            "items",
        ):

            value = data.get(key)

            if isinstance(value, list) and value:
                return value[0]

        return data

    return {}


# ============================================================
# IDENTIFICATION DU LOGEMENT
# ============================================================

def extract_listing_id(
    reservation: dict,
):

    # Formats Guesty possibles
    candidates = [
        reservation.get("listingId"),
        reservation.get("unitId"),
        reservation.get("unitTypeId"),
    ]

    listing = reservation.get("listing")

    if isinstance(listing, dict):

        candidates.extend([
            listing.get("_id"),
            listing.get("id"),
        ])

    elif isinstance(listing, str):

        candidates.append(listing)

    for candidate in candidates:

        if candidate in PROPERTIES:
            return candidate

    # Recherche récursive en secours
    found = deep_find(
        reservation,
        {
            "listingId",
            "listing",
        },
    )

    if found in PROPERTIES:
        return found

    return None


# ============================================================
# CONVERSATION
# ============================================================

def extract_conversation_id(
    reservation: dict,
    payload: dict,
):

    conversation = payload.get("conversation")

    if isinstance(conversation, dict):

        for key in ("_id", "id"):

            if conversation.get(key):
                return conversation[key]

    found = deep_find(
        reservation,
        {
            "conversationId",
            "conversation",
        },
    )

    if found:
        return found

    return None


# ============================================================
# NOM DU VOYAGEUR
# ============================================================

def extract_guest_name(
    reservation: dict,
    payload: dict,
):

    conversation = payload.get("conversation", {})

    if isinstance(conversation, dict):

        meta = conversation.get("meta", {})

        if isinstance(meta, dict):

            name = meta.get("guestName")

            if name:
                return name.split()[0]

    guest = reservation.get("guest")

    if isinstance(guest, dict):

        first_name = (
            guest.get("firstName")
            or guest.get("first_name")
        )

        if first_name:
            return first_name

    return ""


# ============================================================
# MESSAGE DU VOYAGEUR
# ============================================================

def extract_webhook_message(
    payload: dict,
) -> str:

    message = payload.get("message")

    if not isinstance(message, dict):
        return ""

    message_type = message.get("type", "")

    # On veut uniquement les messages reçus
    if message_type in (
        "fromHost",
        "fromGuesty",
    ):
        return ""

    body = (
        message.get("body")
        or message.get("text")
        or message.get("message")
        or ""
    )

    return clean_message(body)


async def latest_guest_message(
    conversation_id: str,
) -> str:

    data = await guesty_get(
        f"/communication/conversations/"
        f"{conversation_id}/posts"
    )

    posts = []

    if isinstance(data, list):

        posts = data

    elif isinstance(data, dict):

        for key in (
            "posts",
            "results",
            "items",
            "data",
        ):

            value = data.get(key)

            if isinstance(value, list):

                posts = value
                break

    # Du plus récent au plus ancien
    for post in reversed(posts):

        if not isinstance(post, dict):
            continue

        message_type = post.get("type", "")

        if message_type in (
            "fromHost",
            "fromGuesty",
        ):
            continue

        body = (
            post.get("body")
            or post.get("text")
            or post.get("message")
        )

        body = clean_message(body or "")

        if body:
            return body

    return ""


# ============================================================
# OPENAI
# ============================================================

def generate_reply(
    guest_name: str,
    guest_message: str,
    property_info: dict,
    keybox_authorized: bool = False,
) -> str:

    context = f"""
PROPERTY INFORMATION:

Property name:
{property_info["name"]}

Address:
{property_info["address"]}

Check-in:
{property_info["check_in"]}

Check-out:
{property_info["check_out"]}

Floor:
{property_info["floor"]}

Elevator:
{"Yes" if property_info["elevator"] else "No"}

Access:
{property_info["access"]}

Luggage:
{property_info["luggage"]}

Bedrooms:
{property_info["bedrooms"]}

Bathrooms:
{property_info["bathrooms"]}

Toilets:
{property_info["toilets"]}

Kitchen:
{property_info["kitchen"]}

Air conditioning:
{property_info["air_conditioning"]}

Key box:
{property_info["keybox"]}

Key box code:
{property_info["keybox_code"] if keybox_authorized else "DO NOT DISCLOSE"}

Wi-Fi:
{property_info["wifi"]}

Guest first name:
{guest_name if guest_name else "Unknown"}

TRAVELER MESSAGE:

{guest_message}

Write ONLY the message that should be sent to the traveler.

If human intervention is required, write exactly:
ESCALATE
"""

    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        instructions=SYSTEM_RULES,
        input=context,
    )

    return response.output_text.strip()


# ============================================================
# ENVOI
# ============================================================

async def send_reply(
    conversation_id: str,
    reply: str,
    module_type: str = "airbnb2",
):

    if TEST_MODE:

        log.warning(
            "======================================"
        )

        log.warning(
            "TEST MODE - MESSAGE NOT SENT"
        )

        log.warning(
            "AI WOULD REPLY: %s",
            reply,
        )

        log.warning(
            "======================================"
        )

        return

    # TEST_MODE doit être désactivé explicitement
    # avant qu'un message puisse partir.

    await guesty_post(
        f"/communication/conversations/"
        f"{conversation_id}/send-message",
        {
            "module": {
                "type": module_type
            },
            "body": reply,
        },
    )


# ============================================================
# TRAITEMENT DU WEBHOOK
# ============================================================

async def process_message(
    payload: dict,
):

    try:

        event = payload.get("event")

        if event != "reservation.messageReceived":

            log.info(
                "Ignored event: %s",
                event,
            )

            return

        log.info(
            "Incoming Guesty message received"
        )

        # ----------------------------------------
        # RESERVATION ID
        # ----------------------------------------

        reservation_id = payload.get(
            "reservationId"
        )

        if not reservation_id:

            reservation_id = deep_find(
                payload,
                {
                    "reservationId",
                },
            )

        if not reservation_id:

            log.warning(
                "No reservation ID - ignored"
            )

            return

        log.info(
            "Reservation ID: %s",
            reservation_id,
        )

        # ----------------------------------------
        # MESSAGE
        # ----------------------------------------

        guest_message = extract_webhook_message(
            payload
        )

        # ----------------------------------------
        # RESERVATION
        # ----------------------------------------

        reservation = await get_reservation(
            reservation_id
        )

        if not reservation:

            log.warning(
                "Reservation not found"
            )

            return

        # ----------------------------------------
        # LOGEMENT
        # ----------------------------------------

        listing_id = extract_listing_id(
            reservation
        )

        if not listing_id:

            log.warning(
                "Unknown listing - ignored"
            )

            return

        property_info = PROPERTIES.get(
            listing_id
        )

        if not property_info:

            log.warning(
                "Property not configured - ignored"
            )

            return

        log.info(
            "Property identified: %s",
            property_info["name"],
        )

        # ----------------------------------------
        # CONVERSATION
        # ----------------------------------------

        conversation_id = (
            extract_conversation_id(
                reservation,
                payload,
            )
        )

        if not conversation_id:

            log.warning(
                "No conversation ID - ignored"
            )

            return

        log.info(
            "Conversation ID found"
        )

        # ----------------------------------------
        # MESSAGE FALLBACK
        # ----------------------------------------

        if not guest_message:

            guest_message = (
                await latest_guest_message(
                    conversation_id
                )
            )

        if not guest_message:

            log.warning(
                "No guest message found"
            )

            return

        log.info(
            "Guest message: %s",
            guest_message[:500],
        )

        # ----------------------------------------
        # NOM
        # ----------------------------------------

        guest_name = extract_guest_name(
            reservation,
            payload,
        )

        # ----------------------------------------
        # SECURITE CODE BOITE A CLES
        # ----------------------------------------

        # Pour le moment on NE donne jamais automatiquement
        # le code de la boîte à clés.
        # On ajoutera ensuite la vérification des dates/statut.
        keybox_authorized = False

        # ----------------------------------------
        # GENERATION IA
        # ----------------------------------------

        reply = generate_reply(
            guest_name=guest_name,
            guest_message=guest_message,
            property_info=property_info,
            keybox_authorized=keybox_authorized,
        )

        if not reply:

            log.warning(
                "OpenAI returned empty reply"
            )

            return

        # ----------------------------------------
        # ESCALADE
        # ----------------------------------------

        if reply.strip().upper() == "ESCALATE":

            log.warning(
                "HUMAN ESCALATION REQUIRED"
            )

            return

        # ----------------------------------------
        # MODULE
        # ----------------------------------------

        module_type = "airbnb2"

        conversation = payload.get(
            "conversation",
            {},
        )

        if isinstance(conversation, dict):

            integration = conversation.get(
                "integration",
                {},
            )

            if isinstance(integration, dict):

                platform = integration.get(
                    "platform",
                    "",
                )

                if platform:
                    log.info(
                        "Conversation platform: %s",
                        platform,
                    )

        # ----------------------------------------
        # ENVOI / TEST
        # ----------------------------------------

        await send_reply(
            conversation_id,
            reply,
            module_type,
        )

    except Exception:

        log.exception(
            "ERROR WHILE PROCESSING MESSAGE"
        )


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
async def root():

    return {
        "status": "Airbnb AI Agent running",
        "test_mode": TEST_MODE,
    }


@app.get("/health")
async def health():

    return {
        "ok": True,
        "test_mode": TEST_MODE,
    }


# ============================================================
# CREATION WEBHOOK
# NE PAS RELANCER SI LE WEBHOOK EXISTE DEJA
# ============================================================

@app.get("/setup-webhook")
async def setup_webhook():

    result = await guesty_post(
        "/webhooks",
        {
            "url": RENDER_WEBHOOK_URL,
            "events": [
                "reservation.messageReceived"
            ],
        },
    )

    return {
        "message":
        "Webhook creation request sent",
        "result": result,
    }


# ============================================================
# RECEPTION GUESTY
# ============================================================

@app.post("/guesty/webhook")
async def guesty_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):

    payload = await request.json()

    # IMPORTANT :
    # On ne log plus tout le payload pour éviter
    # d'enregistrer les données personnelles du voyageur.

    log.info(
        "Guesty webhook received - event=%s",
        payload.get("event"),
    )

    background_tasks.add_task(
        process_message,
        payload,
    )

    return {
        "received": True
    }
