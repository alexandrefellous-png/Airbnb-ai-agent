from app.core.security import nested_strings
import html
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.db.models import Escalation, PropertyAccess, PropertyState, PropertyWifi
from app.schemas.context import GuestContext
from app.services.property_resolver import PropertyResolver, ResolutionError

class DestinationError(RuntimeError):
    pass

def clean(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html.unescape(text or ""))).strip()

class ContextBuilder:
    def __init__(self, db, guesty, vault, settings):
        self.db, self.guesty, self.vault, self.settings = db, guesty, vault, settings
        self.resolver = PropertyResolver()

    async def build(self, payload, now=None):
        now = now or time.time()
        rid = payload.get("reservationId")
        reservation = await self.guesty.reservation(rid) if rid else None
        hinted = payload.get("conversation", {}).get("_id")
        cid = reservation.get("conversationId") if reservation else hinted
        if not cid or (hinted and hinted != cid):
            raise DestinationError("conversation_destination_uncertain")
        conversation = await self.guesty.conversation(cid)
        if conversation.get("conversationWith", "Guest") != "Guest":
            raise DestinationError("not_guest_conversation")
        metas = conversation.get("meta", {}).get("reservations", [])
        if rid and not any(m.get("_id") == rid for m in metas):
            raise DestinationError("reservation_conversation_mismatch")
        if not rid and len(metas) == 1 and metas[0].get("_id"):
            # Even an inquiry may have a Guesty reservation record. Fetch it, never trust webhook dates.
            rid = metas[0]["_id"]
            reservation = await self.guesty.reservation(rid)
            if reservation.get("conversationId") != cid:
                raise DestinationError("inquiry_conversation_mismatch")
        posts = await self.guesty.posts(cid, self.settings.history_limit)
        verified_listing_id=None
        selected=[m for m in metas if m.get('_id')==rid]
        if reservation and reservation.get('conversationId')==cid and len(selected)==1 and len(reservation.get('stay',[]))==1:
            lid=selected[0].get('listing',{}).get('_id')
            from app.db.models import PropertySync
            with self.db.session() as session:
                known=session.scalar(select(PropertySync).where(PropertySync.external_id==lid,PropertySync.status=='synced')) if lid else None
            if known:
                listing=await self.guesty.listing(lid)
                if listing.get('_id')==lid:verified_listing_id=lid
        context = GuestContext(conversation_id=cid, reservation_id=rid,
            reservation_status=reservation.get("status") if reservation else None,
            guest_name=conversation.get("meta", {}).get("guestName") or conversation.get("meta", {}).get("guest",{}).get("fullName", ""),
            recent_history=[{"id": p.get("_id"), "sender": p.get("from", {}).get("type", "unknown"),
                "body": clean(p.get("body")), "created_at": p.get("createdAt")} for p in posts
                if p.get("module", {}).get("type") not in {"note", "log"}])
        context.conversation_type = "CONFIRMED_RESERVATION" if context.reservation_status == "confirmed" else "INQUIRY" if not context.reservation_status or context.reservation_status == "inquiry" else "OTHER_RESERVATION"
        with self.db.session() as session:
            try:
                prop = self.resolver.resolve(session, reservation, conversation, rid,verified_listing_id)
            except ResolutionError as e:
                context.resolution_reason = str(e)
                context.local_now = datetime.fromtimestamp(now, ZoneInfo("UTC")).isoformat()
                from app.services.reservations import ReservationService
                ReservationService(self.db,self.vault,self.settings).remember(context,reservation)
                return context, posts
            context.property_id, context.property_name = prop.id, prop.name
            context.guesty_listing_id = prop.guesty_listing_id
            timezone = ZoneInfo(prop.timezone)
            context.local_now = datetime.fromtimestamp(now, timezone).isoformat()
            context.property_facts = {**prop.facts, "check_in": prop.check_in, "check_out": prop.check_out}
            context.property_rules = prop.rules
            context.property_notes = [self.vault.decrypt(note) for note in prop.notes]
            from app.services.knowledge import KnowledgeService,RULE_FIELDS
            from app.services.manager import ACCESS_FIELDS,WIFI_FIELDS
            knowledge=KnowledgeService(self.vault)
            memory=knowledge.effective(knowledge.rows(session,prop.id),now)
            for key,item in memory.items():
                if key in RULE_FIELDS:context.property_rules={**context.property_rules,key:item['value']}
                elif key not in ACCESS_FIELDS|WIFI_FIELDS|{'name','address','timezone','guesty_listing_id'} and not key.startswith('state:'):
                    context.property_facts[key]=item['value']
            if reservation and len(reservation.get("stay", [])) == 1:
                stay = reservation["stay"][0]
                try:
                    # Stay localized dates are the reservation truth; eta/etd are not authorization times.
                    arrival = datetime.fromisoformat(stay["checkInDateLocalized"] + "T" + prop.check_in).replace(tzinfo=timezone)
                    departure = datetime.fromisoformat(stay["checkOutDateLocalized"] + "T" + prop.check_out).replace(tzinfo=timezone)
                    context.reservation_check_in, context.reservation_check_out = arrival.isoformat(), departure.isoformat()
                    before = prop.rules.get("access_before_hours", self.settings.access_before_hours)
                    after = prop.rules.get("access_after_hours", self.settings.access_after_hours)
                    context.access_authorized = (context.reservation_status == "confirmed" and
                        (arrival-timedelta(hours=before)).timestamp() <= now <= (departure+timedelta(hours=after)).timestamp())
                except (KeyError, ValueError, TypeError):
                    context.access_authorized = False
            # Never send withheld secrets to the model. Strip historical credentials as well.
            secrets = []
            access = session.get(PropertyAccess, prop.id)
            wifi = session.get(PropertyWifi, prop.id)
            if access:
                values = self.vault.decrypt(access.encrypted_data)
                secrets.extend(nested_strings(values))
                if context.access_authorized:
                    context.access = {**values, "address": prop.address}
            if wifi:
                values = self.vault.decrypt(wifi.encrypted_data)
                secrets.extend(nested_strings(values))
                if context.reservation_status == "confirmed":
                    context.wifi = values
            for key,item in memory.items():
                if key in ACCESS_FIELDS|WIFI_FIELDS:
                    secrets.extend(nested_strings(item['value']))
                    if key in ACCESS_FIELDS and context.access_authorized:context.access[key]=item['value']
                    if key in WIFI_FIELDS and context.reservation_status=='confirmed':context.wifi[key]=item['value']
            for item in context.recent_history:
                item["body"] = self.vault.redact(item["body"], secrets)
            states = session.scalars(select(PropertyState).where(PropertyState.property_id == prop.id,
                PropertyState.valid_from <= now)).all()
            context.property_current_states = [{"key": s.key, "status": s.status, "note": s.note,
                "valid_until": s.valid_until} for s in states if s.valid_until is None or s.valid_until > now]
            context.open_escalations = [{"id": e.id, "priority": e.priority, "status": e.status,
                "summary": self.vault.redact(self.vault.decrypt(e.encrypted_summary), secrets)}
                for e in session.scalars(select(Escalation).where(Escalation.guesty_conversation_id == cid,
                    Escalation.status == "open"))]
        from app.services.reservations import ReservationService
        ReservationService(self.db,self.vault,self.settings).remember(context,reservation)
        return context, posts
