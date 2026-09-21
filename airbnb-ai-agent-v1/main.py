import os
import re
import json
import time
import html
import hashlib
import asyncio
from datetime import datetime, date, time as dt_time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI

try:
    from svix.webhooks import Webhook
except ImportError:
    Webhook = None


# ============================================================
# CONFIG
# ============================================================

GUESTY_CLIENT_ID = os.getenv("GUESTY_CLIENT_ID")
GUESTY_CLIENT_SECRET = os.getenv("GUESTY_CLIENT_SECRET")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")
GUESTY_WEBHOOK_SECRET = os.getenv("GUESTY_WEBHOOK_SECRET")

TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"

GUESTY_BASE_URL = "https://open-api.guesty.com/v1"
GUESTY_TOKEN_URL = "https://open-api.guesty.com/oauth2/token"

DEBOUNCE_SECONDS = 15

CONVERSATION_POST_LIMIT = 30

STYLE_CONVERSATION_LIMIT = 6
STYLE_POSTS_PER_CONVERSATION = 10
STYLE_CACHE_TTL = 1800

PROCESSED_EVENT_TTL = 3600


# ============================================================
# OPENAI
# ============================================================

openai_client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    timeout=30.0,
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(title="Airbnb AI Agent")


# ============================================================
# PROPERTIES
# ============================================================

PROPERTIES = {

    "6a908557b01e820012493069": {

        "name": "31 rue du Caire",

        "address": "31 rue du Caire, 75002 Paris",

        "timezone": "Europe/Paris",

        "check_in_time": "16:00",
        "check_out_time": "10:00",

        "floor": "1er étage",

        "elevator": False,

        "bedrooms": 2,
        "bathrooms": 2,
        "wc": 1,

        "fully_equipped_kitchen": True,

        "air_conditioning": True,

        # INFORMATIONS SENSIBLES
        "building_code": "7531",

        "keybox_code": "C2613",

        "access_route": (
            "Entrer dans l'immeuble avec le code 7531, "
            "traverser la petite cour, "
            "prendre l'escalier immédiatement après la petite cour, "
            "monter au 1er étage, "
            "l'appartement est la porte à gauche de l'escalier."
        ),

        "keybox_location": (
            "La boîte à clés se trouve à l'entrée de l'immeuble, "
            "au niveau des boîtes aux lettres."
        ),

        "video_url": (
            "https://drive.google.com/file/d/"
            "1gU5f_pxW13dfYrboP7Vz86q_3yWPwN3G/view?usp=sharing"
        ),

        "arrival_guide": (
            "Toutes les instructions sont disponibles "
            "sur le guide d'arrivée sur Airbnb."
        ),
    }
}


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_RULES = """
Tu es l'assistant Airbnb d'un hôte.

TON OBJECTIF :
Répondre aux voyageurs comme un vrai hôte humain :
naturellement, chaleureusement, intelligemment et de manière concise.

STYLE :
- Réponds dans la langue du voyageur.
- Sois naturel.
- Sois chaleureux.
- Sois utile.
- Évite les réponses robotiques.
- Utilise le prénom quand il est disponible.
- Tu peux utiliser ":)" ou un emoji de temps en temps.
- Ne fais pas de longues listes inutiles.
- Ne répète pas des informations déjà données.

IMPORTANT :
Lis tout l'historique disponible avant de répondre.

Si plusieurs messages récents du voyageur correspondent à la même demande,
réponds à toutes les questions dans UNE SEULE réponse.

NE RÉPONDS PAS séparément à chaque message.

INQUIRIES / AVANT RÉSERVATION :
Une personne qui n'a pas encore réservé doit quand même recevoir une réponse.

L'absence de réservation n'est PAS une raison pour ignorer le message.

Tu peux répondre aux questions générales concernant :
- l'appartement
- l'étage
- l'ascenseur
- les chambres
- les salles de bain
- la cuisine
- la climatisation
- les équipements connus
- le check-in
- le check-out

NE JAMAIS INVENTER une information.

Si une information n'est pas connue :
dis naturellement que tu vas vérifier auprès du manager.

INTELLIGENCE :
Ne réponds pas mécaniquement.

Exemple :

Voyageur :
"Y a-t-il un ascenseur ?"

Ne réponds pas simplement :
"Non."

Réponds plutôt :
"Il n'y a pas d'ascenseur, l'appartement est au 1er étage :)
Si vous avez des bagages, nous pouvons bien sûr vous aider à votre arrivée."

DEMANDES SENSIBLES :

Pour :
- remboursement
- annulation exceptionnelle
- réduction
- compensation
- litige
- paiement
- problème sérieux
- décision commerciale
- problème de sécurité
- urgence médicale

ne prends aucune décision toi-même.

Réponds naturellement que tu vas voir cela avec le manager.

ACCÈS SENSIBLE :

Les codes d'accès et informations détaillées d'accès sont confidentiels.

Ils ne doivent être utilisés que si le serveur indique explicitement :

SENSITIVE_ACCESS_AUTHORIZED = TRUE

Sinon :
- ne donne aucun code ;
- ne donne pas le lien vidéo ;
- ne donne pas le chemin détaillé ;
- tu peux dire que l'appartement est au 1er étage ;
- tu peux dire qu'il n'y a pas d'ascenseur ;
- indique que toutes les instructions sont disponibles sur le guide d'arrivée Airbnb.

VIDÉO :

Ne donne PAS automatiquement la vidéo.

Tu peux donner la vidéo uniquement si :
- le voyageur la demande ;
- il est perdu ;
- il ne trouve pas l'entrée ;
- il ne trouve pas l'escalier ;
- il ne trouve pas l'appartement ;
- il ne comprend pas les instructions d'accès.

Et uniquement si SENSITIVE_ACCESS_AUTHORIZED = TRUE.

CHECK-IN :
À partir de 16h.

CHECK-OUT :
10h.

IMPORTANT :
Une réponse doit toujours répondre au dernier message du voyageur.

Si tu connais une partie de la demande mais pas le reste :
réponds à la partie connue puis indique naturellement que tu vas vérifier le reste.

UNE SEULE RÉPONSE :
Retourne uniquement le message à envoyer au voyageur.
Pas d'analyse.
Pas d'explication.
Pas de commentaire interne.
"""


