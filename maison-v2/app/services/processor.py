from app.core.security import nested_strings
import asyncio
import logging
import re
import time
from sqlalchemy import select
from app.core.security import fingerprint
from app.db.models import Event, PropertyAccess, PropertyWifi, SentMessage, StyleProfile
from app.guesty.errors import GuestyError
from app.schemas.actions import GuestPlan, GuestReply
from app.services.context import DestinationError, clean
from app.services.guest_agent import fallback
from app.services.policies import guard_reply

log = logging.getLogger("agent.processing")

class Processor:
    def __init__(self, db, vault, settings, guesty, builder, calendar, agent, escalations):
        self.db, self.vault, self.settings, self.guesty = db, vault, settings, guesty
        self.builder, self.calendar, self.agent, self.escalations = builder, calendar, agent, escalations

    def safe_inputs(self, context, messages):
        allowed = set(nested_strings([context.access, context.wifi]))
        all_secrets = []
        with self.db.session() as session:
            from app.services.properties import PropertyService
            all_secrets=PropertyService(self.db,self.vault).secrets(session)
        # Input history can contain access credentials from a former stay or another apartment.
        for item in context.recent_history:
            item["body"] = self.vault.redact(item["body"], all_secrets)
        safe = [self.vault.redact(m, all_secrets) for m in messages]
        return safe, [v for v in all_secrets if v not in allowed]

    async def process(self, event_ids):
        with self.db.session() as session:
            events = [session.get(Event, eid) for eid in event_ids]
            events = [e for e in events if e and e.status == "pending"]
            if not events:
                return
            payloads = [self.vault.decrypt(e.encrypted_payload) for e in events]
        payload = payloads[-1]
        context, posts = await self.builder.build(payload)
        cid = context.conversation_id
        for other in payloads:
            hinted = other.get("conversation", {}).get("_id")
            if hinted and hinted != cid:
                raise DestinationError("batch_destination_conflict")
            if other.get("reservationId") != payload.get("reservationId"):
                raise DestinationError("batch_reservation_conflict")
        messages = [clean(p["message"]["body"]) for p in payloads]
        # Verify webhook content against fresh Guesty posts before replying.
        for p, body in zip(payloads, messages):
            mid = p["message"].get("_id") or p["message"].get("postId") or p["message"].get("id")
            candidates = [post for post in posts if (post.get("_id") == mid if mid else clean(post.get("body")) == body)]
            if not candidates or not any(clean(post.get("body")) == body and post.get("from", {}).get("type") == "guest" for post in candidates):
                raise DestinationError("incoming_message_not_verified_in_posts")
        batch_key = fingerprint("|".join(sorted(event_ids)))
        with self.db.session() as session:
            ambiguous = session.scalar(select(SentMessage).where(SentMessage.conversation_id == cid,
                SentMessage.status.in_(["sending", "uncertain"])))
            if ambiguous:
                self.escalations.create("uncertain:"+ambiguous.id, "Envoi incertain : vérifier Guesty avant toute nouvelle réponse.", context, "high")
                self.finish(event_ids, "blocked", "uncertain_previous_send")
                return
            sent = session.scalar(select(SentMessage).where(SentMessage.batch_key == batch_key))
            if sent and sent.status in {"sent", "simulated"}:
                self.finish(event_ids, "done")
                return
            profile = session.get(StyleProfile, "host")
            style = profile.profile if profile else ""
        received_original = list(messages)
        messages, withheld = self.safe_inputs(context, messages)
        log.info("CONTEXT conversation=%s reservation=%s property=%s listing=%s type=%s access=%s",
            cid, context.reservation_id, context.property_name, context.guesty_listing_id,
            context.conversation_type, context.access_authorized)
        language = payload.get("conversation", {}).get("language", "fr")
        if context.resolution_reason:
            log.warning("BLOCKED reason=%s", context.resolution_reason)
            reply = GuestReply(text=fallback(language), escalation_required=True,
                escalation_summary=context.resolution_reason, priority="high")
        else:
            try:
                plan = await self.agent.plan(context, messages)
                from app.services.learning import LearningService
                learning=LearningService(self.db,self.vault)
                missing=learning.missing(context,messages,plan.missing_fields)
                if missing:
                    learning.ask(context,missing)
                    plan.manager_required=True
                    plan.reason='Information manquante : '+', '.join(missing)
                context.requested_check_in, context.requested_check_out = plan.requested_check_in, plan.requested_check_out
                if plan.availability_requested:
                    # No revalidation of the dates already confirmed by Guesty.
                    same_dates = context.conversation_type == "CONFIRMED_RESERVATION" and context.reservation_check_in and context.reservation_check_out and (
                        plan.requested_check_in == context.reservation_check_in[:10] and plan.requested_check_out == context.reservation_check_out[:10])
                    if same_dates:
                        context.availability_status = "confirmed_reservation"
                    elif not plan.requested_check_in or not plan.requested_check_out:
                        context.availability_status = "dates_incomplete"
                    else:
                        try:
                            context.availability_status = await self.calendar.availability(context.guesty_listing_id,
                                plan.requested_check_in, plan.requested_check_out)
                        except Exception:
                            context.availability_status = "unknown"
                            plan.manager_required, plan.reason = True, "Calendrier Guesty non vérifiable."
                if hasattr(self,"training"):
                    style = self.training.style_prompt()
                reply = await self.agent.reply(context, messages, plan, style)
                if plan.manager_required:
                    reply.escalation_required = True
                    reply.escalation_summary = reply.escalation_summary or plan.reason
                    reply.priority = plan.priority
                reply = guard_reply(context, messages, plan, reply, language)
                if missing:
                    reply=GuestReply(text=fallback(language),escalation_required=False,escalation_summary=None,priority='normal')
                if any(secret and secret in reply.text for secret in withheld):
                    raise RuntimeError("withheld_secret_in_output")
                if not reply.text.strip() or len(reply.text) > 8000:
                    raise RuntimeError("invalid_reply")
                # An announced manager contact always creates an actual escalation.
                if not missing and re.search(r"manager|responsable|gestionnaire|hôte|host|check with|verify with", reply.text, re.I):
                    reply.escalation_required = True
            except Exception:
                log.warning("AI FALLBACK conversation=%s", cid)
                reply = GuestReply(text=fallback(language), escalation_required=True,
                    escalation_summary="Erreur IA ou contrôle de sécurité : réponse de vérification préparée.", priority="high")
        with self.db.session() as session:
            pending_ids = set(session.scalars(select(Event.id).where(Event.conversation_id == cid, Event.status == "pending")))
        if pending_ids - set(event_ids):
            log.info("DEBOUNCE new_messages conversation=%s", cid)
            return
        if reply.escalation_required:
            self.escalations.create("reply:"+batch_key, reply.escalation_summary or "Vérification manager nécessaire.", context, reply.priority)
        module = payload["message"].get("module")
        module = {"type": module} if isinstance(module, str) else module
        if not isinstance(module, dict) or module.get("type") not in {"airbnb2", "email", "sms", "whatsapp"}:
            self.escalations.create("channel:"+batch_key, "Canal de réponse non vérifié; aucun envoi.", context, "high")
            self.finish(event_ids, "blocked", "unsupported_reply_channel")
            return
        module = {k: v for k, v in module.items() if k in {"type", "to", "cc", "bcc"}}
        simulation = any(p.get("__observation_only") is True for p in payloads) or self.settings.test_mode or not self.settings.allow_live_sends
        with self.db.session() as session:
            row = session.scalar(select(SentMessage).where(SentMessage.batch_key == batch_key))
            if not row:
                row = SentMessage(batch_key=batch_key, conversation_id=cid, body_hash=fingerprint(reply.text),
                    encrypted_body=self.vault.encrypt(reply.text), test_mode=simulation)
                session.add(row)
            # Commit before sending; a crash cannot cause an automatic resend.
            row.status = "simulated" if simulation else "sending"
            if hasattr(self,"training"):
                session.flush()
                self.training.record(session,row,context,received_original,reply,simulation)
            session.commit()
            row_id = row.id
        if simulation:
            log.info("AI WOULD REPLY - TEST MODE - MESSAGE NOT SENT conversation=%s preview_id=%s (visible dans /admin)", cid, row_id)
            self.finish(event_ids, "done")
            return
        try:
            message_id = await self.guesty.send(cid, reply.text, module)
            with self.db.session() as session:
                row = session.get(SentMessage, row_id)
                row.guesty_message_id, row.status = message_id, "sent"
                session.commit()
            self.finish(event_ids, "done")
        except __import__("app.guesty.errors",fromlist=["LiveSendsBlocked"]).LiveSendsBlocked:
            with self.db.session() as session:
                row=session.get(SentMessage,row_id);row.status,row.test_mode="simulated",True;session.commit()
            self.finish(event_ids,"done")
        except Exception:
            with self.db.session() as session:
                session.get(SentMessage, row_id).status = "uncertain"
                session.commit()
            self.escalations.create("uncertain:"+row_id, "Résultat de l'envoi Guesty incertain. Réconciliation manuelle requise; aucun renvoi automatique.", context, "high")
            self.finish(event_ids, "blocked", "uncertain_send")

    def finish(self, ids, status, error=""):
        with self.db.session() as session:
            for eid in ids:
                row = session.get(Event, eid)
                if row:
                    row.status, row.error = status, error
            session.commit()

    async def tick(self):
        with self.db.session() as session:
            pending = session.scalars(select(Event).where(Event.status == "pending").order_by(Event.received_at).limit(500)).all()
        groups = {}
        for event in pending:
            groups.setdefault(event.conversation_id or "unresolved:"+event.id, []).append(event)
        async def handle(key, group):
            if max(e.due_at for e in group) > time.time():
                return
            async with self.db.lock("conversation:"+key):
                # Re-read after taking lock: another worker or a new event may have changed the group.
                with self.db.session() as session:
                    query = select(Event).where(Event.status == "pending")
                    query = query.where(Event.conversation_id == key) if not key.startswith("unresolved:") else query.where(Event.id == group[0].id)
                    fresh = session.scalars(query.order_by(Event.received_at)).all()
                if not fresh or max(e.due_at for e in fresh) > time.time():
                    return
                ids = [e.id for e in fresh]
                try:
                    if key.startswith("unresolved:"):
                        payload = self.vault.decrypt(fresh[0].encrypted_payload)
                        rid = payload.get("reservationId")
                        if not rid:
                            raise DestinationError("conversation_destination_uncertain")
                        reservation = await self.guesty.reservation(rid)
                        cid = reservation.get("conversationId")
                        if not cid:
                            raise DestinationError("conversation_destination_uncertain")
                        with self.db.session() as session:
                            row = session.get(Event, fresh[0].id)
                            row.conversation_id, row.due_at = cid, time.time()+self.settings.debounce_seconds
                            session.commit()
                        return
                    await self.process(ids)
                except DestinationError as exc:
                    reason = str(exc)
                    self.escalations.create("blocked:"+fingerprint("|".join(ids)), reason, priority="high", conversation_id=fresh[0].conversation_id)
                    self.finish(ids, "blocked", reason)
                    log.warning("BLOCKED reason=%s", reason)
                except Exception as exc:
                    with self.db.session() as session:
                        for eid in ids:
                            row = session.get(Event, eid)
                            row.attempts += 1
                            row.error = type(exc).__name__
                            row.due_at = max(time.time()+min(3600, 30*2**min(row.attempts, 7)), exc.retry_at if isinstance(exc, GuestyError) else 0)
                            if row.attempts >= 5:
                                row.status = "failed"
                        session.commit()
                    self.escalations.create("failure:"+fingerprint("|".join(ids)), "Traitement différé ou en échec : " + type(exc).__name__, priority="high", conversation_id=fresh[0].conversation_id)
                    log.warning("PROCESSING ERROR class=%s", type(exc).__name__)

        semaphore = asyncio.Semaphore(4)
        async def limited(key, group):
            async with semaphore:
                await handle(key, group)
        await asyncio.gather(*(limited(key, group) for key, group in groups.items()))
