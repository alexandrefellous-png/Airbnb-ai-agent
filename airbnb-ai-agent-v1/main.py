import os
import re
import json
import html
import time
import hashlib
import asyncio
from datetime import datetime, date, time as dt_time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request
from openai import AsyncOpenAI


# ============================================================
# CONFIGURATION
# ============================================================

GUESTY_CLIENT_ID = os.getenv("GUESTY_CLIENT_ID")
GUESTY_CLIENT_SECRET = os.getenv("GUESTY_CLIENT_SECRET")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")

TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"

GUESTY_BASE_URL = "https://open-api.guesty.com/v1"
GUESTY_TOKEN_URL = "https://open-api.guesty.com/oauth2/token"

DEBOUNCE_SECONDS = 15

CONVERSATION_POST_LIMIT = 30

STYLE_CONVERSATION_LIMIT = 6
STYLE_POSTS_PER_CONVERSATION = 10
STYLE_CACHE_TTL = 1800  # 30 minutes


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
# PROPRIÉTÉS
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

        "building_code": "7531",

        "keybox_code": "C2613",

        "access_route": (
            "Entrer dans l'immeuble avec le code 7531, "
            "traverser la petite cour, prendre l'escalier juste après "
            "la petite cour, monter au 1er étage. "
            "L'appartement est la porte à gauche de l'escalier."
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
# RÈGLES DE L'AGENT
# ============================================================

SYSTEM_RULES = """
Tu es l'assistant Airbnb d'un hôte.

TON OBJECTIF :
Répondre naturellement aux voyageurs comme le ferait un hôte humain,
avec chaleur, précision et bon sens.

STYLE :
- Réponds dans la langue du voyageur.
- Sois naturel, chaleureux et concis.
- Tu peux utiliser ":)" ou "😊" de temps en temps.
- Ne sois jamais robotique.
- Ne fais pas de longues listes si ce n'est pas nécessaire.
- Ne répète pas inutilement les informations déjà dites.
- Utilise le prénom du voyageur quand il est disponible.
- Tu peux rassurer et aider intelligemment au lieu de répondre
  simplement par oui/non.

IMPORTANT :
Tu dois lire toute la conversation avant de répondre.

Si le voyageur envoie plusieurs messages rapprochés,
considère-les comme une seule demande globale.

Tu dois répondre aux messages entrants.
Ne reste jamais silencieux simplement parce qu'une partie de la demande
est inconnue.

Si tu connais une partie de la réponse mais pas le reste :
- réponds à ce que tu sais ;
- puis indique naturellement que tu vas vérifier le reste auprès du manager.

EXEMPLES :

Voyageur :
"Y a-t-il un ascenseur ?"

Mauvaise réponse :
"Non."

Bonne logique :
"Il n'y a pas d'ascenseur, l'appartement est au 1er étage :)
Si vous avez des bagages, nous pouvons bien sûr vous aider à votre arrivée."

VOYAGEUR AVANT RÉSERVATION :
Les voyageurs qui posent des questions avant de réserver doivent recevoir
une vraie réponse.
L'absence de réservation ne signifie PAS qu'il faut ignorer le message.

Tu peux répondre aux questions générales concernant :
- l'appartement
- l'étage
- l'ascenseur
- les chambres
- les salles de bain
- la cuisine
- la climatisation
- les équipements connus
- le quartier si l'information est fournie
- le check-in général
- le check-out général

NE JAMAIS INVENTER une information.

Si une information n'est pas connue :
dis simplement que tu vas vérifier auprès du manager.

DEMANDES SENSIBLES :
Pour :
- remboursement
- annulation exceptionnelle
- réduction
- compensation
- litige
- paiement
- problème sérieux
- demande inhabituelle
- décision commerciale
- problème de sécurité
- urgence médicale

ne prends jamais la décision toi-même.

Réponds naturellement que tu vas voir cela avec le manager.

ACCÈS :
Les codes d'accès et informations d'accès détaillées sont confidentiels.

Ils ne doivent être communiqués que si le serveur t'indique explicitement
que l'accès sensible est autorisé.

Si l'accès sensible n'est PAS autorisé :
- ne donne aucun code ;
- ne donne pas l'URL de la vidéo ;
- ne donne pas le chemin détaillé ;
- tu peux dire que l'appartement est au 1er étage ;
- tu peux dire qu'il n'y a pas d'ascenseur ;
- indique que toutes les instructions sont disponibles
  sur le guide d'arrivée sur Airbnb.

VIDÉO :
Ne propose pas spontanément la vidéo à chaque message.

Utilise la vidéo uniquement si :
- le voyageur demande la vidéo ;
- il dit qu'il est perdu ;
- il ne trouve pas l'entrée ;
- il ne trouve pas l'escalier ;
- il ne trouve pas l'appartement ;
- il ne comprend pas les instructions d'accès.

Si la vidéo est disponible dans le contexte et que l'accès sensible est autorisé,
tu peux donner son lien.

CHECK-IN :
Le check-in commence à 16h.

CHECK-OUT :
Le check-out est à 10h.

UNE SEULE RÉPONSE :
Génère uniquement le message destiné au voyageur.
Pas d'analyse.
Pas de commentaire interne.
Pas de "Voici la réponse".
"""


# ============================================================
# CACHE TOKENS GUESTY
# ============================================================

_guesty_token: Optional[str] = None
_guesty_token_expires_at: float = 0


async def get_guesty_token() -> str:

    global _guesty_token
    global _guesty_token_expires_at

    now = time.time()

    if _guesty_token and now < (_guesty_token_expires_at - 300):
        return _guesty_token

    if not GUESTY_CLIENT_ID or not GUESTY_CLIENT_SECRET:
        raise RuntimeError("Guesty credentials missing")

    data = {
        "grant_type": "client_credentials",
        "scope": "open-api",
        "client_secret": GUESTY_CLIENT_SECRET,
        "client_id": GUESTY_CLIENT_ID,
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:

        response = await client.post(
            GUESTY_TOKEN_URL,
            data=data,
            headers=headers,
        )

        response.raise_for_status()

        payload = response.json()

    _guesty_token = payload["access_token"]

    expires_in = int(payload.get("expires_in", 86400))

    _guesty_token_expires_at = time.time() + expires_in

    return _guesty_token


# ============================================================
# HTTP GUESTY
# ============================================================

async def guesty_request(
    method: str,
    endpoint: str,
    **kwargs
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

        # Si token expiré, on renouvelle une fois
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

        if response.content:
            return response.json()

        return {}


# ============================================================
# HELPERS JSON
# ============================================================

def first_value(*values):

    for value in values:
        if value not in (None, "", [], {}):
            return value

    return None


def extract_id(obj: Any) -> Optional[str]:

    if not isinstance(obj, dict):
        return None

    return first_value(
        obj.get("_id"),
        obj.get("id"),
    )


def extract_results(payload: Any) -> List[Dict[str, Any]]:

    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in ("results", "data", "posts", "conversations"):
        value = payload.get(key)

        if isinstance(value, list):
            return value

    return []


# ============================================================
# NETTOYAGE HTML
# ============================================================

def clean_message(text: Any) -> str:

    if not text:
        return ""

    text = str(text)

    text = html.unescape(text)

    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)

    text = re.sub(r"<[^>]+>", " ", text)

    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


# ============================================================
# RÉSERVATION
# ============================================================

async def get_reservation(
    reservation_id: str
) -> Optional[Dict[str, Any]]:

    try:

        params = [
            ("reservationIds[]", reservation_id)
        ]

        payload = await guesty_request(
            "GET",
            "/reservations-v3",
            params=params,
        )

        results = extract_results(payload)

        if results:
            print("Reservation retrieved successfully")
            return results[0]

        if isinstance(payload, dict):

            if payload.get("_id") == reservation_id:
                print("Reservation retrieved successfully")
                return payload

        print("Reservation not found")
        return None

    except Exception as exc:

        print(f"ERROR get_reservation: {exc}")

        return None


# ============================================================
# CONVERSATION
# ============================================================

async def get_conversation(
    conversation_id: str
) -> Optional[Dict[str, Any]]:

    try:

        payload = await guesty_request(
            "GET",
            f"/communication/conversations/{conversation_id}",
        )

        return payload

    except Exception as exc:

        print(f"ERROR get_conversation: {exc}")

        return None


async def get_conversation_posts(
    conversation_id: str,
    limit: int = CONVERSATION_POST_LIMIT,
) -> List[Dict[str, Any]]:

    try:

        payload = await guesty_request(
            "GET",
            f"/communication/conversations/{conversation_id}/posts",
            params={
                "sort": "-createdAt",
                "limit": limit,
            },
        )

        posts = extract_results(payload)

        # Guesty renvoie normalement les plus récents en premier.
        posts.reverse()

        return posts

    except Exception as exc:

        print(f"ERROR get_conversation_posts: {exc}")

        return []


# ============================================================
# EXTRACTION IDS
# ============================================================

def extract_reservation_id(
    payload: Dict[str, Any],
    conversation: Optional[Dict[str, Any]] = None,
) -> Optional[str]:

    reservation_id = first_value(
        payload.get("reservationId"),
        payload.get("reservation", {}).get("_id")
        if isinstance(payload.get("reservation"), dict)
        else None,
    )

    if reservation_id:
        return reservation_id

    conversation = conversation or payload.get("conversation") or {}

    meta = conversation.get("meta", {})

    reservations = meta.get("reservations", [])

    if isinstance(reservations, list):

        # On prend le dernier / premier selon disponibilité
        for reservation in reservations:

            if isinstance(reservation, dict):

                rid = first_value(
                    reservation.get("_id"),
                    reservation.get("id"),
                )

                if rid:
                    return rid

    return None


def extract_conversation_id(
    payload: Dict[str, Any]
) -> Optional[str]:

    conversation = payload.get("conversation")

    if isinstance(conversation, dict):

        return first_value(
            conversation.get("_id"),
            conversation.get("id"),
        )

    return first_value(
        payload.get("conversationId"),
        payload.get("conversation_id"),
    )


def extract_listing_id(
    reservation: Optional[Dict[str, Any]],
    conversation: Optional[Dict[str, Any]],
) -> Optional[str]:

    reservation = reservation or {}
    conversation = conversation or {}

    candidates = [

        reservation.get("listingId"),

        reservation.get("listing", {}).get("_id")
        if isinstance(reservation.get("listing"), dict)
        else None,

        reservation.get("listing", {}).get("id")
        if isinstance(reservation.get("listing"), dict)
        else None,

        reservation.get("unitId"),

        reservation.get("unitTypeId"),

        conversation.get("listingId"),

        conversation.get("listing", {}).get("_id")
        if isinstance(conversation.get("listing"), dict)
        else None,

        conversation.get("lastStayListingId"),

        conversation.get("unitId"),

        conversation.get("unitTypeId"),
    ]

    for candidate in candidates:

        if candidate:
            return str(candidate)

    # Si un seul logement est configuré,
    # on peut l'utiliser pour les inquiries sans reservationId.
    if len(PROPERTIES) == 1:
        return next(iter(PROPERTIES.keys()))

    return None


# ============================================================
# NOM VOYAGEUR
# ============================================================

def extract_guest_name(
    conversation: Optional[Dict[str, Any]],
    reservation: Optional[Dict[str, Any]],
) -> Optional[str]:

    conversation = conversation or {}
    reservation = reservation or {}

    meta = conversation.get("meta", {})

    name = first_value(
        meta.get("guestName"),
    )

    if name:
        return str(name).strip()

    guest = reservation.get("guest")

    if isinstance(guest, dict):

        name = first_value(
            guest.get("fullName"),
            guest.get("name"),
        )

        if name:
            return str(name).strip()

        first = guest.get("firstName")
        last = guest.get("lastName")

        if first or last:
            return " ".join(
                x for x in [first, last]
                if x
            ).strip()

    return None


# ============================================================
# DATES / ACCÈS
# ============================================================

def parse_datetime(value: Any) -> Optional[datetime]:

    if not value:
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, date):

        return datetime.combine(
            value,
            dt_time.min,
        )

    value = str(value).strip()

    try:

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        return datetime.fromisoformat(value)

    except Exception:
        return None


def localize_datetime(
    dt: datetime,
    timezone_name: str,
) -> datetime:

    tz = ZoneInfo(timezone_name)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)

    return dt.astimezone(tz)


