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

app = FastAPI(title="Airbnb AI Agent")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GUESTY_CLIENT_ID = os.environ.get("GUESTY_CLIENT_ID", "")
GUESTY_CLIENT_SECRET = os.environ.get("GUESTY_CLIENT_SECRET", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6")
TEST_MODE = os.environ.get("TEST_MODE", "true").lower() == "true"

openai_client = OpenAI(api_key=OPENAI_API_KEY)

GUESTY_BASE = "https://open-api.guesty.com/v1"
TOKEN_URL = "https://open-api.guesty.com/oauth2/token"

RENDER_WEBHOOK_URL = (
    "https://airbnb-ai-agent-7neg.onrender.com/guesty/webhook"
)

_token = {
    "value": None,
    "expires_at": 0,
}


# ============================================================
# LOGEMENTS
# ============================================================

PROPERTIES = {

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

            "Une vidéo montrant l'accès à l'appartement existe dans les ressources internes.",
        ],

        "arrival_instructions": (
            "Adresse : 31 rue du Caire, 75002 Paris. "
            "À l'entrée de l'immeuble, la boîte à clés sécurisée "
            "se trouve dans la boîte aux lettres (code C2613). "
            "Après être entré, traverser la cour puis emprunter "
            "le deuxième escalier. "
            "L'appartement est au 1er étage."
        ),
    }
}


# ============================================================
# INSTRUCTIONS DE L'AGENT IA
# ============================================================

SYSTEM_RULES = """
Tu es l'assistant de messagerie d'un hôte Airbnb à Paris.

RÈGLES ABSOLUES

- Tu réponds UNIQUEMENT après un message entrant d'un voyageur.

- Réponds uniquement à partir des informations du logement
  fournies dans le contexte.

- N'invente JAMAIS une information.

- Si l'information manque ou si tu as un doute,
  retourne exactement :

ESCALATE

- Pour remboursement, annulation, réduction, compensation,
  litige, paiement, modification de réservation,
  urgence médicale ou de sécurité, menace, plainte sérieuse
  ou demande inhabituelle :

ESCALATE

- Ne promets jamais un early check-in ou un late check-out
  si le contexte ne l'autorise pas explicitement.

- Ne révèle jamais les informations d'un autre logement.

- Réponds seulement à la question posée.

- Les messages automatiques Airbnb existent déjà.
  Ne les répète pas inutilement.

STYLE

- Chaleureux.
- Naturel.
- Sympathique.
- Concis.
- Utilise des smileys comme :)
- Pas de ton robotique.

Commence normalement par :

Bonjour [Prénom], merci pour votre message :)

Utilise le prénom si disponible.

Termine naturellement, par exemple :

Au plaisir de vous recevoir !

ou

N'hésitez pas si vous avez besoin de quoi que ce soit :)

Réponds dans la langue utilisée par le voyageur.
"""


# ============================================================
# OUTILS
# ============================================================

def clean_message(body: str) -> str:

    if not body:
        return ""

    body = html.unescape(body)

    body = re.sub(
        r"<[^>]+>",
        " ",
        body,
    )

    body = re.sub(
        r"\s+",
        " ",
        body,
    ).strip()

    return body


# ============================================================
# AUTHENTIFICATION GUESTY
# ============================================================

async def guesty_token() -> str:

    now = time.time()

    if (
        _token["value"]
        and now < _token["expires_at"] - 3600
    ):
        return _token["value"]

    if not GUESTY_CLIENT_ID:
        raise RuntimeError(
            "GUESTY_CLIENT_ID missing"
        )

    if not GUESTY_CLIENT_SECRET:
        raise RuntimeError(
            "GUESTY_CLIENT_SECRET missing"
        )

    async with httpx.AsyncClient(
        timeout=20
    ) as client:

        response = await client.post(

            TOKEN_URL,

            headers={
                "Accept": "application/json",
                "Content-Type":
                    "application/x-www-form-urlencoded",
            },

            data={
                "grant_type":
                    "client_credentials",

                "scope":
                    "open-api",

                "client_id":
                    GUESTY_CLIENT_ID,

                "client_secret":
                    GUESTY_CLIENT_SECRET,
            },
        )

        response.raise_for_status()

        data = response.json()

    _token["value"] = data["access_token"]

    _token["expires_at"] = (
        now
        + int(
            data.get(
                "expires_in",
                86400,
            )
        )
    )

    return _token["value"]


# ============================================================
# REQUÊTES GUESTY
# ============================================================

async def guesty_get(
    path: str,
    params=None,
) -> Any:

    token = await guesty_token()

    async with httpx.AsyncClient(
        timeout=25
    ) as client:

        response = await client.get(

            f"{GUESTY_BASE}{path}",

            headers={
                "Authorization":
                    f"Bearer {token}",

                "Accept":
                    "application/json",
            },

            params=params,
        )

        response.raise_for_status()

        return response.json()


