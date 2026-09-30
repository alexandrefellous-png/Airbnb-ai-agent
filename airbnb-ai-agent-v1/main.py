import os
import re
import json
import time
import html
import hashlib
import asyncio
from datetime import datetime, date, time as dt_time, timedelta
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

STYLE_CONVERSATION_LIMIT = 12
STYLE_POSTS_PER_CONVERSATION = 20
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

# IMPORTANT:
# - La clé du dictionnaire = Guesty Listing ID exact.
# - Le nom sert uniquement de libellé humain.
# - L'adresse ne sert JAMAIS à identifier un logement.
# - Si un logement n'est pas configuré ici, l'agent ne répond pas.
#
# CAIRE1 est volontairement configuré avec uniquement les informations
# certaines que nous avons aujourd'hui. Complète les champs None avant
# d'autoriser l'agent à répondre à des questions spécifiques sur ce logement.

PROPERTIES = {
    "6a908557b01e820012493069": {
        "name": "!RUE31",
        "guesty_name": "!RUE31",
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

        # Informations sensibles propres à !RUE31
        "sensitive_access_enabled": True,
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
    },

    "6ab462d882922b001271fe81": {
        "name": "CAIRE1",
        "guesty_name": "CAIRE1",
        "address": "31 rue du Caire, 75002 Paris",
        "timezone": "Europe/Paris",

        # A COMPLETER avec les vraies informations de CAIRE1.
        # Tant que ces champs sont None, l'IA dira qu'elle va vérifier
        # plutôt que d'inventer ou de reprendre les données de !RUE31.
        "check_in_time": None,
        "check_out_time": None,

        "floor": None,
        "elevator": None,

        "bedrooms": None,
        "bathrooms": None,
        "wc": None,

        "fully_equipped_kitchen": None,
        "air_conditioning": None,

        # Surtout ne jamais réutiliser les codes de !RUE31.
        "sensitive_access_enabled": False,
        "building_code": None,
        "keybox_code": None,
        "access_route": None,
        "keybox_location": None,
        "video_url": None,
        "arrival_guide": (
            "Toutes les instructions d'arrivée disponibles doivent être "
            "prises depuis les informations propres à cette réservation."
        ),
    },
}

# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_RULES = """
Tu es l'assistant Airbnb d'un hôte.

OBJECTIF :
Répondre aux voyageurs comme un vrai hôte humain :
naturellement, chaleureusement, intelligemment et de manière concise.

SOURCE DE VÉRITÉ :
Le serveur te fournit un bloc CONTEXTE GUESTY VÉRIFIÉ.
Il contient l'identité technique du logement, le type de conversation,
le statut de réservation, les dates de séjour connues et, quand nécessaire,
le résultat du calendrier Guesty.

Tu dois toujours considérer ce contexte serveur comme prioritaire.

RÈGLES MULTI-LOGEMENTS :
- Ne déduis JAMAIS le logement à partir de l'adresse, du texte du voyageur ou d'un ancien exemple.
- Plusieurs logements peuvent avoir exactement la même adresse.
- Le logement courant a déjà été sélectionné côté serveur par son Guesty Listing ID.
- N'utilise que les informations du LOGEMENT COURANT fourni.
- Ne mélange jamais les équipements, horaires, codes ou instructions de deux logements.
- Ne révèle jamais au voyageur les IDs techniques Guesty.

DATES / RÉSERVATION :
- Si le contexte indique une réservation Guesty, les dates de check-in et check-out fournies par Guesty font foi.
- Ne réinterprète pas ces dates à partir de la conversation.
- Si le voyageur parle d'une NOUVELLE période ou demande une disponibilité différente,
  utilise uniquement le bloc CALENDRIER LIVE correspondant à cette demande.
- Une réservation confirmée ne doit pas être "revalidée" à partir du calendrier :
  sa propre réservation peut naturellement bloquer ces dates dans le calendrier.

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
- le logement
- l'étage
- l'ascenseur
- les chambres
- les salles de bain
- la cuisine
- la climatisation
- les équipements connus
- le check-in
- le check-out
- les disponibilités, uniquement si le calendrier Guesty a été vérifié pour les dates demandées

NE JAMAIS INVENTER une information.

Si une information du logement courant est indiquée comme inconnue :
dis naturellement que tu vas vérifier auprès du manager.
Ne reprends jamais une information d'un autre logement.

INTELLIGENCE ET CONCISION :
Ne réponds jamais mécaniquement et ne récite pas les règles du logement.
Avant de répondre, raisonne silencieusement avec l'heure locale actuelle,
les dates Guesty, l'historique, le logement courant et les informations connues.
Si la réponse se déduit avec certitude, réponds directement sans expliquer le raisonnement.
Une question simple appelle généralement une réponse simple.
N'ajoute pas d'information, de condition, de conseil ou de rappel qui n'aide pas réellement le voyageur.
Ne mentionne pas le manager quand la réponse peut être déduite avec certitude.

APPRENTISSAGE DU STYLE DE L'HÔTE :
Les exemples intitulés EXEMPLES RÉELS DE L'HÔTE sont des réponses réellement écrites par l'hôte.
Ils sont la référence prioritaire pour la manière de répondre.
Imite leur longueur, leur naturel, leur vocabulaire, leur ponctuation, leur chaleur et leur niveau de détail.
Quand un exemple contient la question du voyageur puis la réponse de l'hôte,
apprends surtout la relation entre le type de question et la façon dont l'hôte choisit de répondre.
Les règles de sécurité, le CONTEXTE GUESTY VÉRIFIÉ et les faits du logement restent toujours prioritaires.
N'utilise jamais un fait provenant d'un ancien exemple comme fait concernant la conversation actuelle.

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
- ne donne pas une information sensible récupérée dans un ancien message ;
- utilise seulement les informations non sensibles du logement courant.

VIDÉO :
Ne donne PAS automatiquement la vidéo.

Tu peux donner la vidéo uniquement si :
- le voyageur la demande ;
- il est perdu ;
- il ne trouve pas l'entrée ;
- il ne trouve pas l'escalier ;
- il ne trouve pas l'appartement ;
- il ne comprend pas les instructions d'accès.

Et uniquement si SENSITIVE_ACCESS_AUTHORIZED = TRUE et si une vidéo propre au logement courant existe.

