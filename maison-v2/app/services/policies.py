"""Deterministic guardrails complement model reasoning; no configuration writes here."""
import re
import unicodedata
from app.schemas.actions import GuestReply
from app.services.guest_agent import fallback


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if unicodedata.category(c) != 'Mn')


def guard_reply(context, messages, plan, reply, language):
    incoming = normalized(' '.join(messages))
    reason = normalized(plan.reason or '')
    output = normalized(reply.text)
    financial = re.search(r'rembours|refund|compensation|compensacion|reduction|discount|geste commercial|remb[ou]+rsement|erstattung|remborso|reembolso', incoming)
    exception = plan.manager_required and re.search(r'early|late|horaire|check.?in|check.?out|12h|arriv|depart|financier|rembours|commercial', reason)
    danger = re.search(r'fumee|smoke|odeur de brule|burning smell|etincelle|sparks|grosse fuite|danger electrique|urgence medicale|enferme|locked inside|incendie|fire in', incoming)
    if danger:
        text = ('Please move away from the danger. Do not touch the equipment. If anyone is in immediate danger, contact local emergency services now. I am alerting my manager immediately.'
                if language.startswith('en') else
                "Éloignez-vous du danger et ne touchez pas à l’équipement. Si une personne est en danger immédiat, contactez les services d’urgence locaux maintenant. J’alerte immédiatement mon manager.")
        return GuestReply(text=text, escalation_required=True, escalation_summary='Signalement de sécurité : intervention urgente.', priority='critical')
    asks_permission = re.search(r'peux|puis|possible|can i|may i|early|late|plus tot|plus tard', incoming)
    arrival = re.search(r'(?:arriv[a-z]*|check.?in)\D{0,25}(\d{1,2})(?:h|:)(\d{2})?', incoming)
    departure = re.search(r'(?:partir|depart[a-z]*|check.?out)\D{0,25}(\d{1,2})(?:h|:)(\d{2})?', incoming)
    time_exception = False
    explicit_time_permission=False
    if asks_permission:
        for match, field, comparison in [(arrival, "check_in", "before"), (departure, "check_out", "after")]:
            limit = context.property_facts.get(field)
            if match and limit and re.fullmatch(r"\d{2}:\d{2}", limit):
                requested = int(match[1])*60 + int(match[2] or 0)
                authorized = int(limit[:2])*60 + int(limit[3:])
                rule=context.property_rules.get('early_checkin_from' if comparison=='before' else 'late_checkout_until')
                permitted=False
                if isinstance(rule,str) and re.fullmatch(r'\d{2}:\d{2}',rule):
                    boundary=int(rule[:2])*60+int(rule[3:])
                    permitted=boundary<=requested<authorized if comparison=='before' else authorized<requested<=boundary
                explicit_time_permission|=permitted
                time_exception |= (requested < authorized if comparison == "before" else requested > authorized) and not permitted
    if explicit_time_permission and not financial and not time_exception and not danger:
        reply.escalation_required=False;reply.escalation_summary=None
        exception=False
    if financial or exception or time_exception:
        return GuestReply(text=fallback(language), escalation_required=True,
            escalation_summary=plan.reason or 'Décision commerciale requise.', priority='normal')
    if plan.availability_requested:
        status = context.availability_status
        if status in {"dates_incomplete", "invalid_dates"}:
            text = "Could you confirm your check-in and check-out dates?" if language.startswith("en") else "Quelles sont vos dates exactes d’arrivée et de départ ?"
            return GuestReply(text=text, escalation_required=False, escalation_summary=None, priority="normal")
        if status in {"unknown", "not_checked"}:
            return GuestReply(text=fallback(language), escalation_required=True,
                escalation_summary="Disponibilité Guesty non vérifiée.", priority="normal")
        if status in {"unavailable", "restricted"}:
            text = "These dates are unavailable or subject to booking restrictions. Would other dates work for you?" if language.startswith("en") else "Ces dates sont indisponibles ou soumises à une restriction de réservation. Avez-vous d’autres dates possibles ?"
            return GuestReply(text=text, escalation_required=False, escalation_summary=None, priority="normal")
    unsafe = re.search(r'(demontez|demonter|disassemble|touch the wires|touchez les fils|manipulez le gaz|ouvrez le tableau|open the electrical panel)', output)
    unknown_credentials = re.search(r'(code|password|mot de passe)\s*(?:est|is|:|=)?\s*[a-z0-9-]{4,}', output) and not (context.access_authorized or context.wifi)
    if unsafe or unknown_credentials:
        return GuestReply(text=fallback(language), escalation_required=True,
            escalation_summary='Réponse retenue par le contrôle de sécurité.', priority='high')
    return reply