async def guesty_post(
    path: str,
    payload: dict,
) -> Any:

    token = await guesty_token()

    async with httpx.AsyncClient(
        timeout=25
    ) as client:

        response = await client.post(

            f"{GUESTY_BASE}{path}",

            headers={
                "Authorization":
                    f"Bearer {token}",

                "Accept":
                    "application/json",

                "Content-Type":
                    "application/json",
            },

            json=payload,
        )

        response.raise_for_status()

        if response.content:
            return response.json()

        return {}


# ============================================================
# RECHERCHE D'INFORMATIONS
# ============================================================

def deep_find(
    obj: Any,
    keys: set[str],
):

    if isinstance(obj, dict):

        for key, value in obj.items():

            if key in keys and value:

                if isinstance(value, dict):

                    for subkey in (
                        "_id",
                        "id",
                    ):

                        if value.get(subkey):
                            return value[subkey]

                else:
                    return value

        for value in obj.values():

            found = deep_find(
                value,
                keys,
            )

            if found:
                return found

    elif isinstance(obj, list):

        for value in obj:

            found = deep_find(
                value,
                keys,
            )

            if found:
                return found

    return None


# ============================================================
# RÉSERVATION
# ============================================================

async def get_reservation(
    reservation_id: str,
) -> dict:

    data = await guesty_get(

        "/reservations-v3",

        params=[
            (
                "reservationIds",
                reservation_id,
            )
        ],
    )

    if isinstance(data, list):

        if data:
            return data[0]

        return {}

    for key in (
        "data",
        "results",
        "reservations",
    ):

        if (
            isinstance(data, dict)
            and isinstance(
                data.get(key),
                list,
            )
            and data[key]
        ):

            return data[key][0]

    if isinstance(data, dict):
        return data

    return {}


def extract_listing_id(
    reservation: dict,
):

    return deep_find(
        reservation,
        {
            "listingId",
            "listing",
        },
    )


def extract_conversation_id(
    reservation: dict,
    payload: dict,
):

    conversation_id = deep_find(

        reservation,

        {
            "conversationId",
            "conversation",
        },
    )

    if conversation_id:
        return conversation_id

    conversation = (
        payload.get("conversation")
        or {}
    )

    return (
        conversation.get("_id")
        or conversation.get("id")
    )


def extract_guest_name(
    reservation: dict,
    payload: dict,
) -> str:

    conversation = (
        payload.get("conversation")
        or {}
    )

    meta = (
        conversation.get("meta")
        or {}
    )

    name = meta.get("guestName")

    if name:
        return str(name).split()[0]

    guest = (
        reservation.get("guest")
        if isinstance(
            reservation,
            dict,
        )
        else None
    )

    if isinstance(guest, dict):

        for key in (
            "firstName",
            "fullname",
            "fullName",
        ):

            if guest.get(key):

                return (
                    str(
                        guest[key]
                    )
                    .split()[0]
                )

    # Important :
    # vide plutôt que "Bonjour"
    # pour éviter "Bonjour Bonjour"
    return ""


# ============================================================
# RÉCUPÉRER LE DERNIER MESSAGE DU VOYAGEUR
# ============================================================

async def latest_guest_message(
    conversation_id: str,
    payload: dict,
) -> str:

    try:

        posts = await guesty_get(

            f"/communication/conversations/"
            f"{conversation_id}/posts",

            params={
                "sort":
                    "-createdAt",

                "limit":
                    20,
            },
        )

        if isinstance(posts, list):

            candidates = posts

        else:

            candidates = (
                posts.get("data")
                or posts.get("results")
                or posts.get("posts")
                or []
            )

        for post in candidates:

            if not isinstance(
                post,
                dict,
            ):
                continue

            message_type = str(
                post.get(
                    "type",
                    "",
                )
            ).lower()

            sent_by = str(
                post.get(
                    "sentBy",
                    "",
                )
            ).lower()

            if (
                "guest" in message_type
                or sent_by == "guest"
                or message_type
                    == "fromthirdparty"
            ):

                return clean_message(
                    post.get(
                        "body",
                        "",
                    )
                )

    except Exception:

        log.exception(
            "Could not fetch conversation posts"
        )

    message = (
        payload.get("message")
        or {}
    )

    return clean_message(
        message.get(
            "body",
            "",
        )
    )


# ============================================================
# OPENAI
# ============================================================

def generate_reply(
    first_name: str,
    property_data: dict,
    guest_message: str,
) -> str:

    guest_name_text = (
        first_name
        if first_name
        else "non disponible"
    )

    context = f"""
VOYAGEUR

Prénom :
{guest_name_text}


LOGEMENT

Nom :
{property_data["name"]}

Adresse :
{property_data["address"]}

Check-in :
à partir de {property_data["check_in"]}

Check-out :
avant {property_data["check_out"]}


INFORMATIONS FIABLES

- """ + "\n- ".join(
        property_data["facts"]
    ) + f"""


INSTRUCTIONS D'ACCÈS FIABLES

{property_data["arrival_instructions"]}


MESSAGE DU VOYAGEUR

{guest_message}
"""

    response = (
        openai_client.responses.create(

            model=OPENAI_MODEL,

            instructions=SYSTEM_RULES,

            input=context,
        )
    )

    return (
        response.output_text
        .strip()
    )