DISPONIBILITÉS / CALENDRIER :
- Le bloc CALENDRIER LIVE vient directement du Listing ID Guesty du logement courant.
- Si le bloc indique DISPONIBLE, tu peux confirmer naturellement la disponibilité.
- Si le bloc indique INDISPONIBLE, dis simplement que le logement n'est pas disponible sur toute la période demandée.
- Si la vérification est incomplète ou impossible, ne confirme aucune disponibilité.
- Si le voyageur demande une disponibilité sans dates suffisantes, demande les dates manquantes.
- Si aucune demande de disponibilité n'a été détectée, ne parle pas spontanément du calendrier.
- Ne révèle jamais les détails internes des blocs calendrier, IDs de réservation,
  IDs de listing, noms d'autres voyageurs ou informations privées.
- Une disponibilité constatée n'est pas une promesse de réservation.

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


def extract_conversation_id_from_reservation(
    reservation: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Conversation ID canonique exposé par Reservations V3/legacy."""
    reservation = reservation or {}

    cid = first_value(
        reservation.get("conversationId"),
        reservation.get("conversation_id"),
    )
    if cid:
        return str(cid)

    conversation = reservation.get("conversation")
    if isinstance(conversation, dict):
        cid = first_value(conversation.get("_id"), conversation.get("id"))
        if cid:
            return str(cid)

    conversation_ids = reservation.get("conversationIds")
    if isinstance(conversation_ids, list) and len(conversation_ids) == 1:
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
    """
    Extrait l'ID de réservation sans choisir arbitrairement une ancienne
    réservation d'une conversation Guesty.

    Priorité Guesty :
    1. reservationId top-level du webhook reservation.messageReceived ;
    2. message.reservationId quand présent ;
    3. objet reservation embarqué ;
    4. conversation.meta.reservations uniquement s'il n'y en a QU'UNE.

    Une conversation Guesty peut contenir plusieurs réservations : dans ce cas,
    on ne prend jamais la première au hasard.
    """
    reservation_id = first_value(
        payload.get("reservationId"),
        payload.get("reservation_id"),
    )

    if reservation_id:
        return str(reservation_id)

    message = payload.get("message")
    if isinstance(message, dict):
        reservation_id = first_value(
            message.get("reservationId"),
            message.get("reservation_id"),
        )
        if reservation_id:
            return str(reservation_id)

    reservation = payload.get("reservation")
    if isinstance(reservation, dict):
        reservation_id = first_value(
            reservation.get("_id"),
            reservation.get("id"),
            reservation.get("reservationId"),
        )
        if reservation_id:
            return str(reservation_id)

    conversation = payload.get("conversation")
    if isinstance(conversation, dict):
        meta = conversation.get("meta")
        if isinstance(meta, dict):
            reservations = meta.get("reservations")
            if isinstance(reservations, list):
                ids = []
                for item in reservations:
                    if not isinstance(item, dict):
                        continue
                    rid = first_value(
                        item.get("_id"),
                        item.get("id"),
                        item.get("reservationId"),
                    )
                    if rid and str(rid) not in ids:
                        ids.append(str(rid))

                # Fail closed : une conversation peut regrouper plusieurs séjours.
                if len(ids) == 1:
                    return ids[0]

    return None


def _add_listing_candidate(
    candidates: List[Dict[str, str]],
    source: str,
    value: Any,
):
    if value in (None, "", [], {}):
        return

    if isinstance(value, dict):
        value = first_value(
            value.get("_id"),
            value.get("id"),
        )

    if value in (None, ""):
        return

    candidates.append({
        "source": source,
        "value": str(value),
    })


def collect_listing_candidates(
    reservation: Optional[Dict[str, Any]],
    conversation: Optional[Dict[str, Any]],
    payloads: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """
    Collecte les identifiants Guesty pouvant désigner le logement.

    IMPORTANT - Reservations V3 :
    Guesty renvoie principalement `unitTypeId` et `unitId` (et non forcément
    `listingId`). Pour une SINGLE listing, les deux correspondent au Listing ID.
    Pour une multi-unit, unitTypeId = parent et unitId = sous-unité.

    On collecte toutes les preuves puis resolve_listing_id() n'accepte qu'un
    seul ID qui corresponde à PROPERTIES. Aucun fallback par adresse ou nom.
    """
    reservation = reservation or {}
    conversation = conversation or {}
    payloads = payloads or []

    candidates: List[Dict[str, str]] = []

    def add_reservation_fields(prefix: str, obj: Dict[str, Any]):
        # Legacy / champs explicites listing
        _add_listing_candidate(candidates, f"{prefix}.listingId", obj.get("listingId"))
        _add_listing_candidate(candidates, f"{prefix}.lastStayListingId", obj.get("lastStayListingId"))
        _add_listing_candidate(candidates, f"{prefix}.listing", obj.get("listing"))

        # Reservations V3 : champs principaux documentés par Guesty.
        _add_listing_candidate(candidates, f"{prefix}.unitTypeId", obj.get("unitTypeId"))
        _add_listing_candidate(candidates, f"{prefix}.unitId", obj.get("unitId"))

        # Certaines réponses portent les IDs dans stay[].
        stay = obj.get("stay")
        if isinstance(stay, list):
            for stay_index, item in enumerate(stay):
                if not isinstance(item, dict):
                    continue
                _add_listing_candidate(
                    candidates,
                    f"{prefix}.stay[{stay_index}].listingId",
                    item.get("listingId"),
                )
                _add_listing_candidate(
                    candidates,
                    f"{prefix}.stay[{stay_index}].unitTypeId",
                    item.get("unitTypeId"),
                )
                _add_listing_candidate(
                    candidates,
                    f"{prefix}.stay[{stay_index}].unitId",
                    item.get("unitId"),
                )
                _add_listing_candidate(
                    candidates,
                    f"{prefix}.stay[{stay_index}].listing",
                    item.get("listing"),
                )

    # Réservation fraîche Guesty = source prioritaire.
    add_reservation_fields("reservation", reservation)

    # Conversation : certains contrats/anciennes réponses peuvent exposer listing.
    _add_listing_candidate(candidates, "conversation.listingId", conversation.get("listingId"))
    _add_listing_candidate(candidates, "conversation.lastStayListingId", conversation.get("lastStayListingId"))
    _add_listing_candidate(candidates, "conversation.unitTypeId", conversation.get("unitTypeId"))
    _add_listing_candidate(candidates, "conversation.unitId", conversation.get("unitId"))
    _add_listing_candidate(candidates, "conversation.listing", conversation.get("listing"))

    conv_reservation = conversation.get("reservation")
    if isinstance(conv_reservation, dict):
        add_reservation_fields("conversation.reservation", conv_reservation)

    meta = conversation.get("meta")
    if isinstance(meta, dict):
        _add_listing_candidate(candidates, "conversation.meta.listingId", meta.get("listingId"))
        _add_listing_candidate(candidates, "conversation.meta.lastStayListingId", meta.get("lastStayListingId"))
        _add_listing_candidate(candidates, "conversation.meta.unitTypeId", meta.get("unitTypeId"))
        _add_listing_candidate(candidates, "conversation.meta.unitId", meta.get("unitId"))
        _add_listing_candidate(candidates, "conversation.meta.listing", meta.get("listing"))

        reservations = meta.get("reservations")
        if isinstance(reservations, list):
            for index, item in enumerate(reservations):
                if not isinstance(item, dict):
                    continue
                add_reservation_fields(
                    f"conversation.meta.reservations[{index}]",
                    item,
                )

    # Webhooks regroupés.
    for index, payload in enumerate(payloads):
        if not isinstance(payload, dict):
            continue

        _add_listing_candidate(candidates, f"payload[{index}].listingId", payload.get("listingId"))
        _add_listing_candidate(candidates, f"payload[{index}].lastStayListingId", payload.get("lastStayListingId"))
        _add_listing_candidate(candidates, f"payload[{index}].unitTypeId", payload.get("unitTypeId"))
        _add_listing_candidate(candidates, f"payload[{index}].unitId", payload.get("unitId"))
        _add_listing_candidate(candidates, f"payload[{index}].listing", payload.get("listing"))

        payload_reservation = payload.get("reservation")
        if isinstance(payload_reservation, dict):
            add_reservation_fields(
                f"payload[{index}].reservation",
                payload_reservation,
            )

        payload_conversation = payload.get("conversation")
        if isinstance(payload_conversation, dict):
            _add_listing_candidate(
                candidates,
                f"payload[{index}].conversation.listingId",
                payload_conversation.get("listingId"),
            )
            _add_listing_candidate(
                candidates,
                f"payload[{index}].conversation.lastStayListingId",
                payload_conversation.get("lastStayListingId"),
            )
            _add_listing_candidate(
                candidates,
                f"payload[{index}].conversation.unitTypeId",
                payload_conversation.get("unitTypeId"),
            )
            _add_listing_candidate(
                candidates,
                f"payload[{index}].conversation.unitId",
                payload_conversation.get("unitId"),
            )
            _add_listing_candidate(
                candidates,
                f"payload[{index}].conversation.listing",
                payload_conversation.get("listing"),
            )

            payload_meta = payload_conversation.get("meta")
            if isinstance(payload_meta, dict):
                _add_listing_candidate(
                    candidates,
                    f"payload[{index}].conversation.meta.listingId",
                    payload_meta.get("listingId"),
                )
                _add_listing_candidate(
                    candidates,
                    f"payload[{index}].conversation.meta.lastStayListingId",
                    payload_meta.get("lastStayListingId"),
                )
                _add_listing_candidate(
                    candidates,
                    f"payload[{index}].conversation.meta.unitTypeId",
                    payload_meta.get("unitTypeId"),
                )
                _add_listing_candidate(
                    candidates,
                    f"payload[{index}].conversation.meta.unitId",
                    payload_meta.get("unitId"),
                )
                _add_listing_candidate(
                    candidates,
                    f"payload[{index}].conversation.meta.listing",
                    payload_meta.get("listing"),
                )

    # Déduplication tout en gardant les sources pour les logs.
    unique: List[Dict[str, str]] = []
    seen = set()

    for candidate in candidates:
        key = (candidate["source"], candidate["value"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    return unique


def resolve_listing_id(
    reservation: Optional[Dict[str, Any]],
    conversation: Optional[Dict[str, Any]],
    payloads: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Fail closed:
    - exactement un Listing ID configuré détecté => OK ;
    - plusieurs Listing IDs configurés détectés => conflit, aucune réponse ;
    - aucun Listing ID configuré => aucune réponse.

    L'adresse n'est jamais utilisée.
    """
    evidence = collect_listing_candidates(
        reservation=reservation,
        conversation=conversation,
        payloads=payloads,
    )

    configured_ids = []
    for item in evidence:
        value = item["value"]
        if value in PROPERTIES and value not in configured_ids:
            configured_ids.append(value)

    if len(configured_ids) == 1:
        listing_id = configured_ids[0]
        return {
            "ok": True,
            "listing_id": listing_id,
            "property_data": PROPERTIES[listing_id],
            "evidence": evidence,
            "reason": None,
        }

    if len(configured_ids) > 1:
        return {
            "ok": False,
            "listing_id": None,
            "property_data": None,
            "evidence": evidence,
            "reason": "conflicting_configured_listing_ids",
        }

    raw_ids = []
    for item in evidence:
        value = item["value"]
        if value not in raw_ids:
            raw_ids.append(value)

    return {
        "ok": False,
        "listing_id": None,
        "property_data": None,
        "evidence": evidence,
        "reason": (
            "unknown_listing_id"
            if raw_ids
            else "listing_id_not_found"
        ),
    }


def extract_date_iso(
    value: Any,
    timezone_name: str,
) -> Optional[str]:
    if not value:
        return None

    if isinstance(value, datetime):
        return localize_datetime(
            value,
            timezone_name,
        ).date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()

    if not text:
        return None

    try:
        # Les champs Guesty "Localized" sont souvent YYYY-MM-DD.
        return date.fromisoformat(text[:10]).isoformat()
    except Exception:
        pass

    dt = parse_datetime(text)

    if dt:
        return localize_datetime(
            dt,
            timezone_name,
        ).date().isoformat()

    return None


def extract_reservation_stay(
    reservation: Optional[Dict[str, Any]],
    timezone_name: str,
) -> Dict[str, Optional[str]]:
    reservation = reservation or {}

    check_in = None
    check_out = None

    for key in (
        "checkInDateLocalized",
        "checkinDateLocalized",
        "checkInDate",
        "checkIn",
        "checkin",
        "arrivalDate",
    ):
        check_in = extract_date_iso(
            reservation.get(key),
            timezone_name,
        )
        if check_in:
            break

    for key in (
        "checkOutDateLocalized",
        "checkoutDateLocalized",
        "checkOutDate",
        "checkOut",
        "checkout",
        "departureDate",
    ):
        check_out = extract_date_iso(
            reservation.get(key),
            timezone_name,
        )
        if check_out:
            break

    if check_in and check_out:
        try:
            if date.fromisoformat(check_out) <= date.fromisoformat(check_in):
                print("Invalid reservation stay dates - ignoring")
                check_in = None
                check_out = None
        except Exception:
            check_in = None
            check_out = None

    return {
        "check_in": check_in,
        "check_out": check_out,
    }


def build_booking_context(
    conversation_id: str,
    listing_id: str,
    property_data: Dict[str, Any],
    reservation_id: Optional[str],
    reservation: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    timezone_name = property_data.get(
        "timezone",
        "Europe/Paris",
    )

    stay = extract_reservation_stay(
        reservation,
        timezone_name,
    )

    status = None
    if reservation:
        raw_status = reservation.get("status")
        if raw_status not in (None, ""):
            status = str(raw_status).strip().lower()

    if reservation:
        if status in ("confirmed", "reserved"):
            conversation_type = "CONFIRMED_RESERVATION"
        else:
            conversation_type = "RESERVATION_OTHER_STATUS"
    else:
        conversation_type = "INQUIRY"

    return {
        "conversation_id": conversation_id,
        "conversation_type": conversation_type,
        "listing_id": listing_id,
        "property_name": property_data.get("name"),
        "reservation_id": reservation_id,
        "reservation_status": status,
        "reservation_check_in": stay.get("check_in"),
        "reservation_check_out": stay.get("check_out"),
    }


def booking_context_to_prompt(
    booking_context: Dict[str, Any],
) -> str:
    return (
        "CONTEXTE GUESTY VÉRIFIÉ :\n"
        f"TYPE_CONVERSATION = {booking_context.get('conversation_type')}\n"
        f"LOGEMENT = {booking_context.get('property_name')}\n"
        f"LISTING_ID_INTERNE = {booking_context.get('listing_id')}\n"
        f"RESERVATION_ID_INTERNE = {booking_context.get('reservation_id') or 'AUCUNE'}\n"
        f"RESERVATION_STATUS = {booking_context.get('reservation_status') or 'AUCUN'}\n"
        f"RESERVATION_CHECK_IN = {booking_context.get('reservation_check_in') or 'INCONNU'}\n"
        f"RESERVATION_CHECK_OUT = {booking_context.get('reservation_check_out') or 'INCONNU'}\n"
        "Les IDs sont internes et ne doivent jamais être révélés au voyageur."
    )

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


def parse_hhmm(
    value: Any,
) -> Optional[tuple[int, int]]:
    if not value:
        return None

    text = str(value).strip()

    match = re.fullmatch(
        r"(\d{1,2}):(\d{2})",
        text,
    )

    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))

    if not (
        0 <= hour <= 23
        and 0 <= minute <= 59
    ):
        return None

    return hour, minute


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

    # Chaque logement doit explicitement autoriser sa propre logique d'accès.
    if not property_data.get(
        "sensitive_access_enabled",
        False,
    ):
        print(
            "ACCESS DENIED - sensitive access is disabled "
            f"for {property_data.get('name')}"
        )
        return False

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

    # Les horaires doivent venir DU logement courant, jamais d'une règle globale.
    checkin_hhmm = parse_hhmm(
        property_data.get(
            "check_in_time"
        )
    )
    checkout_hhmm = parse_hhmm(
        property_data.get(
            "check_out_time"
        )
    )

    if not checkin_hhmm or not checkout_hhmm:
        print(
            "ACCESS DENIED - property check-in/check-out "
            "times are not configured"
        )
        return False

    # Les secrets eux-mêmes doivent être configurés pour ce logement.
    if not (
        property_data.get("building_code")
        and property_data.get("keybox_code")
        and property_data.get("access_route")
    ):
        print(
            "ACCESS DENIED - property access secrets incomplete"
        )
        return False

    timezone_name = property_data.get(
        "timezone",
        "Europe/Paris",
    )

    tz = ZoneInfo(
        timezone_name
    )

    now = datetime.now(tz)

    stay = extract_reservation_stay(
        reservation,
        timezone_name,
    )

    if not (
        stay.get("check_in")
        and stay.get("check_out")
    ):
        print(
            "ACCESS DENIED - reservation dates unavailable"
        )
        return False

    checkin = make_local_date_time(
        stay["check_in"],
        checkin_hhmm[0],
        checkin_hhmm[1],
        timezone_name,
    )

    checkout = make_local_date_time(
        stay["check_out"],
        checkout_hhmm[0],
        checkout_hhmm[1],
        timezone_name,
    )

    if not checkin or not checkout:
        print(
            "ACCESS DENIED - could not build local stay datetimes"
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

    authorized_from = checkin - timedelta(
        hours=24
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

def display_value(
    value: Any,
) -> str:
    if value is None:
        return "INCONNU"
    return str(value)


def display_bool(
    value: Any,
) -> str:
    if value is True:
        return "oui"
    if value is False:
        return "non"
    return "INCONNU"


def build_property_context(
    listing_id: str,
    property_data: Dict[str, Any],
    authorized: bool,
) -> str:

    address = (
        property_data.get("address")
        if authorized
        else "Adresse exacte non communicable avant autorisation d'accès"
    )

    context = f"""
LOGEMENT COURANT :
{display_value(property_data.get("name"))}

NOM GUESTY :
{display_value(property_data.get("guesty_name"))}

LISTING ID INTERNE :
{listing_id}
Ne jamais communiquer cet ID au voyageur.

ADRESSE :
{display_value(address)}

CHECK-IN :
{display_value(property_data.get("check_in_time"))}

CHECK-OUT :
{display_value(property_data.get("check_out_time"))}

ÉTAGE :
{display_value(property_data.get("floor"))}

ASCENSEUR :
{display_bool(property_data.get("elevator"))}

CHAMBRES :
{display_value(property_data.get("bedrooms"))}

SALLES DE BAIN :
{display_value(property_data.get("bathrooms"))}

WC :
{display_value(property_data.get("wc"))}

CUISINE ÉQUIPÉE :
{display_bool(property_data.get("fully_equipped_kitchen"))}

CLIMATISATION :
{display_bool(property_data.get("air_conditioning"))}

SENSITIVE_ACCESS_AUTHORIZED :
{str(authorized).upper()}
"""

    if authorized:
        context += f"""

INFORMATIONS D'ACCÈS AUTORISÉES POUR CE LOGEMENT UNIQUEMENT :

CODE IMMEUBLE :
{display_value(property_data.get("building_code"))}

CODE BOÎTE À CLÉS :
{display_value(property_data.get("keybox_code"))}

EMPLACEMENT BOÎTE À CLÉS :
{display_value(property_data.get("keybox_location"))}

CHEMIN D'ACCÈS :
{display_value(property_data.get("access_route"))}

VIDÉO D'ACCÈS :
{display_value(property_data.get("video_url"))}
"""

    else:
        context += f"""

INFORMATIONS D'ACCÈS SENSIBLES INTERDITES.

NE DONNE PAS :
- le code immeuble
- le code boîte à clés
- le chemin détaillé
- le lien vidéo
- des codes ou instructions provenant d'un autre logement

GUIDE D'ARRIVÉE NON SENSIBLE :
{display_value(property_data.get("arrival_guide"))}
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

    cache_key = listing_id or "__global__"
    cached = _style_cache.get(cache_key)
    now = time.time()

    if cached and now - cached["timestamp"] < STYLE_CACHE_TTL:
        return cached["examples"]

    try:
        params = {
            "limit": STYLE_CONVERSATION_LIMIT,
            "sort": "-createdAt",
        }

        if listing_id:
            filters = [{
                "field": "listing._id",
                "operator": "$eq",
                "value": listing_id,
            }]
            params["filters"] = json.dumps(filters, separators=(",", ":"))

        payload = await guesty_request(
            "GET",
            "/communication/conversations",
            params=params,
        )
        conversations = extract_results(payload)
        examples: List[str] = []

        # Learn only from HUMAN host replies. Pair each one with the guest
        # message immediately before it, so the model learns not only wording
        # but also how much detail the host uses for each kind of question.
        for conversation in conversations:
            conversation_id = extract_object_id(conversation)
            if not conversation_id:
                continue

            posts = await get_conversation_posts(conversation_id)
            previous_guest = None

            for post in posts[-STYLE_POSTS_PER_CONVERSATION:]:
                body = post_text(post)
                if not body:
                    continue

                if is_guest_post(post):
                    previous_guest = sanitize_style_example(body, property_data)
                    continue

                if not is_host_post(post):
                    continue

                # Never let the AI learn from its own/automatic messages.
                if post.get("isAutomatic") is True:
                    continue

                host_body = sanitize_style_example(body, property_data)
                if not host_body:
                    continue

                if previous_guest:
                    example = (
                        f"VOYAGEUR: {previous_guest}\n"
                        f"HÔTE HUMAIN: {host_body}"
                    )
                else:
                    example = f"HÔTE HUMAIN: {host_body}"

                if example not in examples:
                    examples.append(example)

                if len(examples) >= 30:
                    break

            if len(examples) >= 30:
                break

        _style_cache[cache_key] = {
            "timestamp": now,
            "examples": examples,
        }

        print(
            f"Human style cache refreshed for listing {cache_key}: "
            f"{len(examples)} examples"
        )
        return examples

    except Exception as exc:
        print(f"ERROR style cache: {exc}")
        return []


# ============================================================
# LIVE AVAILABILITY / CALENDAR
# ============================================================

async def extract_availability_request(
    latest_guest_message: str,
    history: str,
    booking_context: Dict[str, Any],
    timezone_name: str,
) -> Dict[str, Any]:
    """
    Détermine si le DERNIER message demande réellement une disponibilité
    pour une période nouvelle / à vérifier.

    Important :
    - les dates d'une réservation existante restent celles de Guesty ;
    - on n'interroge pas le calendrier juste parce que des dates de réservation
      apparaissent dans l'historique ;
    - pour une extension clairement formulée, les dates Guesty de la réservation
      peuvent servir de point d'ancrage.
    """
    today = datetime.now(
        ZoneInfo(timezone_name)
    ).date().isoformat()

    reservation_check_in = booking_context.get(
        "reservation_check_in"
    )
    reservation_check_out = booking_context.get(
        "reservation_check_out"
    )

    prompt = f"""
Today in the property's timezone is {today}.

You analyze the LAST Airbnb guest message to decide whether the guest is asking
about availability for a stay period that must be checked in the live calendar.

CURRENT VERIFIED RESERVATION CONTEXT:
conversation_type = {booking_context.get("conversation_type")}
reservation_check_in = {reservation_check_in or "NONE"}
reservation_check_out = {reservation_check_out or "NONE"}

LAST GUEST MESSAGE:
{latest_guest_message}

CONVERSATION HISTORY:
{history}

Return ONLY valid JSON:
{{
  "availability_question": true or false,
  "check_in": "YYYY-MM-DD" or null,
  "check_out": "YYYY-MM-DD" or null,
  "reason": "short internal reason"
}}

Rules:
1. Set availability_question=true only when the last guest message asks whether
   dates are available, asks to book/stay for dates, asks to extend/shorten into
   a new period that requires availability, or otherwise clearly requires a
   calendar availability check.
2. Do NOT set it true merely because the conversation contains dates.
3. Do NOT use the calendar to confirm an already-confirmed reservation.
4. If the guest is just discussing arrival/check-in/check-out timing for their
   existing reservation, availability_question=false.
5. For a pre-booking inquiry like "is it available?" with no dates,
   availability_question=true and both dates null.
6. Resolve relative dates only when unambiguous.
7. You may use the verified reservation_check_out as the start of an extension
   only when the guest clearly asks to extend beyond the current stay.
8. Never invent a missing date or year.
9. check_out must be strictly after check_in.
"""

    try:
        response = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify Airbnb availability requests and extract "
                        "dates. Output valid JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        raw = (
            response.choices[0].message.content
            or ""
        ).strip()

        raw = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            raw,
            flags=re.I | re.S,
        ).strip()

        data = json.loads(raw)

        availability_question = bool(
            data.get("availability_question")
        )

        check_in = data.get("check_in")
        check_out = data.get("check_out")

        if not availability_question:
            return {
                "availability_question": False,
                "check_in": None,
                "check_out": None,
                "reason": str(
                    data.get("reason")
                    or "not_an_availability_request"
                ),
            }

        if not check_in or not check_out:
            return {
                "availability_question": True,
                "check_in": None,
                "check_out": None,
                "reason": str(
                    data.get("reason")
                    or "dates_missing"
                ),
            }

        ci_d = date.fromisoformat(
            str(check_in)
        )
        co_d = date.fromisoformat(
            str(check_out)
        )

        if co_d <= ci_d:
            return {
                "availability_question": True,
                "check_in": None,
                "check_out": None,
                "reason": "invalid_date_order",
            }

        if (
            co_d - ci_d
        ).days > 365:
            return {
                "availability_question": True,
                "check_in": None,
                "check_out": None,
                "reason": "stay_too_long",
            }

        return {
            "availability_question": True,
            "check_in": ci_d.isoformat(),
            "check_out": co_d.isoformat(),
            "reason": str(
                data.get("reason")
                or "availability_dates_extracted"
            ),
        }

    except Exception as exc:
        print(
            f"Availability request extraction unavailable: {exc}"
        )

        # Fail closed : aucune disponibilité n'est confirmée.
        return {
            "availability_question": False,
            "check_in": None,
            "check_out": None,
            "reason": "extractor_error",
        }


def extract_calendar_days(
    payload: Any,
) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [
            item
            for item in payload
            if isinstance(item, dict)
        ]

    if not isinstance(payload, dict):
        return []

    for key in (
        "days",
        "data",
        "results",
    ):
        value = payload.get(key)

        if isinstance(value, list):
            return [
                item
                for item in value
                if isinstance(item, dict)
            ]

        if (
            isinstance(value, dict)
            and isinstance(
                value.get("days"),
                list,
            )
        ):
            return [
                item
                for item in value["days"]
                if isinstance(item, dict)
            ]

    return []


def calendar_day_date(
    day: Dict[str, Any],
) -> Optional[str]:
    raw = first_value(
        day.get("date"),
        day.get("startDate"),
        day.get("calendarDate"),
    )

    if not raw:
        return None

    try:
        return date.fromisoformat(
            str(raw)[:10]
        ).isoformat()
    except Exception:
        return None


def calendar_day_is_available(
    day: Dict[str, Any],
) -> bool:
    allotment = day.get("allotment")

    if isinstance(
        allotment,
        (int, float),
    ):
        return allotment > 0

    status = str(
        day.get(
            "status",
            "",
        )
    ).strip().lower()

    return status in (
        "available",
        "open",
    )


async def get_live_availability_context(
    listing_id: str,
    availability_request: Dict[str, Any],
) -> str:

    if not availability_request.get(
        "availability_question"
    ):
        return (
            "CALENDRIER LIVE : AUCUNE DEMANDE DE DISPONIBILITÉ "
            "DÉTECTÉE DANS LE DERNIER MESSAGE. "
            "Ne parle pas spontanément de disponibilité."
        )

    check_in = availability_request.get(
        "check_in"
    )
    check_out = availability_request.get(
        "check_out"
    )

    if not check_in or not check_out:
        return (
            "CALENDRIER LIVE : le voyageur demande une disponibilité "
            "mais aucune période complète et non ambiguë n'a pu être "
            "déterminée. Demande naturellement les dates d'arrivée et "
            "de départ manquantes."
        )

    check_in_date = date.fromisoformat(
        check_in
    )
    check_out_date = date.fromisoformat(
        check_out
    )

    # Les nuits vont du check-in jusqu'à la veille du check-out.
    expected_dates = []
    cursor = check_in_date

    while cursor < check_out_date:
        expected_dates.append(
            cursor.isoformat()
        )
        cursor += timedelta(
            days=1
        )

    last_night = expected_dates[-1]

    try:
        # CRITIQUE : l'URL contient exactement le Listing ID déjà vérifié.
        payload = await guesty_request(
            "GET",
            (
                "/availability-pricing/api/calendar/"
                f"listings/{listing_id}"
            ),
            params={
                "startDate": check_in,
                "endDate": last_night,
                "includeAllotment": "true",
            },
        )

        days = extract_calendar_days(
            payload
        )

        by_date: Dict[str, Dict[str, Any]] = {}

        for day in days:
            day_date = calendar_day_date(
                day
            )
            if day_date:
                by_date[day_date] = day

        missing_dates = [
            day_date
            for day_date in expected_dates
            if day_date not in by_date
        ]

        if missing_dates:
            print(
                "Calendar incomplete for "
                f"{listing_id} {check_in} -> {check_out}. "
                f"Missing: {missing_dates}"
            )

            return (
                "CALENDRIER LIVE : vérification Guesty incomplète "
                f"pour {check_in} → {check_out}. "
                "Ne confirme pas la disponibilité ; indique que tu vas vérifier."
            )

        unavailable_dates = [
            day_date
            for day_date in expected_dates
            if not calendar_day_is_available(
                by_date[day_date]
            )
        ]

        if unavailable_dates:
            print(
                "LIVE CALENDAR: unavailable "
                f"listing={listing_id} "
                f"{check_in} -> {check_out}"
            )

            return (
                "CALENDRIER LIVE GUESTY : "
                f"période demandée {check_in} → {check_out} = INDISPONIBLE. "
                "Le calendrier interrogé est celui du logement courant uniquement. "
                "Ne révèle pas les raisons internes ni les dates bloquées."
            )

        print(
            "LIVE CALENDAR: available "
            f"listing={listing_id} "
            f"{check_in} -> {check_out}"
        )

        return (
            "CALENDRIER LIVE GUESTY : "
            f"période demandée {check_in} → {check_out} = DISPONIBLE "
            "au moment de la vérification. "
            "Le calendrier interrogé est celui du logement courant uniquement."
        )

    except Exception as exc:
        print(
            f"ERROR live calendar for listing {listing_id}: {exc}"
        )

        return (
            "CALENDRIER LIVE : Guesty n'a pas pu être vérifié "
            f"pour {check_in} → {check_out}. "
            "Ne confirme pas la disponibilité ; indique que tu vas vérifier."
        )

# ============================================================
# OPENAI
# ============================================================

async def generate_reply(
    guest_name: Optional[str],
    listing_id: str,
    property_data: Dict[str, Any],
    booking_context: Dict[str, Any],
    authorized: bool,
    history: str,
    style_examples: List[str],
    availability_context: str,
) -> str:

    property_context = build_property_context(
        listing_id=listing_id,
        property_data=property_data,
        authorized=authorized,
    )

    verified_booking_context = booking_context_to_prompt(
        booking_context
    )

    style_context = ""

    if style_examples:
        style_context = "\n\nEXEMPLES RÉELS DE L'HÔTE :\n"

        for i, example in enumerate(
            style_examples,
            1,
        ):
            style_context += (
                f"\nEXEMPLE {i}:\n"
                f"{example}\n"
            )

        style_context += """

Ces exemples ont été écrits par l'hôte humain.
Utilise-les comme référence prioritaire pour le STYLE et le NIVEAU DE DÉTAIL.
N'utilise jamais leur contenu factuel comme information sur la conversation actuelle.
"""

    guest_name_context = (
        f"Prénom du voyageur : {guest_name}"
        if guest_name
        else "Prénom du voyageur inconnu"
    )

    timezone_name = property_data.get(
        "timezone",
        "Europe/Paris",
    )

    current_local_datetime = datetime.now(
        ZoneInfo(timezone_name)
    )

    current_time_context = (
        current_local_datetime.strftime(
            "%A %d/%m/%Y %H:%M"
        )
    )

    user_prompt = f"""
{verified_booking_context}

{property_context}

HEURE LOCALE ACTUELLE DU LOGEMENT :
{current_time_context}

{guest_name_context}

{availability_context}

HISTORIQUE DE CONVERSATION :

{history}

{style_context}

Réponds au dernier message du voyageur.

Rappels critiques :
- le logement courant a déjà été identifié par le serveur ;
- n'utilise jamais les données d'un autre logement ;
- les dates de réservation Guesty sont la vérité pour la réservation existante ;
- une nouvelle disponibilité vient uniquement du bloc CALENDRIER LIVE ;
- ne révèle jamais les IDs internes ;
- si une donnée du logement est INCONNUE, ne l'invente pas.

Si plusieurs messages récents forment une même demande,
réponds à tous les points en UNE SEULE réponse.

Raisonne silencieusement avec l'heure actuelle, les dates, l'historique
et le contexte Guesty vérifié.
Si la réponse est évidente, réponds directement sans réciter la règle.
Adapte surtout ta longueur et ta façon de répondre aux EXEMPLES RÉELS DE L'HÔTE.
Sois naturel, chaleureux, utile et aussi concis que l'hôte le serait.
"""

    response = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_RULES,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
    )

    reply = (
        response.choices[0].message.content
        or ""
    ).strip()

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

    if not payloads:
        print("No payloads - skipping")
        return

    latest_payload = payloads[-1]

    # --------------------------------------------------------
    # 1. Conversation fraîche Guesty
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

    if not is_guest_conversation(
        conversation
    ):
        print(
            "Non-guest / owner conversation - skipping"
        )
        return

    print(
        "Conversation retrieved"
    )

    # --------------------------------------------------------
    # 2. Reservation ID
    # --------------------------------------------------------

    reservation_id = None

    # D'abord les webhooks.
    for payload in reversed(
        payloads
    ):
        reservation_id = extract_reservation_id(
            payload
        )

        if reservation_id:
            break

    # Puis la conversation fraîche Guesty.
    if not reservation_id:
        reservation_id = extract_reservation_id(
            {
                "conversation":
                    conversation
            }
        )

    reservation = None

    if reservation_id:
        print(
            f"Reservation ID found: "
            f"{reservation_id}"
        )

        reservation = await get_reservation(
            reservation_id
        )

        # Fail closed : si Guesty nous dit qu'il y a une réservation mais
        # qu'on ne peut pas la récupérer, on ne répond pas avec un contexte partiel.
        if not reservation:
            print(
                "BLOCKED - reservation ID exists but "
                "reservation could not be retrieved safely"
            )
            return

        # Reservations V3 expose principalement unitTypeId / unitId.
        # Ces logs ne contiennent aucune donnée voyageur et permettent de
        # vérifier immédiatement quel logement Guesty est rattaché au séjour.
        print(
            "Reservation property IDs: "
            f"unitTypeId={reservation.get('unitTypeId')} | "
            f"unitId={reservation.get('unitId')} | "
            f"lastStayListingId={reservation.get('lastStayListingId')} | "
            f"listingId={reservation.get('listingId')}"
        )

        # Best practice Guesty : une fois la réservation récupérée, son
        # conversationId devient la référence pour aller chercher les messages.
        canonical_conversation_id = extract_conversation_id_from_reservation(
            reservation
        )

        if canonical_conversation_id:
            if canonical_conversation_id != conversation_id:
                print(
                    "Using reservation conversationId from Guesty: "
                    f"{canonical_conversation_id}"
                )
                conversation_id = canonical_conversation_id

            canonical_conversation = await get_conversation(
                conversation_id
            )

            if not canonical_conversation:
                print(
                    "BLOCKED - reservation conversation could not be retrieved"
                )
                return

            conversation = canonical_conversation

            if not is_guest_conversation(conversation):
                print(
                    "Non-guest / owner conversation - skipping"
                )
                return

    else:
        print(
            "No reservation ID - inquiry / pre-booking conversation"
        )

    # --------------------------------------------------------
    # 3. IDENTIFICATION STRICTE DU LOGEMENT
    # --------------------------------------------------------

    listing_resolution = resolve_listing_id(
        reservation=reservation,
        conversation=conversation,
        payloads=payloads,
    )

    if not listing_resolution.get(
        "ok"
    ):
        print(
            "BLOCKED - property could not be identified safely. "
            f"Reason: {listing_resolution.get('reason')}"
        )

        evidence = listing_resolution.get(
            "evidence"
        ) or []

        # Logs techniques : IDs seulement, jamais de secrets.
        for item in evidence:
            print(
                "Listing evidence: "
                f"{item.get('source')} = "
                f"{item.get('value')}"
            )

        return

    listing_id = listing_resolution[
        "listing_id"
    ]

    property_data = listing_resolution[
        "property_data"
    ]

    if property_data.get(
        "enabled",
        True,
    ) is False:
        print(
            f"BLOCKED - property disabled: "
            f"{property_data.get('name')}"
        )
        return

    print(
        "PROPERTY VERIFIED"
    )
    print(
        f"Guesty name: "
        f"{property_data.get('guesty_name')}"
    )
    print(
        f"Listing ID: {listing_id}"
    )

    # --------------------------------------------------------
    # 4. Contexte réservation / inquiry + dates Guesty
    # --------------------------------------------------------

    booking_context = build_booking_context(
        conversation_id=conversation_id,
        listing_id=listing_id,
        property_data=property_data,
        reservation_id=reservation_id,
        reservation=reservation,
    )

    print(
        f"Conversation type: "
        f"{booking_context.get('conversation_type')}"
    )
    print(
        f"Reservation status: "
        f"{booking_context.get('reservation_status')}"
    )
    print(
        f"Reservation check-in: "
        f"{booking_context.get('reservation_check_in')}"
    )
    print(
        f"Reservation check-out: "
        f"{booking_context.get('reservation_check_out')}"
    )

    # --------------------------------------------------------
    # 5. Sensitive access
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
    # 6. Messages de la conversation
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

        posts = payloads_to_posts(
            payloads
        )

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

    if not posts:
        print(
            "ERROR: no usable guest message found anywhere."
        )

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
    # 7. Dernier message voyageur
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
    # 8. Anti double-réponse
    # --------------------------------------------------------

    if host_replied_after_guest(
        posts,
        guest_post,
    ):
        print(
            "Host already replied after latest "
            "guest message - skipping"
        )
        return

    # --------------------------------------------------------
    # 9. Guest name
    # --------------------------------------------------------

    guest_name = extract_guest_name(
        conversation,
        reservation,
    )

    # --------------------------------------------------------
    # 10. History sécurisée pour LE logement courant
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
    # 11. Dates demandées + calendrier DU BON LISTING
    # --------------------------------------------------------

    availability_request = await extract_availability_request(
        latest_guest_message=guest_message,
        history=history,
        booking_context=booking_context,
        timezone_name=property_data.get(
            "timezone",
            "Europe/Paris",
        ),
    )

    print(
        "Availability request detected: "
        f"{availability_request.get('availability_question')}"
    )
    print(
        "Requested availability dates: "
        f"{availability_request.get('check_in')} -> "
        f"{availability_request.get('check_out')}"
    )

    availability_context = await get_live_availability_context(
        listing_id=listing_id,
        availability_request=availability_request,
    )

    # --------------------------------------------------------
    # 12. Style : uniquement conversations du même Listing ID
    # --------------------------------------------------------

    style_examples = await get_recent_style_examples(
        listing_id,
        property_data,
    )

    # --------------------------------------------------------
    # 13. Dernier verrou avant OpenAI
    # --------------------------------------------------------

    if listing_id not in PROPERTIES:
        print(
            "BLOCKED - listing disappeared from configuration"
        )
        return

    if (
        PROPERTIES[listing_id]
        is not property_data
    ):
        # Ce test n'est pas censé arriver ; il empêche un changement de contexte
        # accidentel en mémoire avant la génération.
        print(
            "BLOCKED - property context changed unexpectedly"
        )
        return

    print("")
    print(
        "===== VERIFIED CONTEXT ====="
    )
    print(
        f"Conversation: {conversation_id}"
    )
    print(
        f"Type: "
        f"{booking_context.get('conversation_type')}"
    )
    print(
        f"Property: "
        f"{property_data.get('name')}"
    )
    print(
        f"Listing: {listing_id}"
    )
    print(
        f"Reservation: "
        f"{reservation_id or 'NONE'}"
    )
    print(
        f"Status: "
        f"{booking_context.get('reservation_status') or 'NONE'}"
    )
    print(
        f"Check-in: "
        f"{booking_context.get('reservation_check_in') or 'NONE'}"
    )
    print(
        f"Check-out: "
        f"{booking_context.get('reservation_check_out') or 'NONE'}"
    )
    print(
        f"Availability dates: "
        f"{availability_request.get('check_in') or 'NONE'} "
        f"-> "
        f"{availability_request.get('check_out') or 'NONE'}"
    )
    print(
        "============================"
    )
    print("")

    # --------------------------------------------------------
    # 14. OpenAI
    # --------------------------------------------------------

    print(
        "Calling OpenAI..."
    )

    reply = await generate_reply(
        guest_name=guest_name,
        listing_id=listing_id,
        property_data=property_data,
        booking_context=booking_context,
        authorized=authorized,
        history=history,
        style_examples=style_examples,
        availability_context=availability_context,
    )

    print(
        "OpenAI response generated successfully."
    )

    # --------------------------------------------------------
    # 15. Send / test
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



# Per-conversation lock for synchronous webhook processing on Render.
conversation_locks: Dict[str, asyncio.Lock] = {}

def get_conversation_lock(conversation_id: str) -> asyncio.Lock:
    lock = conversation_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        conversation_locks[conversation_id] = lock
    return lock


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
    # Render-safe processing
    # --------------------------------------------------------
    # Do not detach the important work with create_task().
    # Keep the webhook request alive through the debounce and processing.
    # A per-conversation lock ensures simultaneous webhooks cannot send
    # duplicate replies; the second pass sees the fresh host reply and skips.

    lock = get_conversation_lock(conversation_id)
    async with lock:
        print(f"Waiting {DEBOUNCE_SECONDS}s before processing {conversation_id}")
        await asyncio.sleep(DEBOUNCE_SECONDS)
        await process_messages(conversation_id, [payload])

    return {
        "ok": True,
        "processed": True,
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
