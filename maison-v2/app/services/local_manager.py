"""Limited deterministic commands when no OpenAI key is configured. Never presented as an LLM."""
import re
import unicodedata
from app.schemas.actions import ManagerAction, FieldUpdate

def norm(text):
    return ''.join(c for c in unicodedata.normalize("NFD",text.casefold()) if unicodedata.category(c)!="Mn")

def local_action(message, catalog):
    text = norm(message)
    base = dict(property=None, updates=[], state_key=None, state_status=None, valid_until=None, note=None,
        mapping_kind=None, external_id=None, escalation_id=None, confidence=1, message="")
    if re.search(r"reponses? plus court|moins.*emoji|pas.*emoji|dear guest|ne repet|simplement oui|reponse.*evidente",text):
        return ManagerAction(intent="set_style_preference",**{**base,"style_preference":message})
    if re.search(r"remets?|repasse|passe|active|desactive",text) and re.search(r"agent|reponses|production|mode|test",text):
        mode="test" if re.search(r"test|desactive",text) else "live" if re.search(r"production|reelles|live",text) else None
        if mode:return ManagerAction(intent="set_agent_mode",**{**base,"agent_mode":mode})
    matches = [p["name"] for p in catalog if re.search(r"(?<![\w])"+re.escape(norm(p["name"]).lstrip("!"))+r"(?![\w])",text)]
    name = matches[0] if len(matches)==1 else None
    if name: base["property"]=name
    if re.search(r"ajout|nouvel appartement|rentre un",text):
        match=re.search(r"appartement\s+([!\w-]+)\s*[.!]?\s*$",message,re.I)
        if match and norm(match[1]) not in {"nouveau","nouvel"}:
            return ManagerAction(intent="create_property",**{**base,"property":match[1]})
        return ManagerAction(intent="onboarding",**base)
    if "attend" in text or "voyageur" in text or "late check" in text or "alert" in text:
        return ManagerAction(intent="list_escalations",**{**base,"query":message})
    if "sejour" in text: return ManagerAction(intent="list_stays",**base)
    if "appartement" in text and ("probleme" in text or "panne" in text):return ManagerAction(intent="list_issues",**base)
    if "appartements" in text:return ManagerAction(intent="list_properties",**base)
    if name and ("infos" in text or "fiche" in text or "montre" in text):return ManagerAction(intent="get_property",**base)
    if name:
        for word,key in [("ascenseur","elevator"),("clim","air_conditioning"),("wi-fi","wifi"),("wifi","wifi"),("eau chaude","hot_water")]:
            if word in text:
                status="working" if re.search(r"remarche|repare|fonctionne|retabli",text) else "out_of_order" if re.search(r"panne|probleme|ne marche|ne fonctionne",text) else None
                if status and not re.search(r"jusqu|demain|vendredi|lundi|aujourd",text):
                    return ManagerAction(intent="update_property_state",**{**base,"state_key":key,"state_status":status})
        for word,field in [("code boite","lockbox_code"),("code de la boite","lockbox_code"),("code immeuble","building_code"),("mot de passe wi-fi","wifi_password")]:
            if word in text:
                value=re.search(r"(?:=|maintenant|par|est)\s*([^\s.]+)\s*[.]?$",message,re.I)
                if value:return ManagerAction(intent="update_fields",**{**base,"updates":[FieldUpdate(field=field,value=value[1])]})
    return ManagerAction(intent="clarify",**{**base,"message":"Le chat complet nécessite la clé OpenAI. Vous pouvez déjà consulter les fiches et alertes, ajouter un appartement ou écrire « NOM l’ascenseur remarche ». Pour une demande plus précise, utilisez les boutons de la fiche ou configurez OpenAI dans les réglages."})