# ============================================================
# ENVOYER UNE RÉPONSE
# ============================================================

async def send_reply(
    conversation_id: str,
    reply: str,
    incoming_module: Any,
):

    # TEST MODE :
    # aucune réponse réelle n'est envoyée.

    if TEST_MODE:

        log.warning(
            "TEST_MODE reply for %s: %s",
            conversation_id,
            reply,
        )

        return

    if isinstance(
        incoming_module,
        dict,
    ):

        module = incoming_module

    else:

        module = {
            "type":
                "platform"
        }

    await guesty_post(

        f"/communication/conversations/"
        f"{conversation_id}/send-message",

        {
            "module":
                module,

            "body":
                reply,
        },
    )


# ============================================================
# TRAITEMENT D'UN MESSAGE ENTRANT
# ============================================================

async def process_message(
    payload: dict,
):

    try:

        if (
            payload.get("event")
            !=
            "reservation.messageReceived"
        ):
            return

        message = (
            payload.get("message")
            or {}
        )

        message_type = str(
            message.get(
                "type",
                "",
            )
        ).lower()

        # On ignore les messages
        # qui ne viennent pas du voyageur.

        if (
            message_type
            and message_type
            not in {
                "fromguest",
                "fromthirdparty",
            }
        ):

            log.info(
                "Ignored message type: %s",
                message_type,
            )

            return

        reservation_id = (
            payload.get(
                "reservationId"
            )
        )

        if not reservation_id:

            log.warning(
                "No reservationId - ignored"
            )

            return

        reservation = (
            await get_reservation(
                reservation_id
            )
        )

        listing_id = (
            extract_listing_id(
                reservation
            )
        )

        # Sécurité :
        # aucun autre logement
        # n'est autorisé.

        if listing_id not in PROPERTIES:

            log.warning(
                "Unknown listing %s - NO AUTO REPLY",
                listing_id,
            )

            return

        conversation_id = (
            extract_conversation_id(
                reservation,
                payload,
            )
        )

        if not conversation_id:

            log.warning(
                "No conversation ID - NO AUTO REPLY"
            )

            return

        guest_message = (
            await latest_guest_message(
                conversation_id,
                payload,
            )
        )

        if not guest_message:

            log.warning(
                "Empty guest message - ignored"
            )

            return

        first_name = (
            extract_guest_name(
                reservation,
                payload,
            )
        )

        reply = generate_reply(

            first_name,

            PROPERTIES[
                listing_id
            ],

            guest_message,
        )

        if (
            reply.strip()
            == "ESCALATE"
        ):

            log.warning(
                "ESCALATE reservation=%s message=%s",
                reservation_id,
                guest_message,
            )

            return

        await send_reply(

            conversation_id,

            reply,

            message.get(
                "module"
            ),
        )

    except Exception:

        log.exception(
            "Message processing failed"
        )


# ============================================================
# PAGE D'ACCUEIL
# ============================================================

@app.get("/")
async def root():

    return {

        "status":
            "ok",

        "service":
            "airbnb-ai-agent",

        "test_mode":
            TEST_MODE,

        "known_properties":
            len(PROPERTIES),
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():

    return {

        "ok":
            True,

        "test_mode":
            TEST_MODE,
    }


# ============================================================
# CRÉER LE WEBHOOK GUESTY
# ============================================================

@app.get("/setup-webhook")
async def setup_webhook():

    try:

        token = await guesty_token()

        async with httpx.AsyncClient(
            timeout=30
        ) as client:

            response = await client.post(

                f"{GUESTY_BASE}/webhooks",

                headers={
                    "Authorization":
                        f"Bearer {token}",

                    "Accept":
                        "application/json",

                    "Content-Type":
                        "application/json",
                },

                json={
                    "url":
                        RENDER_WEBHOOK_URL,

                    "events": [
                        "reservation.messageReceived"
                    ],
                },
            )

        return {

            "status_code":
                response.status_code,

            "response":
                response.text,
        }

    except Exception as error:

        log.exception(
            "Webhook setup failed"
        )

        return {

            "status":
                "error",

            "error":
                str(error),
        }


# ============================================================
# WEBHOOK RECEVANT LES MESSAGES GUESTY
# ============================================================

@app.post("/guesty/webhook")
async def guesty_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):

    payload = await request.json()

    log.info(
        "Guesty webhook received"
    )

    # On répond immédiatement 200 à Guesty
    # puis le message est traité en arrière-plan.

    background_tasks.add_task(
        process_message,
        payload,
    )

    return {
        "received":
            True
    }