def localized_date_to_datetime(
    value: Any,
    hour: int,
    minute: int,
    timezone_name: str,
) -> Optional[datetime]:

    if not value:
        return None

    try:

        if isinstance(value, datetime):

            dt = value

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=ZoneInfo(timezone_name)
                )

            return dt.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

        if isinstance(value, date):

            return datetime.combine(
                value,
                dt_time(hour, minute),
                tzinfo=ZoneInfo(timezone_name),
            )

        text = str(value).strip()

        if "T" in text:

            dt = parse_datetime(text)

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

        parsed_date = date.fromisoformat(text[:10])

        return datetime.combine(
            parsed_date,
            dt_time(hour, minute),
            tzinfo=ZoneInfo(timezone_name),
        )

    except Exception:

        return None


def access_is_authorized(
    reservation: Optional[Dict[str, Any]],
    property_data: Dict[str, Any],
) -> bool:

    if not reservation:
        print("No reservation - sensitive access impossible")
        return False

    status = str(
        reservation.get("status", "")
    ).lower()

    print(f"Reservation status for access check: {status}")

    if status not in ("confirmed", "reserved"):
        print("ACCESS DENIED - reservation status")
        return False

    timezone_name = property_data["timezone"]

    tz = ZoneInfo(timezone_name)

    now = datetime.now(tz)

    checkin = None
    checkout = None

    # Priorité aux dates localisées Guesty
    checkin_localized = reservation.get(
        "checkInDateLocalized"
    )

    checkout_localized = reservation.get(
        "checkOutDateLocalized"
    )

    if checkin_localized:

        checkin = localized_date_to_datetime(
            checkin_localized,
            16,
            0,
            timezone_name,
        )

    if checkout_localized:

        checkout = localized_date_to_datetime(
            checkout_localized,
            10,
            0,
            timezone_name,
        )

    # Fallback
    if not checkin:

        for key in (
            "checkIn",
            "checkInDate",
            "arrivalDate",
        ):

            if reservation.get(key):

                dt = parse_datetime(
                    reservation[key]
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
            "checkOutDate",
            "departureDate",
        ):

            if reservation.get(key):

                dt = parse_datetime(
                    reservation[key]
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

        print("ACCESS DENIED - dates unavailable")
        return False

    print(
        f"Reservation check-in detected: "
        f"{checkin.isoformat()}"
    )

    print(
        f"Reservation check-out detected: "
        f"{checkout.isoformat()}"
    )

    # Autorisation :
    # 24h avant le check-in jusqu'au check-out.
    authorized_from = checkin.replace(
        hour=16,
        minute=0,
        second=0,
        microsecond=0,
    )

    authorized_from = authorized_from.timestamp() - (
        24 * 60 * 60
    )

    authorized_from = datetime.fromtimestamp(
        authorized_from,
        tz=tz,
    )

    authorized_until = checkout.replace(
        hour=10,
        minute=0,
        second=0,
        microsecond=0,
    )

    if authorized_from <= now <= authorized_until:

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
# CONTEXTE PROPRIÉTÉ
# ============================================================

def build_property_context(
    property_data: Dict[str, Any],
    sensitive_access_authorized: bool,
) -> str:

    lines = [

        f"LOGEMENT : {property_data['name']}",

        f"ADRESSE : {property_data['address']}",

        f"CHECK-IN : {property_data['check_in_time']}",

        f"CHECK-OUT : {property_data['check_out_time']}",

        f"ÉTAGE : {property_data['floor']}",

        f"ASCENSEUR : "
        f"{'oui' if property_data['elevator'] else 'non'}",

        f"CHAMBRES : {property_data['bedrooms']}",

        f"SALLES DE BAIN : {property_data['bathrooms']}",

        f"WC : {property_data['wc']}",

        f"CUISINE ÉQUIPÉE : "
        f"{'oui' if property_data['fully_equipped_kitchen'] else 'non'}",

        f"CLIMATISATION : "
        f"{'oui' if property_data['air_conditioning'] else 'non'}",
    ]

    if sensitive_access_authorized:

        lines.extend([

            "",

            "ACCÈS SENSIBLE AUTORISÉ : OUI",

            f"CODE IMMEUBLE : "
            f"{property_data['building_code']}",

            f"CODE BOÎTE À CLÉS : "
            f"{property_data['keybox_code']}",

            f"EMPLACEMENT BOÎTE À CLÉS : "
            f"{property_data['keybox_location']}",

            f"CHEMIN D'ACCÈS : "
            f"{property_data['access_route']}",

            f"VIDÉO D'ACCÈS : "
            f"{property_data['video_url']}",
        ])

    else:

        lines.extend([

            "",

            "ACCÈS SENSIBLE AUTORISÉ : NON",

            "NE JAMAIS donner les codes d'accès.",

            "NE JAMAIS donner le lien vidéo.",

            "NE JAMAIS donner le chemin détaillé.",

            "Tu peux uniquement préciser que "
            "l'appartement est au 1er étage et qu'il n'y a pas d'ascenseur.",

            "Toutes les instructions sont disponibles "
            "sur le guide d'arrivée sur Airbnb.",
        ])

    return "\n".join(lines)


# ============================================================
# HISTORIQUE
# ============================================================

def is_host_post(post: Dict[str, Any]) -> bool:

    post_type = str(
        post.get("type", "")
    )

    return post_type in (
        "fromHost",
        "fromGuesty",
    )


def is_guest_post(post: Dict[str, Any]) -> bool:

    post_type = str(
        post.get("type", "")
    )

    return post_type in (
        "fromGuest",
        "fromThirdParty",
    )


def sanitize_sensitive_text(
    text: str,
    property_data: Dict[str, Any],
    authorized: bool,
) -> str:

    if authorized:
        return text

    replacements = [

        (
            property_data.get("building_code"),
            "[CODE D'ACCÈS MASQUÉ]",
        ),

        (
            property_data.get("keybox_code"),
            "[CODE BOÎTE À CLÉS MASQUÉ]",
        ),

        (
            property_data.get("video_url"),
            "[VIDÉO D'ACCÈS MASQUÉE]",
        ),
    ]

    for secret, replacement in replacements:

        if secret:
            text = text.replace(
                str(secret),
                replacement,
            )

    return text


def build_conversation_history(
    posts: List[Dict[str, Any]],
    property_data: Dict[str, Any],
    authorized: bool,
) -> str:

    history = []

    for post in posts:

        body = clean_message(
            post.get("body")
        )

        if not body:
            continue

        post_type = str(
            post.get("type", "")
        )

        if is_host_post(post):

            role = "HÔTE"

        elif is_guest_post(post):

            role = "VOYAGEUR"

        else:

            # On ignore les logs / événements système
            continue

        body = sanitize_sensitive_text(
            body,
            property_data,
            authorized,
        )

        history.append(
            f"{role}: {body}"
        )

    return "\n".join(history)


# ============================================================
# STYLE
# ============================================================

_style_cache: Dict[
    str,
    Dict[str, Any]
] = {}


def sanitize_style_example(
    text: str,
    property_data: Optional[Dict[str, Any]] = None,
) -> str:

    text = clean_message(text)

    if property_data:

        for secret in (
            property_data.get("building_code"),
            property_data.get("keybox_code"),
            property_data.get("video_url"),
        ):

            if secret:
                text = text.replace(
                    str(secret),
                    "[INFO D'ACCÈS]",
                )

    # Protection générale URLs / emails / téléphones
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
    token: str,
    listing_id: Optional[str],
    property_data: Optional[Dict[str, Any]],
) -> List[str]:

    cache_key = listing_id or "__global__"

    cached = _style_cache.get(cache_key)

    now = time.time()

    if cached:

        if now - cached["timestamp"] < STYLE_CACHE_TTL:

            return cached["examples"]

    try:

        params = {

            "type": "guest",

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

            conversation_id = extract_id(
                conversation
            )

            if not conversation_id:
                continue

            posts = await get_conversation_posts(
                conversation_id,
                STYLE_POSTS_PER_CONVERSATION,
            )

            for post in posts:

                if not is_host_post(post):
                    continue

                if post.get("isAutomatic") is True:
                    continue

                body = clean_message(
                    post.get("body")
                )

                if not body:
                    continue

                body = sanitize_style_example(
                    body,
                    property_data,
                )

                if body:
                    examples.append(body)

                if len(examples) >= 12:
                    break

            if len(examples) >= 12:
                break

        _style_cache[cache_key] = {
            "timestamp": now,
            "examples": examples,
        }

        print(
            f"Style cache refreshed for listing "
            f"{cache_key}: {len(examples)} examples"
        )

        return examples

    except Exception as exc:

        print(
            f"ERROR get_recent_style_examples: {exc}"
        )

        return []


# ============================================================
# RÉPONSE OPENAI
# ============================================================

async def generate_reply(
    guest_name: Optional[str],
    property_data: Dict[str, Any],
    authorized: bool,
    conversation_history: str,
    style_examples: List[str],
) -> str:

    property_context = build_property_context(
        property_data,
        authorized,
    )

    style_context = ""

    if style_examples:

        style_context = (
            "\n\nEXEMPLES DU STYLE DE L'HÔTE "
            "À IMITER UNIQUEMENT POUR LE TON :\n"
        )

        for example in style_examples:

            style_context += (
                f"- {example}\n"
            )

        style_context += (
            "\nIMPORTANT : ces exemples servent uniquement "
            "à apprendre le ton et la manière d'écrire. "
            "N'en déduis aucune information factuelle "
            "sur le logement.\n"
        )

    guest_name_context = (
        f"Prénom du voyageur : {guest_name}"
        if guest_name
        else "Prénom du voyageur inconnu"
    )

    prompt = f"""
{property_context}

{guest_name_context}

HISTORIQUE COMPLET DE LA CONVERSATION :
{conversation_history}

{style_context}

Analyse toute la conversation et réponds au dernier message
du voyageur.

Si plusieurs messages récents du voyageur forment une seule demande,
réponds à toutes les questions dans UNE SEULE réponse.

Réponds comme un vrai hôte Airbnb :
naturel, chaleureux, utile et concis.

Ne parle jamais de ton fonctionnement interne.
Ne dis jamais que tu es une IA.
"""

    try:

        response = await openai_client.responses.create(
            model=OPENAI_MODEL,
            instructions=SYSTEM_RULES,
            input=prompt,
        )

        reply = (
            response.output_text
            if hasattr(response, "output_text")
            else ""
        )

        reply = reply.strip()

        if not reply:
            raise RuntimeError(
                "OpenAI returned an empty response"
            )

        return reply

    except Exception as exc:

        print(
            f"ERROR OpenAI generate_reply: {exc}"
        )

        raise


# ============================================================
# ENVOI MESSAGE
# ============================================================

async def send_reply(
    conversation_id: str,
    reply: str,
):

    if TEST_MODE:

        print("")
        print("==============================")
        print("TEST MODE - MESSAGE NOT SENT")
        print("==============================")
        print("")
        print("AI WOULD REPLY:")
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

    print("MESSAGE SENT SUCCESSFULLY")


# ============================================================
# DÉDUPLICATION
# ============================================================

processed_events: Dict[
    str,
    float
] = {}

PROCESSED_EVENT_TTL = 3600


def cleanup_processed_events():

    now = time.time()

    expired = [

        key
        for key, timestamp
        in processed_events.items()
        if now - timestamp > PROCESSED_EVENT_TTL
    ]

    for key in expired:

        processed_events.pop(
            key,
            None,
        )


def make_event_key(
    payload: Dict[str, Any]
) -> str:

    message = payload.get(
        "message",
        {},
    )

    conversation = payload.get(
        "conversation",
        {},
    )

    event_id = first_value(

        payload.get("eventId"),

        payload.get("id"),

        message.get("postId")
        if isinstance(message, dict)
        else None,

        message.get("_id")
        if isinstance(message, dict)
        else None,

        message.get("id")
        if isinstance(message, dict)
        else None,
    )

    if event_id:

        return str(event_id)

    conversation_id = extract_conversation_id(
        payload
    )

    message_body = ""

    if isinstance(message, dict):

        message_body = clean_message(
            message.get("body")
        )

    raw = (
        f"{conversation_id}|"
        f"{message_body}|"
        f"{message.get('createdAt') if isinstance(message, dict) else ''}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# ============================================================
# DERNIER MESSAGE VOYAGEUR
# ============================================================

def get_latest_guest_message(
    posts: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:

    for post in reversed(posts):

        if not is_guest_post(post):
            continue

        body = clean_message(
            post.get("body")
        )

        if body:
            return post

    return None


def host_replied_after_guest(
    posts: List[Dict[str, Any]],
    guest_post: Dict[str, Any],
) -> bool:

    guest_created = parse_datetime(
        guest_post.get("createdAt")
    )

    if not guest_created:
        return False

    for post in posts:

        if not is_host_post(post):
            continue

        host_created = parse_datetime(
            post.get("createdAt")
        )

        if not host_created:
            continue

        if host_created > guest_created:
            return True

    return False


# ============================================================
# TRAITEMENT PRINCIPAL
# ============================================================

async def process_message(
    payload: Dict[str, Any]
):

    event = payload.get("event")

    if event != "reservation.messageReceived":

        print(
            f"Ignored event: {event}"
        )

        return

    conversation_id = extract_conversation_id(
        payload
    )

    if not conversation_id:

        print(
            "No conversation ID - cannot process"
        )

        return

    conversation = payload.get(
        "conversation"
    ) or {}

    reservation_id = extract_reservation_id(
        payload,
        conversation,
    )

    reservation = None

    if reservation_id:

        reservation = await get_reservation(
            reservation_id
        )

    else:

        print(
            "No reservation ID - inquiry / "
            "pre-booking conversation"
        )

    # --------------------------------------------------------
    # CONVERSATION
    # --------------------------------------------------------

    fresh_conversation = await get_conversation(
        conversation_id
    )

    if fresh_conversation:

        conversation = fresh_conversation

    print(
        "Conversation ID found"
    )

    # --------------------------------------------------------
    # PROPERTY
    # --------------------------------------------------------

    listing_id = extract_listing_id(
        reservation,
        conversation,
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
            f"Unknown listing ID: {listing_id}"
        )

        return

    print(
        f"Property identified: "
        f"{property_data['name']}"
    )

    # --------------------------------------------------------
    # ACCESS
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
    # FULL CONVERSATION
    # --------------------------------------------------------

    posts = await get_conversation_posts(
        conversation_id
    )

    if not posts:

        print(
            "No conversation posts found"
        )

        return

    # --------------------------------------------------------
    # DERNIER MESSAGE VOYAGEUR
    # --------------------------------------------------------

    guest_post = get_latest_guest_message(
        posts
    )

    if not guest_post:

        print(
            "No guest message found"
        )

        return

    # Si l'hôte a déjà répondu après ce message,
    # on ne répond surtout pas une deuxième fois.
    if host_replied_after_guest(
        posts,
        guest_post,
    ):

        print(
            "Host already replied after latest "
            "guest message - skipping"
        )

        return

    guest_message = clean_message(
        guest_post.get("body")
    )

    print(
        f"Guest message: {guest_message}"
    )

    # --------------------------------------------------------
    # NOM
    # --------------------------------------------------------

    guest_name = extract_guest_name(
        conversation,
        reservation,
    )

    # --------------------------------------------------------
    # HISTORIQUE
    # --------------------------------------------------------

    conversation_history = build_conversation_history(
        posts,
        property_data,
        authorized,
    )

    # --------------------------------------------------------
    # STYLE
    # --------------------------------------------------------

    token = await get_guesty_token()

    style_examples = await get_recent_style_examples(
        token,
        listing_id,
        property_data,
    )

    # --------------------------------------------------------
    # OPENAI
    # --------------------------------------------------------

    reply = await generate_reply(
        guest_name=guest_name,
        property_data=property_data,
        authorized=authorized,
        conversation_history=conversation_history,
        style_examples=style_examples,
    )

    # --------------------------------------------------------
    # ENVOI
    # --------------------------------------------------------

    await send_reply(
        conversation_id,
        reply,
    )


# ============================================================
# DEBOUNCE / GROUPAGE DES MESSAGES
# ============================================================

pending_tasks: Dict[
    str,
    asyncio.Task
] = {}


async def delayed_process(
    conversation_id: str,
    payload: Dict[str, Any],
):

    current_task = asyncio.current_task()

    try:

        print(
            f"Waiting {DEBOUNCE_SECONDS}s "
            f"before processing conversation "
            f"{conversation_id}"
        )

        await asyncio.sleep(
            DEBOUNCE_SECONDS
        )

        await process_message(
            payload
        )

    except asyncio.CancelledError:

        print(
            f"Debounce cancelled for "
            f"{conversation_id}"
        )

        raise

    except Exception as exc:

        print(
            f"ERROR delayed_process: {exc}"
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

    existing = pending_tasks.get(
        conversation_id
    )

    if existing:

        existing.cancel()

    task = asyncio.create_task(
        delayed_process(
            conversation_id,
            payload,
        )
    )

    pending_tasks[
        conversation_id
    ] = task


# ============================================================
# WEBHOOK
# ============================================================

@app.post("/guesty/webhook")
async def guesty_webhook(
    request: Request
):

    try:

        payload = await request.json()

    except Exception:

        return {
            "ok": False,
            "error": "Invalid JSON",
        }

    print("")
    print("==============================")
    print("Incoming Guesty webhook")
    print("==============================")

    print(
        f"Event: {payload.get('event')}"
    )

    event = payload.get("event")

    if event != "reservation.messageReceived":

        return {
            "ok": True,
            "ignored": True,
        }

    cleanup_processed_events()

    # --------------------------------------------------------
    # DÉDUPLICATION
    # --------------------------------------------------------

    event_key = make_event_key(
        payload
    )

    if event_key in processed_events:

        print(
            "Duplicate webhook ignored"
        )

        return {
            "ok": True,
            "duplicate": True,
        }

    processed_events[
        event_key
    ] = time.time()

    # --------------------------------------------------------
    # CONVERSATION
    # --------------------------------------------------------

    conversation_id = extract_conversation_id(
        payload
    )

    if not conversation_id:

        print(
            "No conversation ID in webhook"
        )

        return {
            "ok": True,
            "ignored": True,
            "reason": "no_conversation_id",
        }

    # --------------------------------------------------------
    # PLANIFICATION
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

        "properties": len(PROPERTIES),

        "debounce_seconds": DEBOUNCE_SECONDS,

        "openai_timeout_seconds": 30,
    }


@app.get("/")
async def root():

    return {

        "message": "Airbnb AI Agent is running",

        "test_mode": TEST_MODE,

        "properties": list(
            PROPERTIES.keys()
        ),
    }


# ============================================================
# SETUP WEBHOOK
# ============================================================

@app.get("/setup-webhook")
async def setup_webhook():

    return {

        "message": (
            "Webhook already configured. "
            "Do not create another one."
        ),

        "event": "reservation.messageReceived",
    }
