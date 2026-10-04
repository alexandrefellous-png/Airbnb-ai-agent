from urllib.parse import quote
import time
from app.core.security import fingerprint
from app.db.models import OAuthToken
import httpx
from app.guesty.errors import ContractError, GuestyError, retry_timestamp, LiveSendsBlocked

BASE = "https://open-api.guesty.com/v1"

def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 120:
        raise ContractError("invalid_guesty_identifier")
    return value

def path_id(value):
    return quote(identifier(value), safe="")

def verified_sender(post):
    """Two documented Guesty representations; contradictory provenance stays unknown."""
    origin=post.get("from") or {}
    sender=origin.get("type") if isinstance(origin,dict) else None
    sent_by=post.get("sentBy")
    if (sender=="guest" and sent_by=="host") or (sender in {"user","host"} and sent_by=="guest"):
        return "unknown"
    if sender in {"guest","user","host"}:return "user" if sender=="host" else sender
    if sender:return "unknown"
    return {"guest":"guest","host":"user"}.get(sent_by,"unknown")

class GuestyClient:
    def __init__(self, tokens, http: httpx.AsyncClient, settings):
        self.tokens, self.http, self.settings = tokens, http, settings
        self.db = getattr(tokens, "db", None)

    @property
    def rate_key(self):
        return fingerprint("api-rate:" + self.settings.guesty_client_id)

    async def request(self, method, path, *, params=None, payload=None):
        if method.upper() not in {"GET", "HEAD"} and (self.settings.test_mode or not self.settings.allow_live_sends):
            raise LiveSendsBlocked("Envoi externe bloqué par le mode ou la sécurité serveur")
        if self.db:
            with self.db.session() as session:
                cooldown = session.get(OAuthToken, self.rate_key)
                if cooldown and cooldown.retry_at > time.time():
                    raise GuestyError(path, 429, cooldown.retry_at)
        token = await self.tokens.get()
        # Exactly one controlled retry for a rejected token. No retry of uncertain POSTs.
        for attempt in range(2):
            try:
                response = await self.http.request(method, BASE + path, params=params, json=payload,
                    headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
            except httpx.HTTPError:
                raise GuestyError(path, 503) from None
            if response.status_code == 401 and attempt == 0:
                token = await self.tokens.get(rejected=token)
                continue
            if response.status_code == 429:
                deadline = retry_timestamp(response.headers)
                if self.db:
                    async with self.db.lock("api-rate:"+self.rate_key):
                        with self.db.session() as session:
                            row = session.get(OAuthToken, self.rate_key)
                            if row is None:
                                row = OAuthToken(id=self.rate_key, retry_at=deadline)
                                session.add(row)
                            else:
                                row.retry_at = max(row.retry_at, deadline)
                            session.commit()
                raise GuestyError(path, 429, deadline)
            if not 200 <= response.status_code < 300:
                raise GuestyError(path, response.status_code)
            return response.json()
        raise GuestyError(path, 401)

    async def conversations(self,limit=25,cursor=None):
        params={"limit":min(100,max(1,limit)),"sort":"-modifiedAt"}
        if cursor:params["cursorAfter"]=cursor
        # Full documented envelope. Field projection on meta triggers upstream errors
        # on some accounts; never infer missing metadata from a projected result.
        result=await self.request("GET","/communication/conversations",params=params)
        data=result.get("data") if isinstance(result,dict) else None
        if not isinstance(data,dict) or not isinstance(data.get("conversations"),list):
            raise ContractError("conversations_expected_data_conversations")
        for row in data["conversations"]:identifier(row.get("_id"))
        return {"conversations":data["conversations"],"cursor":data.get("cursor",{}).get("after")}

    async def reservation(self, reservation_id):
        data = await self.request("GET", "/reservations-v3", params=[("reservationIds[]", identifier(reservation_id))])
        if not isinstance(data, list):
            raise ContractError("reservation_v3_expected_array")
        matches = [r for r in data if r.get("_id") == reservation_id]
        if len(matches) != 1:
            raise ContractError("reservation_id_not_unique")
        return matches[0]

    async def conversation(self, conversation_id):
        result = await self.request("GET", f"/communication/conversations/{path_id(conversation_id)}")
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or data.get("_id") != conversation_id:
            raise ContractError("conversation_destination_mismatch")
        return data

    async def posts(self, conversation_id, limit=100):
        posts, cursor, seen = [], None, set()
        while len(posts) < limit:
            params = {"sort": "-createdAt", "limit": min(25, limit-len(posts))}
            if cursor:
                params["cursorAfter"] = cursor
            result = await self.request("GET", f"/communication/conversations/{path_id(conversation_id)}/posts", params=params)
            data = result.get("data") if isinstance(result, dict) else None
            if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
                raise ContractError("posts_expected_data_posts")
            for post in data["posts"]:
                if post.get("conversationId") != conversation_id:
                    raise ContractError("post_destination_mismatch")
            for post in data["posts"]:
                source=verified_sender(post)
                if not isinstance(post.get("from"),dict) or post["from"].get("type")!=source:
                    post={**post,"from":{**(post.get("from") if isinstance(post.get("from"),dict) else {}),"type":source}}
                posts.append(post)
            cursor = data.get("cursor", {}).get("after")
            if not cursor or cursor in seen or not data["posts"]:
                break
            seen.add(cursor)
        return sorted(posts[:limit], key=lambda p: p.get("createdAt", ""))

    async def listing(self, listing_id):
        data = await self.request("GET", f"/listings/{path_id(listing_id)}")
        if not isinstance(data, dict) or data.get("_id") != listing_id:
            raise ContractError("listing_id_mismatch")
        return data

    async def listings(self):
        rows, skip = [], 0
        while True:
            # Omit active/pmsActive/listed: documented to include all states.
            data = await self.request("GET", "/listings", params={"limit": 100, "skip": skip,"sort":"_id"})
            # Endpoint guide documents a results envelope; reference also documents a flat array.
            page = data if isinstance(data, list) else data.get("results") if isinstance(data,dict) else None
            if not isinstance(page, list):
                raise ContractError("listings_expected_results")
            rows.extend(page)
            if len(page) < 100:
                return rows
            skip += len(page)
            if skip > 10000:
                raise ContractError("listings_pagination_limit")

    async def reservation_ids(self, listing_ids):
        import json
        from datetime import date,timedelta
        if not listing_ids:return []
        ids,seen=[],set()
        today=date.today()
        for listing_id in listing_ids:
            skip=0
            filters=[{"operator":"$eq","field":"listingId","value":identifier(listing_id)},
                {"operator":"$between","field":"checkOutDateLocalized","value":[(today-timedelta(days=30)).isoformat(),(today+timedelta(days=365)).isoformat()],"context":"date"}]
            while True:
                data=await self.request("GET","/reservations",params={"filters":json.dumps(filters),"fields":"_id listingId checkOutDateLocalized","limit":100,"skip":skip,"sort":"_id"})
                page=data if isinstance(data,list) else data.get("results") if isinstance(data,dict) else None
                if not isinstance(page,list):raise ContractError("reservation_search_expected_results")
                for row in page:
                    if row.get("listingId") != listing_id:raise ContractError("reservation_search_listing_mismatch")
                    rid=identifier(row.get("_id"))
                    if rid in seen:raise ContractError("reservation_search_duplicate")
                    ids.append(rid);seen.add(rid)
                if len(page)<100:break
                skip+=len(page)
                if skip>10000:raise ContractError("reservation_search_pagination_limit")
        return ids

    async def calendar(self, listing_id, start, end):
        return await self.request("GET", f"/availability-pricing/api/calendar/listings/{path_id(listing_id)}",
            params={"startDate": start, "endDate": end, "includeAllotment": "true"})

    async def send(self, conversation_id, text, module):
        if self.db:
            async with self.db.lock("agent_mode"):
                return await self._send_checked(conversation_id,text,module)
        return await self._send_checked(conversation_id,text,module)

    async def _send_checked(self, conversation_id, text, module):
        if self.settings.test_mode or not self.settings.allow_live_sends:
            raise LiveSendsBlocked("TEST_MODE ou ALLOW_LIVE_SENDS=false interdit tout envoi Guesty")
        if module.get("type") not in {"airbnb2", "email", "sms", "whatsapp"}:
            raise ContractError("unsupported_reply_channel")
        data = await self.request("POST", f"/communication/conversations/{path_id(conversation_id)}/send-message",
            payload={"module": module, "body": text})
        post = data.get("data", {})
        if not post.get("_id") or post.get("conversationId") != conversation_id:
            raise ContractError("send_confirmation_missing")
        return post["_id"]
