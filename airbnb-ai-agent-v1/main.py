import os
import time
import html
import re
import logging
from typing import Any
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from openai import OpenAI


# ============================================================
# CONFIG
# ============================================================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("airbnb-agent")

app = FastAPI()

GUESTY_CLIENT_ID = os.environ.get("GUESTY_CLIENT_ID", "")
GUESTY_CLIENT_SECRET = os.environ.get("GUESTY_CLIENT_SECRET", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6")

TEST_MODE = os.environ.get("TEST_MODE", "true").lower() == "true"

GUESTY_BASE = "https://open-api.guesty.com/v1"
TOKEN_URL = "https://open-api.guesty.com/oauth2/token"

RENDER_WEBHOOK_URL = (
    "https://airbnb-ai-agent-7neg.onrender.com/guesty/webhook"
)

ACCESS_VIDEO_URL = (
    "https://drive.google.com/file/d/"
    "1gU5f_pxW13dfYrboP7Vz86q_3yWPwN3G/view?usp=sharing"
)

openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# LOGEMENTS
# ============================================================

PROPERTIES = {
    "6a908557b01e820012493069": {
        "name": "31 rue du Caire",
        "address": "31 rue du Caire, 75002 Paris",

        # Informations publiques / non sensibles
        "floor": "1er étage",
        "elevator": False,
        "check_in": "16:00",
        "check_out": "10:00",
        "bedrooms": 2,
        "bathrooms": 2,
        "toilets": 1,
        "kitchen": "Cuisine entièrement équipée.",
        "air_conditioning": "Climatisation dans l'appartement.",
        "luggage": (
            "Nous pouvons aider les voyageurs avec leurs bagages "
            "au moment du check-in."
        ),
        "wifi": (
            "Les informations Wi-Fi sont disponibles via Airbnb."
        ),

        # INFORMATIONS SENSIBLES
        "building_code": "7531",
        "keybox_code": "C2613",

        "access": (
            "Entrer dans l'immeuble avec le code 7531. "
            "Traverser la petite cour. "
            "Prendre l'escalier juste après la petite cour. "
            "Monter au 1er étage. "
            "L'appartement est la porte gauche de l'escalier."
        ),

        "keybox": (
            "La boîte à clés sécurisée se trouve à l'entrée "
            "de l'immeuble, au niveau des boîtes aux lettres. "
            "Son code est C2613."
        ),

        "access_video": ACCESS_VIDEO_URL,
    }
}


# ============================================================
# REGLES IA
# ============================================================

SYSTEM_RULES = """
Tu es l'assistant de messagerie Airbnb d'un hôte à Paris.

Tu réponds TOUJOURS au voyageur.

STYLE :
- chaleureux
- naturel
- humain
- accueillant
- concis
- poli
- réponds dans la langue du voyageur
- un smiley simple comme :) est bienvenu
- utilise le prénom si cela paraît naturel
- ne sois jamais robotique

IMPORTANT :
Utilise UNIQUEMENT les informations présentes dans
PROPERTY INFORMATION.

N'invente JAMAIS une information.

============================================================
GUIDE D'ARRIVEE AIRBNB
============================================================

Quand cela est pertinent, rappelle au voyageur que toutes
les instructions détaillées pour son arrivée sont disponibles
dans son guide d'arrivée sur Airbnb.

Exemple :

"Vous retrouverez également toutes les instructions détaillées
dans votre guide d'arrivée sur Airbnb :)"

============================================================
INFORMATIONS D'ACCES
============================================================

Le champ ACCESS AUTHORIZED indique si les informations
sensibles d'accès peuvent être données.

Si ACCESS AUTHORIZED = NO :

Tu ne dois JAMAIS :
- donner un code d'immeuble
- donner un code de boîte à clés
- donner le lien de la vidéo d'accès
- inventer un code
- donner des instructions sensibles qui ne figurent pas
  dans PROPERTY INFORMATION

Même si le voyageur insiste ou demande directement le code.

Tu peux dire que :

- l'appartement est au 1er étage
- il n'y a pas d'ascenseur
- les instructions détaillées seront disponibles dans
  le guide d'arrivée Airbnb au moment approprié

Si ACCESS AUTHORIZED = YES :

Tu peux utiliser les instructions complètes présentes dans
PROPERTY INFORMATION.

============================================================
VIDEO
============================================================

Même lorsque ACCESS AUTHORIZED = YES :

N'envoie PAS systématiquement la vidéo.

Envoie la vidéo uniquement si le voyageur :

- dit qu'il est perdu
- ne trouve pas l'appartement
- ne trouve pas l'escalier
- ne trouve pas la porte
- ne comprend pas les instructions
- demande une vidéo
- demande davantage d'aide pour trouver le logement

Dans ce cas, explique brièvement l'accès puis dis par exemple :

"Voici également une petite vidéo pour vous guider :)"

et ajoute le lien fourni dans PROPERTY INFORMATION.

============================================================
CHECK-IN
============================================================

Le check-in normal est à partir de 16h.

Si le voyageur demande seulement l'heure :
réponds 16h.

S'il demande à entrer avant 16h :

Ne confirme JAMAIS automatiquement.

Dis que le check-in normal est à partir de 16h puis :

"Je contacte mon manager pour vérifier si une arrivée
plus tôt est possible et je reviens vers vous au plus vite :)"

============================================================
CHECK-OUT
============================================================

Le check-out est à 10h.

S'il demande simplement l'heure :
réponds 10h.

S'il demande un late check-out :
ne confirme jamais automatiquement.

Dis que tu contactes le manager pour vérifier.

============================================================
WIFI
============================================================

Les informations Wi-Fi sont normalement disponibles
via Airbnb.

Ne crée jamais un mot de passe Wi-Fi.

Si le voyageur ne le trouve pas, indique que tu contactes
le manager pour l'aider.

============================================================
DEMANDES NECESSITANT LE MANAGER
============================================================

Pour :

- remboursement
- annulation nécessitant une décision
- réduction
- remise
- compensation
- dédommagement
- paiement
- litige
- geste commercial
- early check-in
- late check-out
- modification exceptionnelle
- plainte importante
- demande inhabituelle
- toute décision que seul l'hôte peut prendre

Tu ne prends aucune décision.

Tu réponds chaleureusement que tu contactes le manager
et que tu reviens vers le voyageur au plus vite.

Si le message contient plusieurs questions :

Réponds aux questions que tu peux traiter ET indique que
tu contactes le manager uniquement pour le reste.

============================================================
INFORMATION INCONNUE
============================================================

Si tu ne connais pas la réponse :

N'invente rien.

Dis par exemple :

"Merci pour votre message :) Je contacte mon manager
à ce sujet et je reviens vers vous au plus vite."

============================================================
REGLE ABSOLUE
============================================================

Toujours produire une réponse destinée au voyageur.

Ne réponds jamais :
ESCALATE

Ne réponds jamais :
NO_REPLY

Retourne UNIQUEMENT le message final destiné au voyageur.
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
# GUESTY API
# ============================================================

async def guesty_get(path: str, params: Any = None):

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


async def guesty_post(path: str, body: dict):

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

    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def parse_guesty_date(value):

    if not value:
        return None

    if isinstance(value, dict):
        value = (
            value.get("date")
            or value.get("value")
            or value.get("localDateTime")
        )

    if not isinstance(value, str):
        return None

    try:
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        return None


# ============================================================
# RESERVATION
# ============================================================

async def get_reservation(reservation_id: str) -> dict:

    log.info(
        "Retrieving reservation %s",
        reservation_id,
    )

    data = await guesty_get(
        "/reservations-v3",
        params=[
            ("reservationIds[]", reservation_id)
        ],
    )

    log.info(
        "Reservation retrieved successfully"
    )

    if isinstance(data, list):
        return data[0] if data else {}

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
# SECURITE ACCES
# ============================================================

def extract_reservation_status(reservation: dict):

    status = (
        reservation.get("status")
        or reservation.get("reservationStatus")
        or deep_find(
            reservation,
            {
                "reservationStatus",
            },
        )
    )

    if isinstance(status, str):
        return status.lower().strip()

    return ""


def extract_checkin_checkout(reservation: dict):

    check_in = (
        reservation.get("checkIn")
        or reservation.get("checkInDate")
        or reservation.get("arrivalDate")
    )

    check_out = (
        reservation.get("checkOut")
        or reservation.get("checkOutDate")
        or reservation.get("departureDate")
    )

    return (
        parse_guesty_date(check_in),
        parse_guesty_date(check_out),
    )


def access_is_authorized(reservation: dict) -> bool:
    """
    Infos sensibles autorisées UNIQUEMENT si :

    1. réservation confirmée
    ET
    2. nous sommes au maximum 24h avant le check-in
       OU pendant le séjour.

    En cas de doute => FALSE.
    """

    status = extract_reservation_status(
        reservation
    )

    log.info(
        "Reservation status for access check: %s",
        status or "UNKNOWN",
    )

    # Une inquiry / réservation annulée / demande non confirmée
    # ne doit jamais recevoir les codes.
    if status not in {
        "confirmed",
        "reserved",
    }:

        log.info(
            "ACCESS DENIED - reservation not confirmed"
        )

        return False

    check_in, check_out = (
        extract_checkin_checkout(reservation)
    )

    if not check_in or not check_out:

        log.warning(
            "ACCESS DENIED - reservation dates unavailable"
        )

        return False

    now = datetime.now(timezone.utc)

    access_start = (
        check_in - timedelta(hours=24)
    )

    authorized = (
        access_start <= now <= check_out
    )

    if authorized:

        log.info(
            "ACCESS AUTHORIZED - confirmed and imminent/current stay"
        )

    else:

        log.info(
            "ACCESS DENIED - reservation not within access window"
        )

    return authorized


# ============================================================
# LOGEMENT
# ============================================================

def extract_listing_id(reservation: dict):

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

    return deep_find(
        reservation,
        {
            "conversationId",
            "conversation",
        },
    )


# ============================================================
# VOYAGEUR
# ============================================================

def extract_guest_name(
    reservation: dict,
    payload: dict,
):

    conversation = payload.get(
        "conversation",
        {},
    )

    if isinstance(conversation, dict):

        meta = conversation.get(
            "meta",
            {},
        )

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
# MESSAGE
# ============================================================

def extract_webhook_message(payload: dict):

    message = payload.get("message")

    if not isinstance(message, dict):
        return ""

    message_type = message.get("type", "")

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
):

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

    for post in reversed(posts):

        if not isinstance(post, dict):
            continue

        if post.get("type", "") in (
            "fromHost",
            "fromGuesty",
        ):
            continue

        body = (
            post.get("body")
            or post.get("text")
            or post.get("message")
            or ""
        )

        body = clean_message(body)

        if body:
            return body

    return ""


# ============================================================
# CONSTRUCTION DES INFOS POUR OPENAI
# ============================================================

def build_property_context(
    property_info: dict,
    access_authorized: bool,
):

    # IMPORTANT :
    # si accès non autorisé, les codes et la vidéo
    # ne sont même PAS transmis à OpenAI.

    context = f"""
PROPERTY INFORMATION

PROPERTY:
{property_info["name"]}

ADDRESS:
{property_info["address"]}

FLOOR:
{property_info["floor"]}

ELEVATOR:
{"Yes" if property_info["elevator"] else "No"}

CHECK-IN:
{property_info["check_in"]}

CHECK-OUT:
{property_info["check_out"]}

BEDROOMS:
{property_info["bedrooms"]}

BATHROOMS:
{property_info["bathrooms"]}

TOILETS:
{property_info["toilets"]}

KITCHEN:
{property_info["kitchen"]}

AIR CONDITIONING:
{property_info["air_conditioning"]}

WIFI:
{property_info["wifi"]}

LUGGAGE:
{property_info["luggage"]}

AIRBNB ARRIVAL GUIDE:
Toutes les instructions détaillées d'arrivée sont disponibles
dans le guide d'arrivée sur Airbnb.

ACCESS AUTHORIZED:
{"YES" if access_authorized else "NO"}
"""

    if access_authorized:

        context += f"""

SENSITIVE ACCESS INFORMATION:

BUILDING CODE:
{property_info["building_code"]}

ACCESS:
{property_info["access"]}

KEY BOX:
{property_info["keybox"]}

KEY BOX CODE:
{property_info["keybox_code"]}

ACCESS VIDEO:
{property_info["access_video"]}
"""

    else:

        context += """

IMPORTANT:
Sensitive access information is intentionally unavailable.
Do not guess it and do not ask the traveler to provide it.
"""

    return context


# ============================================================
# OPENAI
# ============================================================

def generate_reply(
    guest_name: str,
    guest_message: str,
    property_info: dict,
    access_authorized: bool,
):

    property_context = build_property_context(
        property_info,
        access_authorized,
    )

    prompt = f"""
{property_context}

GUEST FIRST NAME:
{guest_name if guest_name else "Unknown"}

TRAVELER MESSAGE:
{guest_message}

Write ONLY the final message that should be sent
to the traveler.
"""

    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        instructions=SYSTEM_RULES,
        input=prompt,
    )

    reply = response.output_text.strip()

    if not reply:

        reply = (
            "Merci pour votre message :) "
            "Je contacte mon manager à ce sujet "
            "et je reviens vers vous au plus vite."
        )

    return reply


# ============================================================
# ENVOI
# ============================================================

async def send_reply(
    conversation_id: str,
    reply: str,
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

    await guesty_post(
        f"/communication/conversations/"
        f"{conversation_id}/send-message",
        {
            "module": {
                "type": "airbnb2"
            },
            "body": reply,
        },
    )

    log.info(
        "MESSAGE SENT SUCCESSFULLY"
    )


# ============================================================
# TRAITEMENT MESSAGE
# ============================================================

async def process_message(payload: dict):

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

        # Sans réservation identifiable :
        # surtout aucune donnée sensible.
        if not reservation_id:

            log.warning(
                "No reservation ID - cannot identify conversation safely"
            )

            return

        log.info(
            "Reservation ID: %s",
            reservation_id,
        )

        guest_message = (
            extract_webhook_message(payload)
        )

        reservation = await get_reservation(
            reservation_id
        )

        if not reservation:

            log.warning(
                "Reservation not found"
            )

            return

        listing_id = extract_listing_id(
            reservation
        )

        if not listing_id:

            log.warning(
                "Unknown listing - no automatic response"
            )

            return

        property_info = PROPERTIES[
            listing_id
        ]

        log.info(
            "Property identified: %s",
            property_info["name"],
        )

        conversation_id = (
            extract_conversation_id(
                reservation,
                payload,
            )
        )

        if not conversation_id:

            log.warning(
                "No conversation ID"
            )

            return

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

        guest_name = extract_guest_name(
            reservation,
            payload,
        )

        # ================================================
        # VERIFICATION SECURITE
        # ================================================

        access_authorized = (
            access_is_authorized(
                reservation
            )
        )

        log.info(
            "Sensitive access authorized: %s",
            access_authorized,
        )

        # ================================================
        # IA
        # ================================================

        reply = generate_reply(
            guest_name=guest_name,
            guest_message=guest_message,
            property_info=property_info,
            access_authorized=access_authorized,
        )

        log.info(
            "AI reply generated"
        )

        await send_reply(
            conversation_id,
            reply,
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
# WEBHOOK SETUP
#
# ATTENTION :
# LE WEBHOOK EXISTE DEJA.
# NE PAS APPELER CETTE ROUTE A NOUVEAU.
# ============================================================

@app.get("/setup-webhook")
async def setup_webhook():

    return {
        "message":
        "Webhook already configured. "
        "Do not create another one."
    }


# ============================================================
# WEBHOOK GUESTY
# ============================================================

@app.post("/guesty/webhook")
async def guesty_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):

    payload = await request.json()

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