# ============================================================
# GUESTY TOKEN
# ============================================================

_guesty_token: Optional[str] = None
_guesty_token_expires_at = 0.0


async def get_guesty_token() -> str:

    global _guesty_token
    global _guesty_token_expires_at

    now = time.time()

    if (
        _guesty_token
        and now < _guesty_token_expires_at - 300
    ):
        return _guesty_token

    if not GUESTY_CLIENT_ID:
        raise RuntimeError("GUESTY_CLIENT_ID missing")

    if not GUESTY_CLIENT_SECRET:
        raise RuntimeError("GUESTY_CLIENT_SECRET missing")

    data = {
        "grant_type": "client_credentials",
        "scope": "open-api",
        "client_id": GUESTY_CLIENT_ID,
        "client_secret": GUESTY_CLIENT_SECRET,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:

        response = await client.post(
            GUESTY_TOKEN_URL,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        response.raise_for_status()

        payload = response.json()

    _guesty_token = payload["access_token"]

    _guesty_token_expires_at = (
        time.time()
        + int(payload.get("expires_in", 86400))
    )

    return _guesty_token


# ============================================================
# GUESTY REQUEST
# ============================================================

async def guesty_request(
    method: str,
    endpoint: str,
    **kwargs,
):

    token = await get_guesty_token()

    headers = kwargs.pop("headers", {})

    headers["Authorization"] = f"Bearer {token}"
    headers["Accept"] = "application/json"

    async with httpx.AsyncClient(timeout=30.0) as client:

        response = await client.request(
            method,
            f"{GUESTY_BASE_URL}{endpoint}",
            headers=headers,
            **kwargs,
        )

        if response.status_code in (401, 403):

            global _guesty_token
            global _guesty_token_expires_at

            _guesty_token = None
            _guesty_token_expires_at = 0

            token = await get_guesty_token()

            headers["Authorization"] = f"Bearer {token}"

            response = await client.request(
                method,
                f"{GUESTY_BASE_URL}{endpoint}",
                headers=headers,
                **kwargs,
            )

        response.raise_for_status()

        if not response.content:
            return {}

        return response.json()


# ============================================================
# UTILS
# ============================================================

def first_value(*values):

    for value in values:

        if value not in (
            None,
            "",
            [],
            {},
        ):
            return value

    return None


def clean_message(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

    text = html.unescape(text)

    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"</p\s*>",
        "\n",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n\s+",
        "\n",
        text,
    )

    return text.strip()


def extract_results(payload: Any) -> List[Dict[str, Any]]:

    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in (
        "results",
        "data",
        "posts",
        "conversations",
    ):

        value = payload.get(key)

        if isinstance(value, list):
            return value

    return []


def extract_object_id(
    obj: Any,
) -> Optional[str]:

    if not isinstance(obj, dict):
        return None

    return first_value(
        obj.get("_id"),
        obj.get("id"),
    )


# ============================================================
# RESERVATION
# ============================================================

async def get_reservation(
    reservation_id: str,
) -> Optional[Dict[str, Any]]:

    try:

        payload = await guesty_request(
            "GET",
            "/reservations-v3",
            params=[
                (
                    "reservationIds[]",
                    reservation_id,
                )
            ],
        )

        results = extract_results(payload)

        if results:

            print(
                "Reservation retrieved successfully"
            )

            return results[0]

        if (
            isinstance(payload, dict)
            and payload.get("_id") == reservation_id
        ):

            print(
                "Reservation retrieved successfully"
            )

            return payload

        print("Reservation not found")

        return None

    except Exception as exc:

        print(
            f"ERROR get_reservation: {exc}"
        )

        return None


# ============================================================
# CONVERSATION
# ============================================================

async def get_conversation(
    conversation_id: str,
) -> Optional[Dict[str, Any]]:

    try:

        return await guesty_request(
            "GET",
            f"/communication/conversations/"
            f"{conversation_id}",
        )

    except Exception as exc:

        print(
            f"Conversation retrieval failed: {exc}"
        )

        return None


async def get_conversation_posts(
    conversation_id: str,
) -> List[Dict[str, Any]]:

    try:

        payload = await guesty_request(
            "GET",
            f"/communication/conversations/"
            f"{conversation_id}/posts",
            params={
                "sort": "-createdAt",
                "limit": CONVERSATION_POST_LIMIT,
            },
        )

        posts = extract_results(payload)

        if posts:
            posts.reverse()

        return posts

    except Exception as exc:

        print(
            f"Conversation posts unavailable: {exc}"
        )

        return []


# ============================================================
# EXTRACTION WEBHOOK
# ============================================================

def extract_conversation_id(
    payload: Dict[str, Any],
) -> Optional[str]:

    conversation = payload.get(
        "conversation"
    )

    if isinstance(conversation, dict):

        conversation_id = first_value(
            conversation.get("_id"),
            conversation.get("id"),
        )

        if conversation_id:
            return str(conversation_id)

    return first_value(
        payload.get("conversationId"),
        payload.get("conversation_id"),
    )


async def recover_conversation_id_from_inquiry(
    payload: Dict[str, Any],
) -> Optional[str]:
    """Best-effort recovery for pre-booking inquiries that have no reservation.

    We never fabricate a conversation id: we inspect recent Guesty conversations and
    require the webhook message text to match the latest guest message.
    """
    message = payload.get("message")
    if not isinstance(message, dict):
        message = {}

    target_body = post_text(message)
    if not target_body:
        conversation = payload.get("conversation")
        if isinstance(conversation, dict):
            for key in ("message", "lastMessage"):
                candidate = conversation.get(key)
                if isinstance(candidate, dict):
                    target_body = post_text(candidate)
                    if target_body:
                        break

    if not target_body:
        return None

    try:
        recent = await guesty_request(
            "GET",
            "/communication/conversations",
            params={"limit": 30, "sort": "-createdAt"},
        )
        conversations = extract_results(recent)

        matches: List[str] = []
        for conv in conversations:
            if not isinstance(conv, dict) or not is_guest_conversation(conv):
                continue
            cid = extract_object_id(conv)
            if not cid:
                continue

            posts = await get_conversation_posts(str(cid))
            latest_guest = get_latest_guest_post(posts)
            if not latest_guest:
                continue

            if post_text(latest_guest).strip() == target_body.strip():
                matches.append(str(cid))
                if len(matches) > 1:
                    # Ambiguous: fail closed rather than reply in the wrong thread.
                    return None

        return matches[0] if len(matches) == 1 else None

    except Exception as exc:
        print(f"Inquiry conversation recovery failed: {exc}")
        return None


async def recover_conversation_id_from_reservation(
    reservation_id: Optional[str],
) -> Optional[str]:
    if not reservation_id:
        return None
    reservation = await get_reservation(str(reservation_id))
    if not reservation:
        return None
    conversation = reservation.get("conversation")
    if isinstance(conversation, dict):
        cid = first_value(conversation.get("_id"), conversation.get("id"))
        if cid:
            return str(cid)
    cid = first_value(reservation.get("conversationId"), reservation.get("conversation_id"))
    if cid:
        return str(cid)
    conversation_ids = reservation.get("conversationIds")
    if isinstance(conversation_ids, list) and conversation_ids:
        return str(conversation_ids[0])
    return None


def is_guest_conversation(conversation: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(conversation, dict):
        return True
    conversation_with = conversation.get("conversationWith")
    if conversation_with is None:
        return True
    return str(conversation_with).strip().lower() == "guest"


def extract_reservation_id(
    payload: Dict[str, Any],
) -> Optional[str]:

    reservation_id = first_value(
        payload.get("reservationId"),
    )

    if reservation_id:
        return str(reservation_id)

    reservation = payload.get(
        "reservation"
    )

    if isinstance(reservation, dict):

        reservation_id = first_value(
            reservation.get("_id"),
            reservation.get("id"),
        )

        if reservation_id:
            return str(reservation_id)

    conversation = payload.get(
        "conversation"
    )

    if isinstance(conversation, dict):

        meta = conversation.get(
            "meta"
        )

        if isinstance(meta, dict):

            reservations = meta.get(
                "reservations"
            )

            if isinstance(reservations, list):

                for reservation in reservations:

                    if not isinstance(
                        reservation,
                        dict,
                    ):
                        continue

                    reservation_id = first_value(
                        reservation.get("_id"),
                        reservation.get("id"),
                    )

                    if reservation_id:
                        return str(reservation_id)

    return None


def extract_listing_id(
    reservation: Optional[Dict[str, Any]],
    conversation: Optional[Dict[str, Any]],
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[str]:

    reservation = reservation or {}
    conversation = conversation or {}
    payload = payload or {}

    candidates = [

        payload.get("listingId"),

        payload.get("listing", {}).get("_id")
        if isinstance(
            payload.get("listing"),
            dict,
        )
        else None,

        reservation.get("listingId"),

        reservation.get("lastStayListingId"),

        reservation.get("unitId"),

        reservation.get("unitTypeId"),

        reservation.get("listing", {}).get("_id")
        if isinstance(
            reservation.get("listing"),
            dict,
        )
        else None,

        conversation.get("listingId"),

        conversation.get("lastStayListingId"),

        conversation.get("unitId"),

        conversation.get("unitTypeId"),

        conversation.get("listing", {}).get("_id")
        if isinstance(
            conversation.get("listing"),
            dict,
        )
        else None,
    ]

    for candidate in candidates:

        if candidate:
            return str(candidate)

    # UNE SEULE propriété :
    # fallback sûr pour les inquiries.
    if len(PROPERTIES) == 1:

        return next(
            iter(PROPERTIES.keys())
        )

    return None


# ============================================================
# GUEST NAME
# ============================================================

def extract_guest_name(
    conversation: Optional[Dict[str, Any]],
    reservation: Optional[Dict[str, Any]],
) -> Optional[str]:

    conversation = conversation or {}
    reservation = reservation or {}

    meta = conversation.get(
        "meta"
    )

    if isinstance(meta, dict):

        name = meta.get(
            "guestName"
        )

        if name:
            return str(name).strip()

    guest = reservation.get(
        "guest"
    )

    if isinstance(guest, dict):

        name = first_value(
            guest.get("fullName"),
            guest.get("name"),
        )

        if name:
            return str(name).strip()

        first = guest.get(
            "firstName"
        )

        last = guest.get(
            "lastName"
        )

        if first or last:

            return " ".join(
                x
                for x in [first, last]
                if x
            ).strip()

    return None


# ============================================================
# MESSAGE TYPES
# ============================================================

def is_guest_post(
    post: Dict[str, Any],
) -> bool:

    message_type = str(
        post.get("type", "")
    )

    return message_type in (
        "fromGuest",
        "fromThirdParty",
    )


def is_host_post(
    post: Dict[str, Any],
) -> bool:

    message_type = str(
        post.get("type", "")
    )

    return message_type in (
        "fromHost",
        "fromGuesty",
    )


def post_text(
    post: Dict[str, Any],
) -> str:

    return clean_message(
        first_value(
            post.get("body"),
            post.get("text"),
        )
    )


# ============================================================
# FALLBACK WEBHOOK -> POSTS
# ============================================================

def webhook_thread_to_posts(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:

    conversation = payload.get(
        "conversation"
    )

    if not isinstance(
        conversation,
        dict,
    ):
        conversation = {}

    posts = []

    # --------------------------------------------------------
    # 1. conversation.thread
    # --------------------------------------------------------

    thread = conversation.get(
        "thread"
    )

    if isinstance(thread, list):

        for item in thread:

            if isinstance(item, dict):

                posts.append(
                    dict(item)
                )

    # --------------------------------------------------------
    # 2. conversation.message / body
    # --------------------------------------------------------

    for key in (
        "message",
        "lastMessage",
    ):

        item = conversation.get(
            key
        )

        if isinstance(item, dict):
            
            msg_copy = dict(item)
            if not msg_copy.get("type"):
                msg_copy["type"] = "fromGuest"

            posts.append(
                msg_copy
            )

    # --------------------------------------------------------
    # 3. top-level webhook message
    # --------------------------------------------------------

    message = payload.get(
        "message"
    )

    if isinstance(message, dict):
        
        msg_copy = dict(message)
        if not msg_copy.get("type"):
            msg_copy["type"] = "fromGuest"

        posts.append(
            msg_copy
        )

    # --------------------------------------------------------
    # Déduplication des posts
    # --------------------------------------------------------

    unique = {}

    for post in posts:

        post_id = first_value(
            post.get("postId"),
            post.get("_id"),
            post.get("id"),
        )

        body = post_text(post)

        if not body:
            continue

        key = (
            str(post_id)
            if post_id
            else hashlib.sha256(
                (
                    f"{post.get('createdAt','')}"
                    f"|{body}"
                ).encode("utf-8")
            ).hexdigest()
        )

        unique[key] = post

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x: (
            str(
                x.get("createdAt")
                or x.get("sentAt")
                or ""
            )
        )
    )

    return result


# ============================================================
# FALLBACK MESSAGES GROUPÉS
# ============================================================

def payloads_to_posts(
    payloads: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    posts = []

    for payload in payloads:

        posts.extend(
            webhook_thread_to_posts(
                payload
            )
        )

    unique = {}

    for post in posts:

        post_id = first_value(
            post.get("postId"),
            post.get("_id"),
            post.get("id"),
        )

        body = post_text(post)

        if not body:
            continue

        key = (
            str(post_id)
            if post_id
            else hashlib.sha256(
                (
                    f"{post.get('createdAt','')}"
                    f"|{body}"
                ).encode("utf-8")
            ).hexdigest()
        )

        unique[key] = post

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x: (
            str(
                x.get("createdAt")
                or x.get("sentAt")
                or ""
            )
        )
    )

    return result


# ============================================================
# SECURITY / SENSITIVE ACCESS
# ============================================================

def parse_datetime(
    value: Any,
) -> Optional[datetime]:

    if not value:
        return None

    if isinstance(
        value,
        datetime,
    ):
        return value

    if isinstance(
        value,
        date,
    ):

        return datetime.combine(
            value,
            dt_time.min,
        )

    text = str(value).strip()

    try:

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        return datetime.fromisoformat(
            text
        )

    except Exception:

        return None


def localize_datetime(
    value: datetime,
    timezone_name: str,
) -> datetime:

    tz = ZoneInfo(
        timezone_name
    )

    if value.tzinfo is None:

        return value.replace(
            tzinfo=tz
        )

    return value.astimezone(tz)


def make_local_date_time(
    value: Any,
    hour: int,
    minute: int,
    timezone_name: str,
) -> Optional[datetime]:

    if not value:
        return None

    tz = ZoneInfo(
        timezone_name
    )

    try:

        if isinstance(
            value,
            datetime,
        ):

            dt = localize_datetime(
                value,
                timezone_name,
            )

            return dt.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

        if isinstance(
            value,
            date,
        ):

            return datetime.combine(
                value,
                dt_time(
                    hour,
                    minute,
                ),
                tzinfo=tz,
            )

        text = str(value).strip()

        if "T" in text:

            dt = parse_datetime(
                text
            )

            if dt:

                dt = localize_datetime(
                    dt,
                    timezone_name,
                )

                return dt.replace(
                    hour=hour,
                    minute=minute,
                    second=0,
                    microsecond=0,
                )

        parsed = date.fromisoformat(
            text[:10]
        )

        return datetime.combine(
            parsed,
            dt_time(
                hour,
                minute,
            ),
            tzinfo=tz,
        )

    except Exception:

        return None


def access_is_authorized(
    reservation: Optional[Dict[str, Any]],
    property_data: Dict[str, Any],
) -> bool:

    if not reservation:

        print(
            "No reservation - sensitive access impossible"
        )

        return False

    status = str(
        reservation.get(
            "status",
            "",
        )
    ).lower()

    print(
        f"Reservation status for access check: {status}"
    )

    if status not in (
        "confirmed",
        "reserved",
    ):

        print(
            "ACCESS DENIED - reservation status"
        )

        return False

    timezone_name = property_data[
        "timezone"
    ]

    tz = ZoneInfo(
        timezone_name
    )

    now = datetime.now(tz)

    checkin = None
    checkout = None

    # --------------------------------------------------------
    # Dates localisées Guesty
    # --------------------------------------------------------

    checkin_date = first_value(
        reservation.get(
            "checkInDateLocalized"
        ),
        reservation.get(
            "checkinDateLocalized"
        ),
    )

    checkout_date = first_value(
        reservation.get(
            "checkOutDateLocalized"
        ),
        reservation.get(
            "checkoutDateLocalized"
        ),
    )

    if checkin_date:

        checkin = make_local_date_time(
            checkin_date,
            16,
            0,
            timezone_name,
        )

    if checkout_date:

        checkout = make_local_date_time(
            checkout_date,
            10,
            0,
            timezone_name,
        )

    # --------------------------------------------------------
    # Fallback timestamps
    # --------------------------------------------------------

    if not checkin:

        for key in (
            "checkIn",
            "checkin",
            "checkInDate",
            "arrivalDate",
        ):

            value = reservation.get(
                key
            )

            dt = parse_datetime(
                value
            )

            if dt:

                checkin = localize_datetime(
                    dt,
                    timezone_name,
                ).replace(
                    hour=16,
                    minute=0,
                    second=0,
                    microsecond=0,
                )

                break

    if not checkout:

        for key in (
            "checkOut",
            "checkout",
            "checkOutDate",
            "departureDate",
        ):

            value = reservation.get(
                key
            )

            dt = parse_datetime(
                value
            )

            if dt:

                checkout = localize_datetime(
                    dt,
                    timezone_name,
                ).replace(
                    hour=10,
                    minute=0,
                    second=0,
                    microsecond=0,
                )

                break

    if not checkin or not checkout:

        print(
            "ACCESS DENIED - dates unavailable"
        )

        return False

    print(
        f"Reservation check-in detected: "
        f"{checkin.isoformat()}"
    )

    print(
        f"Reservation check-out detected: "
        f"{checkout.isoformat()}"
    )

    authorized_from = (
        checkin.timestamp()
        - 24 * 60 * 60
    )

    authorized_from = datetime.fromtimestamp(
        authorized_from,
        tz=tz,
    )

    authorized_until = checkout

    if (
        authorized_from
        <= now
        <= authorized_until
    ):

        print(
            "ACCESS AUTHORIZED - "
            "confirmed imminent/current stay"
        )

        return True

    print(
        "ACCESS DENIED - "
        "reservation is not imminent/current"
    )

    return False


# ============================================================
# PROPERTY CONTEXT
# ============================================================

def build_property_context(
    property_data: Dict[str, Any],
    authorized: bool,
) -> str:

    context = f"""
LOGEMENT :
{property_data["name"]}

ADRESSE :
{property_data["address"] if authorized else "Adresse exacte non communicable avant autorisation d'accès"}

CHECK-IN :
{property_data["check_in_time"]}

CHECK-OUT :
{property_data["check_out_time"]}

ÉTAGE :
{property_data["floor"]}

ASCENSEUR :
{"oui" if property_data["elevator"] else "non"}

CHAMBRES :
{property_data["bedrooms"]}

SALLES DE BAIN :
{property_data["bathrooms"]}

WC :
{property_data["wc"]}

CUISINE ÉQUIPÉE :
{"oui" if property_data["fully_equipped_kitchen"] else "non"}

CLIMATISATION :
{"oui" if property_data["air_conditioning"] else "non"}

SENSITIVE_ACCESS_AUTHORIZED :
{str(authorized).upper()}
"""

    if authorized:

        context += f"""

INFORMATIONS D'ACCÈS AUTORISÉES :

CODE IMMEUBLE :
{property_data["building_code"]}

CODE BOÎTE À CLÉS :
{property_data["keybox_code"]}

EMPLACEMENT BOÎTE À CLÉS :
{property_data["keybox_location"]}

CHEMIN D'ACCÈS :
{property_data["access_route"]}

VIDÉO D'ACCÈS :
{property_data["video_url"]}
"""

    else:

        context += """

INFORMATIONS D'ACCÈS SENSIBLES INTERDITES.

NE DONNE PAS :
- le code immeuble
- le code boîte à clés
- le chemin détaillé
- le lien vidéo

Tu peux seulement indiquer :
- appartement au 1er étage
- pas d'ascenseur
- toutes les instructions sont disponibles
  sur le guide d'arrivée Airbnb.
"""

    return context.strip()


# ============================================================
# HISTORY
# ============================================================

def sanitize_sensitive_text(
    text: str,
    property_data: Dict[str, Any],
    authorized: bool,
) -> str:
    if authorized:
        return text
    sensitive_values = [
        property_data.get("building_code"),
        property_data.get("keybox_code"),
        property_data.get("video_url"),
        property_data.get("address"),
        property_data.get("access_route"),
        property_data.get("keybox_location"),
    ]
    for secret in sensitive_values:
        if secret:
            text = text.replace(str(secret), "[INFORMATION SENSIBLE MASQUÉE]")
    text = re.sub(r"https?://\S+", "[URL MASQUÉE]", text)
    text = re.sub(
        r"(?i)\b(code(?:\s+(?:immeuble|porte|bo[iî]te\s*[àa]\s*cl[eé]s?))?\s*[:=-]?\s*)[A-Z]?\d{3,8}\b",
        r"\1[CODE MASQUÉ]",
        text,
    )
    return text


def build_conversation_history(
    posts: List[Dict[str, Any]],
    property_data: Dict[str, Any],
    authorized: bool,
) -> str:

    lines = []

    for post in posts:

        body = post_text(
            post
        )

        if not body:
            continue

        if is_guest_post(post):

            role = "VOYAGEUR"

        elif is_host_post(post):

            role = "HÔTE"

        else:

            continue

        body = sanitize_sensitive_text(
            body,
            property_data,
            authorized,
        )

        lines.append(
            f"{role}: {body}"
        )

    return "\n".join(lines)


# ============================================================
# STYLE CACHE
# ============================================================

_style_cache: Dict[
    str,
    Dict[str, Any],
] = {}


def sanitize_style_example(
    text: str,
    property_data: Dict[str, Any],
) -> str:

    text = clean_message(
        text
    )

    for secret in (
        property_data.get("building_code"),
        property_data.get("keybox_code"),
        property_data.get("video_url"),
        property_data.get("address"),
        property_data.get("access_route"),
        property_data.get("keybox_location"),
    ):

        if secret:

            text = text.replace(
                str(secret),
                "[INFO SENSIBLE]",
            )

    text = re.sub(
        r"https?://\S+",
        "[URL]",
        text,
    )

    text = re.sub(
        r"[\w\.-]+@[\w\.-]+\.\w+",
        "[EMAIL]",
        text,
    )

    text = re.sub(
        r"\+?\d[\d\s().-]{7,}\d",
        "[TÉLÉPHONE]",
        text,
    )

    return text.strip()


async def get_recent_style_examples(
    listing_id: Optional[str],
    property_data: Dict[str, Any],
) -> List[str]:

    cache_key = (
        listing_id
        or "__global__"
    )

    cached = _style_cache.get(
        cache_key
    )

    now = time.time()

    if cached:

        if (
            now - cached["timestamp"]
            < STYLE_CACHE_TTL
        ):

            return cached["examples"]

    try:

        params = {
            "limit": STYLE_CONVERSATION_LIMIT,
            "sort": "-createdAt",
        }

        if listing_id:

            filters = [
                {
                    "field": "listing._id",
                    "operator": "$eq",
                    "value": listing_id,
                }
            ]

            params["filters"] = json.dumps(
                filters,
                separators=(",", ":"),
            )

        payload = await guesty_request(
            "GET",
            "/communication/conversations",
            params=params,
        )

        conversations = extract_results(
            payload
        )

        examples = []

        for conversation in conversations:

            conversation_id = extract_object_id(
                conversation
            )

            if not conversation_id:
                continue

            posts = await get_conversation_posts(
                conversation_id
            )

            for post in posts:

                if not is_host_post(
                    post
                ):
                    continue

                if (
                    post.get(
                        "isAutomatic"
                    )
                    is True
                ):
                    continue

                body = post_text(
                    post
                )

                if not body:
                    continue

                body = sanitize_style_example(
                    body,
                    property_data,
                )

                if body:
                    examples.append(
                        body
                    )

                if len(examples) >= 12:
                    break

            if len(examples) >= 12:
                break

        _style_cache[
            cache_key
        ] = {
            "timestamp": now,
            "examples": examples,
        }

        print(
            f"Style cache refreshed "
            f"for listing {cache_key}: "
            f"{len(examples)} examples"
        )

        return examples

    except Exception as exc:

        print(
            f"ERROR style cache: {exc}"
        )

        return []


# ============================================================
# OPENAI
# ============================================================

async def generate_reply(
    guest_name: Optional[str],
    property_data: Dict[str, Any],
    authorized: bool,
    history: str,
    style_examples: List[str],
) -> str:

    property_context = build_property_context(
        property_data,
        authorized,
    )

    style_context = ""

    if style_examples:

        style_context = (
            "\n\nSTYLE DE L'HÔTE "
            "(uniquement pour le ton) :\n"
        )

        for example in style_examples:

            style_context += (
                f"- {example}\n"
            )

        style_context += """
Ces exemples servent uniquement à reproduire
le ton et la façon d'écrire de l'hôte.

N'utilise jamais leur contenu factuel
pour inventer des informations sur le logement.
"""

    guest_name_context = (
        f"Prénom du voyageur : {guest_name}"
        if guest_name
        else "Prénom du voyageur inconnu"
    )

    user_prompt = f"""
{property_context}

{guest_name_context}

HISTORIQUE DE CONVERSATION :

{history}

{style_context}

Réponds au dernier message du voyageur.

Si plusieurs messages récents forment une même demande,
réponds à tous les points en UNE SEULE réponse.

Sois naturel, chaleureux, utile et concis.
"""

    response = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_RULES},
            {"role": "user", "content": user_prompt}
        ]
    )

    reply = response.choices[0].message.content.strip()

    if not reply:

        raise RuntimeError(
            "OpenAI returned empty response"
        )

    return reply


# ============================================================
# SEND
# ============================================================

async def send_reply(
    conversation_id: str,
    reply: str,
):

    if TEST_MODE:

        print("")
        print(
            "================================"
        )
        print(
            "TEST MODE - MESSAGE NOT SENT"
        )
        print(
            "================================"
        )
        print("")
        print(
            "AI WOULD REPLY:"
        )
        print(reply)
        print("")

        return

    payload = {
        "module": {
            "type": "airbnb2"
        },
        "body": reply,
    }

    await guesty_request(
        "POST",
        f"/communication/conversations/"
        f"{conversation_id}/send-message",
        json=payload,
        headers={
            "Content-Type": "application/json",
        },
    )

    print(
        "MESSAGE SENT SUCCESSFULLY"
    )


# ============================================================
# DEDUPLICATION
# ============================================================

processed_events: Dict[
    str,
    float,
] = {}


def cleanup_processed_events():

    now = time.time()

    expired = [

        key

        for key, timestamp
        in processed_events.items()

        if now - timestamp
        > PROCESSED_EVENT_TTL
    ]

    for key in expired:

        processed_events.pop(
            key,
            None,
        )


def make_event_key(
    payload: Dict[str, Any],
) -> str:

    meta = payload.get(
        "meta"
    )

    if not isinstance(
        meta,
        dict,
    ):
        meta = {}

    message = payload.get(
        "message"
    )

    if not isinstance(
        message,
        dict,
    ):
        message = {}

    event_id = first_value(

        meta.get(
            "eventId"
        ),

        meta.get(
            "messageId"
        ),

        payload.get(
            "eventId"
        ),

        payload.get(
            "messageId"
        ),

        message.get(
            "postId"
        ),

        message.get(
            "_id"
        ),

        message.get(
            "id"
        ),
    )

    if event_id:

        return str(
            event_id
        )

    raw = json.dumps(
        {
            "event": payload.get(
                "event"
            ),
            "conversationId":
                extract_conversation_id(
                    payload
                ),
            "createdAt":
                message.get(
                    "createdAt"
                ),
            "body":
                post_text(
                    message
                ),
        },
        sort_keys=True,
        default=str,
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# ============================================================
# GUEST LATEST MESSAGE
# ============================================================

def get_latest_guest_post(
    posts: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:

    for post in reversed(
        posts
    ):

        if not is_guest_post(
            post
        ):
            continue

        if post_text(
            post
        ):

            return post

    return None


def host_replied_after_guest(
    posts: List[Dict[str, Any]],
    guest_post: Dict[str, Any],
) -> bool:

    guest_time = parse_datetime(
        first_value(
            guest_post.get(
                "createdAt"
            ),
            guest_post.get(
                "sentAt"
            ),
        )
    )

    if not guest_time:
        return False

    for post in posts:

        if not is_host_post(
            post
        ):
            continue

        host_time = parse_datetime(
            first_value(
                post.get(
                    "createdAt"
                ),
                post.get(
                    "sentAt"
                ),
            )
        )

        if not host_time:
            continue

        if host_time > guest_time:

            return True

    return False


# ============================================================
# MAIN PROCESSING
# ============================================================

async def process_messages(
    conversation_id: str,
    payloads: List[Dict[str, Any]],
):

    print("")
    print(
        "================================"
    )
    print(
        f"PROCESSING CONVERSATION "
        f"{conversation_id}"
    )
    print(
        f"Grouped webhooks: {len(payloads)}"
    )
    print(
        "================================"
    )

    # --------------------------------------------------------
    # Dernier payload
    # --------------------------------------------------------

    latest_payload = payloads[-1]

    # --------------------------------------------------------
    # Reservation
    # --------------------------------------------------------

    reservation_id = None

    for payload in reversed(
        payloads
    ):

        reservation_id = extract_reservation_id(
            payload
        )

        if reservation_id:
            break

    reservation = None

    if reservation_id:

        print(
            f"Reservation ID: "
            f"{reservation_id}"
        )

        reservation = await get_reservation(
            reservation_id
        )

    else:

        print(
            "No reservation ID - "
            "inquiry / pre-booking conversation"
        )

    # --------------------------------------------------------
    # Conversation
    # --------------------------------------------------------

    conversation = latest_payload.get(
        "conversation"
    )

    if not isinstance(
        conversation,
        dict,
    ):

        conversation = {}

    fresh_conversation = await get_conversation(
        conversation_id
    )

    if fresh_conversation:

        conversation = fresh_conversation

    if not is_guest_conversation(conversation):
        print("Non-guest / owner conversation - skipping")
        return

    print(
        "Conversation ID found"
    )

    # --------------------------------------------------------
    # Property
    # --------------------------------------------------------

    listing_id = extract_listing_id(
        reservation,
        conversation,
        latest_payload,
    )

    if not listing_id:

        print(
            "Could not identify property safely"
        )

        return

    property_data = PROPERTIES.get(
        listing_id
    )

    if not property_data:

        print(
            f"Unknown listing ID: "
            f"{listing_id}"
        )

        return

    print(
        f"Property identified: "
        f"{property_data['name']}"
    )

    # --------------------------------------------------------
    # Sensitive access
    # --------------------------------------------------------

    authorized = access_is_authorized(
        reservation,
        property_data,
    )

    print(
        f"Sensitive access authorized: "
        f"{authorized}"
    )

    # --------------------------------------------------------
    # 1. Try Guesty posts
    # --------------------------------------------------------

    posts = await get_conversation_posts(
        conversation_id
    )

    if posts:

        print(
            f"Conversation posts retrieved: "
            f"{len(posts)}"
        )

    else:

        print(
            "No conversation posts returned."
        )

        print(
            "USING WEBHOOK FALLBACK."
        )

        # ----------------------------------------------------
        # 2. conversation.thread + message
        # ----------------------------------------------------

        posts = payloads_to_posts(
            payloads
        )

        # Si get_conversation a fourni un thread,
        # on l'utilise également.
        if not posts:

            posts = webhook_thread_to_posts(
                {
                    "conversation":
                        conversation,
                    "message":
                        latest_payload.get(
                            "message"
                        ),
                }
            )

        print(
            f"Fallback posts available: "
            f"{len(posts)}"
        )

    # --------------------------------------------------------
    # Aucun message exploitable
    # --------------------------------------------------------

    if not posts:

        print(
            "ERROR: no usable guest message "
            "found anywhere."
        )

        # IMPORTANT :
        # on affiche seulement la structure,
        # pas les secrets.
        message = latest_payload.get(
            "message"
        )

        webhook_conversation = latest_payload.get(
            "conversation"
        )

        if isinstance(
            message,
            dict,
        ):

            print(
                "Webhook message keys: "
                f"{list(message.keys())}"
            )

        if isinstance(
            webhook_conversation,
            dict,
        ):

            print(
                "Webhook conversation keys: "
                f"{list(webhook_conversation.keys())}"
            )

        return

    # --------------------------------------------------------
    # Dernier message voyageur
    # --------------------------------------------------------

    guest_post = get_latest_guest_post(
        posts
    )

    if not guest_post:

        print(
            "No guest message found."
        )

        return

    guest_message = post_text(
        guest_post
    )

    print(
        f"Guest message: "
        f"{guest_message}"
    )

    # --------------------------------------------------------
    # Anti double-réponse
    # --------------------------------------------------------

    if host_replied_after_guest(
        posts,
        guest_post,
    ):

        print(
            "Host already replied after "
            "latest guest message - skipping"
        )

        return

    # --------------------------------------------------------
    # Guest name
    # --------------------------------------------------------

    guest_name = extract_guest_name(
        conversation,
        reservation,
    )

    # --------------------------------------------------------
    # History
    # --------------------------------------------------------

    history = build_conversation_history(
        posts,
        property_data,
        authorized,
    )

    if not history:

        history = (
            f"VOYAGEUR: {guest_message}"
        )

    # --------------------------------------------------------
    # Style
    # --------------------------------------------------------

    style_examples = await get_recent_style_examples(
        listing_id,
        property_data,
    )

    # --------------------------------------------------------
    # OpenAI
    # --------------------------------------------------------

    print(
        "Calling OpenAI..."
    )

    reply = await generate_reply(
        guest_name=guest_name,
        property_data=property_data,
        authorized=authorized,
        history=history,
        style_examples=style_examples,
    )

    print(
        "OpenAI response generated successfully."
    )

    # --------------------------------------------------------
    # Send / test
    # --------------------------------------------------------

    await send_reply(
        conversation_id,
        reply,
    )


# ============================================================
# DEBOUNCE
# ============================================================

pending_payloads: Dict[
    str,
    List[Dict[str, Any]],
] = {}

pending_tasks: Dict[
    str,
    asyncio.Task,
] = {}


async def delayed_process(
    conversation_id: str,
):

    current_task = asyncio.current_task()

    try:

        print(
            f"Waiting {DEBOUNCE_SECONDS}s "
            f"before processing "
            f"{conversation_id}"
        )

        await asyncio.sleep(
            DEBOUNCE_SECONDS
        )

        payloads = pending_payloads.pop(
            conversation_id,
            [],
        )

        if not payloads:

            print(
                "No pending payloads."
            )

            return

        await process_messages(
            conversation_id,
            payloads,
        )

    except asyncio.CancelledError:

        print(
            f"Debounce reset for "
            f"{conversation_id}"
        )

        raise

    except Exception as exc:

        print(
            f"ERROR delayed_process: "
            f"{exc}"
        )

    finally:

        current = pending_tasks.get(
            conversation_id
        )

        if current is current_task:

            pending_tasks.pop(
                conversation_id,
                None,
            )


def schedule_message(
    conversation_id: str,
    payload: Dict[str, Any],
):

    if conversation_id not in pending_payloads:

        pending_payloads[
            conversation_id
        ] = []

    pending_payloads[
        conversation_id
    ].append(
        payload
    )

    existing = pending_tasks.get(
        conversation_id
    )

    if existing:

        existing.cancel()

    pending_tasks[
        conversation_id
    ] = asyncio.create_task(
        delayed_process(
            conversation_id
        )
    )


# ============================================================
# WEBHOOK
# ============================================================

@app.post("/guesty/webhook")
async def guesty_webhook(
    request: Request,
):

    try:
        raw_body = await request.body()

        if GUESTY_WEBHOOK_SECRET:
            if Webhook is None:
                print("ERROR: svix package missing; webhook rejected")
                return JSONResponse(
                    status_code=503,
                    content={"ok": False, "error": "Webhook verifier unavailable"},
                )
            try:
                verified = Webhook(GUESTY_WEBHOOK_SECRET).verify(
                    raw_body,
                    dict(request.headers),
                )
                payload = verified if isinstance(verified, dict) else json.loads(raw_body)
            except Exception as exc:
                print(f"Invalid Guesty webhook signature: {exc}")
                return JSONResponse(
                    status_code=401,
                    content={"ok": False, "error": "Invalid webhook signature"},
                )
        else:
            if not TEST_MODE:
                print("ERROR: GUESTY_WEBHOOK_SECRET missing in production")
                return JSONResponse(
                    status_code=503,
                    content={"ok": False, "error": "Webhook secret missing"},
                )
            payload = json.loads(raw_body)

    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Invalid JSON"},
        )

    print("")
    print(
        "================================"
    )
    print(
        "Incoming Guesty webhook"
    )
    print(
        "================================"
    )

    event = payload.get(
        "event"
    )

    print(
        f"Event: {event}"
    )

    if event != "reservation.messageReceived":

        print(
            "Event ignored."
        )

        return {
            "ok": True,
            "ignored": True,
        }

    cleanup_processed_events()

    # --------------------------------------------------------
    # Dedup
    # --------------------------------------------------------

    event_key = make_event_key(
        payload
    )

    if event_key in processed_events:

        print(
            "Duplicate webhook ignored."
        )

        return {
            "ok": True,
            "duplicate": True,
        }

    processed_events[
        event_key
    ] = time.time()

    # --------------------------------------------------------
    # Conversation ID
    # --------------------------------------------------------

    conversation_id = extract_conversation_id(
        payload
    )

    if not conversation_id:

        print("NO CONVERSATION ID IN WEBHOOK - trying reservation fallback")

        reservation_id = extract_reservation_id(payload)
        conversation_id = await recover_conversation_id_from_reservation(reservation_id)

        if conversation_id:
            print(f"Conversation ID recovered from reservation: {conversation_id}")
        else:
            print("Reservation fallback unavailable - trying inquiry recovery")
            conversation_id = await recover_conversation_id_from_inquiry(payload)

        if not conversation_id:
            print(
                "Could not recover conversation ID safely. Webhook keys: "
                f"{list(payload.keys())}"
            )
            return {
                "ok": True,
                "ignored": True,
                "reason": "no_conversation_id_safe_match",
            }

        print(f"Conversation ID safely recovered: {conversation_id}")

    print(
        f"Conversation ID: "
        f"{conversation_id}"
    )

    # --------------------------------------------------------
    # Log SAFE du message
    # --------------------------------------------------------

    message = payload.get(
        "message"
    )

    if isinstance(
        message,
        dict,
    ):

        print(
            "Webhook message keys: "
            f"{list(message.keys())}"
        )

        webhook_body = post_text(
            message
        )

        if webhook_body:

            print(
                f"Webhook guest message: "
                f"{webhook_body}"
            )

    conversation = payload.get(
        "conversation"
    )

    if isinstance(
        conversation,
        dict,
    ):

        print(
            "Webhook conversation keys: "
            f"{list(conversation.keys())}"
        )

    # --------------------------------------------------------
    # Debounce
    # --------------------------------------------------------

    schedule_message(
        conversation_id,
        payload,
    )

    return {
        "ok": True,
        "scheduled": True,
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {

        "status": "ok",

        "test_mode": TEST_MODE,

        "openai_model": OPENAI_MODEL,

        "webhook_signature_validation":
            bool(GUESTY_WEBHOOK_SECRET),

        "properties": len(PROPERTIES),

        "debounce_seconds":
            DEBOUNCE_SECONDS,

        "openai_timeout_seconds":
            30,
    }


@app.get("/")
async def root():

    return {

        "message":
            "Airbnb AI Agent is running",

        "test_mode":
            TEST_MODE,

        "properties":
            list(PROPERTIES.keys()),
    }


# ============================================================
# SETUP WEBHOOK
# ============================================================

@app.get("/setup-webhook")
async def setup_webhook():

    return {

        "message":
            "Webhook already configured. "
            "Do not create another one.",

        "event":
            "reservation.messageReceived",
    }
