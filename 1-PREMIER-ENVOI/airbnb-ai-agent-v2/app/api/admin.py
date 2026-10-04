import secrets
import time
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import Response, JSONResponse
from app.schemas.actions import ManagerAction
from app.services.session_auth import COOKIE
from app.services.properties import SECRET_FIELDS
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.core.security import fingerprint
from app.db.models import AuditLog, Escalation, Event, GuestyMapping, ManagerChange, Property, PropertyState, SentMessage
from app.services.manager import ManagerError

from app.api.auth import authenticate

router = APIRouter(prefix="/admin", dependencies=[Depends(authenticate)])

class TranslationInput(BaseModel):
    texts: list[str] = Field(min_length=1,max_length=8)

@router.post('/translations')
async def translations(body:TranslationInput,request:Request):
    if any(not text.strip() or len(text)>12000 for text in body.texts) or sum(map(len,body.texts))>30000:
        raise HTTPException(422,'Messages trop longs pour cette traduction.')
    from app.services.translation import TranslationService
    try:
        result=await TranslationService(request.app.state.db,request.app.state.vault,request.app.state.ai).translate(body.texts)
        return masked(request,{'translations':result})
    except ValueError as exc:raise HTTPException(429,str(exc)) from None
    except Exception:raise HTTPException(503,'Traduction indisponible. L’original reste accessible ; vérifiez la connexion OpenAI.') from None

class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    media_ids: list[str] = Field(default_factory=list,max_length=5)

class Confirmation(BaseModel):
    accept: bool

@router.get("", response_class=HTMLResponse)
def admin_page():
    return HTMLResponse((Path(__file__).parents[1]/"templates"/"admin.html").read_text(),
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"})

@router.post("/chat")
async def chat(body: ChatInput, request: Request):
    try:
        result=await request.app.state.manager.chat(body.message,body.media_ids)
        if result.get('status')=='applied':
            from app.services.learning import LearningService
            result['resumed']=await LearningService(request.app.state.db,request.app.state.vault).resume(request.app.state)
        return masked(request,result)
    except PermissionError as exc:
        raise HTTPException(403,str(exc)) from None
    except ManagerError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(503, "Service IA/Guesty indisponible. Aucun changement appliqué.") from None

@router.post("/changes/{change_id}/confirm")
async def confirm(change_id: str, body: Confirmation, request: Request):
    try:
        result=await request.app.state.manager.confirm(change_id, body.accept)
        if result.get('status')=='applied':
            from app.services.learning import LearningService
            result['resumed']=await LearningService(request.app.state.db,request.app.state.vault).resume(request.app.state)
        return result
    except ManagerError as exc:
        raise HTTPException(409, str(exc)) from None

@router.get("/data")
def data(request: Request):
    db, vault = request.app.state.db, request.app.state.vault
    with db.session() as session:
        properties = [{"id": p.id, "name": p.name, "listing_id": p.guesty_listing_id,
            "timezone": p.timezone, "facts": p.facts, "onboarding_step": p.onboarding_step,
            "states": [{"key": s.key, "status": s.status, "note": s.note, "valid_until": s.valid_until}
                for s in session.scalars(select(PropertyState).where(PropertyState.property_id == p.id,
                    PropertyState.valid_from <= time.time())) if s.valid_until is None or s.valid_until > time.time()],
            "mappings": [{"kind": m.kind, "id": m.external_id} for m in session.scalars(select(GuestyMapping).where(GuestyMapping.property_id == p.id))]}
            for p in session.scalars(select(Property).order_by(Property.name))]
        escalations = [{"id": e.id, "property_id": e.property_id, "conversation_id": e.guesty_conversation_id,
            "summary": vault.decrypt(e.encrypted_summary), "priority": e.priority, "notification": e.notification_status}
            for e in session.scalars(select(Escalation).where(Escalation.status == "open").order_by(Escalation.created_at.desc()).limit(200))]
        previews = [{"id": m.id, "conversation_id": m.conversation_id, "text": vault.decrypt(m.encrypted_body),
            "status": m.status, "created_at": m.created_at} for m in session.scalars(select(SentMessage).order_by(SentMessage.created_at.desc()).limit(50))]
        changes = [{"id": c.id, "property_id": c.property_id, "old": vault.decrypt(c.encrypted_old),
            "new": vault.decrypt(c.encrypted_new)} for c in session.scalars(select(ManagerChange).where(ManagerChange.status == "pending", ManagerChange.expires_at > time.time()))]
        audit = [{"source": a.source, "created_at": a.created_at, "change": vault.decrypt(a.encrypted_change)}
            for a in session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(50))]
        failures = [{"id": e.id, "conversation_id": e.conversation_id, "status": e.status, "reason": e.error}
            for e in session.scalars(select(Event).where(Event.status.in_(["failed", "blocked", "quarantined"])).limit(100))]
    return masked(request, {"test_mode": request.app.state.settings.test_mode, "properties": properties, "escalations": escalations,
        "previews": previews, "changes": changes, "audit": audit, "failures": failures})

@router.post("/simulate")
async def simulate(payload: dict, request: Request):
    if not request.app.state.settings.test_mode:
        raise HTTPException(403, "Simulation uniquement en TEST_MODE")
    return request.app.state.queue.enqueue(payload)

@router.post("/events/{event_id}/retry")
def retry_event(event_id: str, request: Request):
    with request.app.state.db.session() as session:
        row = session.get(Event, event_id)
        if not row or row.status != "failed":
            raise HTTPException(409, "Seuls les événements en échec sans envoi peuvent être relancés")
        row.status, row.attempts, row.due_at = "pending", 0, time.time()+request.app.state.settings.debounce_seconds
        session.commit()
    return {"status": "pending"}

class StyleApproval(BaseModel):
    human_message_ids: list[str] = Field(default_factory=list, max_length=100)

@router.post("/style/{conversation_id}")
async def learn_style(conversation_id: str, body: StyleApproval, request: Request):
    try:
        posts = await request.app.state.guesty.posts(conversation_id, 100)
        return await request.app.state.style.learn(conversation_id, posts, body.human_message_ids)
    except Exception:
        raise HTTPException(503, "Apprentissage indisponible") from None

@router.post("/messages/{message_id}/reconcile")
async def reconcile(message_id: str, request: Request):
    db = request.app.state.db
    async with db.lock("reconcile:"+message_id):
        with db.session() as session:
            row = session.get(SentMessage, message_id)
            if not row or row.status not in {"sending", "uncertain"}:
                raise HTTPException(409, "Pas d'envoi incertain à réconcilier")
            posts = await request.app.state.guesty.posts(row.conversation_id, 100)
            matches = [p for p in posts if fingerprint(p.get("body", "")) == row.body_hash
                and p.get("from", {}).get("type") != "guest"]
            if len(matches) != 1:
                raise HTTPException(409, "Message non retrouvé de manière unique : vérification manuelle Guesty nécessaire")
            row.guesty_message_id, row.status = matches[0]["_id"], "sent"
            session.commit()
    return {"status": "sent", "message": "Envoi retrouvé dans Guesty. Aucun renvoi effectué."}


@router.get("/guesty/reservations/{reservation_id}/identity")
async def inspect_identity(reservation_id: str, request: Request):
    """Read-only Guesty diagnosis: no guessed ID equivalence and no configuration mutation."""
    try:
        reservation = await request.app.state.guesty.reservation(reservation_id)
        cid = reservation.get("conversationId")
        conversation = await request.app.state.guesty.conversation(cid) if cid else {}
        meta = [r for r in conversation.get("meta", {}).get("reservations", []) if r.get("_id") == reservation_id]
        return {"reservation_id": reservation_id, "conversation_id": cid,
            "status": reservation.get("status"),
            "stay": [{key: stay.get(key) for key in ["unitId", "unitTypeId", "checkInDateLocalized", "checkOutDateLocalized"]}
                for stay in reservation.get("stay", [])],
            "conversation_listing_ids": [r.get("listing", {}).get("_id") for r in meta],
            "message": "Confirmez les correspondances exactes avec le manager. Un type partagé ou plusieurs séjours ne désignent pas nécessairement un appartement unique."}
    except Exception:
        raise HTTPException(503, "Identité Guesty non vérifiable") from None


@router.get("/style/{conversation_id}/candidates")
async def style_candidates(conversation_id: str, request: Request):
    from app.db.models import PropertyAccess, PropertyWifi
    state = request.app.state
    try:
        posts = await state.guesty.posts(conversation_id, 100)
        with state.db.session() as session:
            sent = session.scalars(select(SentMessage).where(SentMessage.conversation_id == conversation_id)).all()
            own_ids = {m.guesty_message_id for m in sent}
            own_hashes = {m.body_hash for m in sent}
            secrets_to_mask = []
            for model in [PropertyAccess, PropertyWifi]:
                for row in session.scalars(select(model)):
                    secrets_to_mask += [v for v in state.vault.decrypt(row.encrypted_data).values() if isinstance(v, str)]
        return {"candidates": [{"id": p["_id"], "text": state.vault.redact(p.get("body", ""), secrets_to_mask)}
            for p in posts if p.get("_id") and p["_id"] not in own_ids and p.get("from", {}).get("type") == "user"
            and p.get("isAutomatic") is not True and fingerprint(p.get("body", "")) not in own_hashes
            and p.get("module", {}).get("type") not in {"note", "log"}]}
    except Exception:
        raise HTTPException(503, "Historique de style indisponible") from None

def masked(request, data):
    with request.app.state.db.session() as session:
        secret_values=[]
        from app.db.models import PropertyAccess,PropertyWifi
        for model in [PropertyAccess,PropertyWifi]:
            for row in session.scalars(select(model)):
                values=request.app.state.vault.decrypt(row.encrypted_data)
                secret_values.extend(v for k,v in values.items() if k in SECRET_FIELDS and isinstance(v,str) and v)
    def walk(value,key=""):
        if key in SECRET_FIELDS: return "••••••••" if value else ""
        if isinstance(value,dict):
            if value.get("field") in SECRET_FIELDS and "value" in value:
                value={**value,"value":"••••••••"}
            return {k:walk(v,k) for k,v in value.items()}
        if isinstance(value,list):return [walk(v,key) for v in value]
        if isinstance(value,str) and key in {"message","text","body","note","summary","request","temporary_response","temporary_reply","reply","received","corrected","reason","escalation_reason"}:
            for secret in sorted(secret_values,key=len,reverse=True):value=value.replace(secret,"••••••••")
            # Typed secrets in the chat are masked even before their confirmation.
            import re
            value=re.sub(r"((?:code|mot de passe)[^\n]{0,60}?\b(?:est|par|=)\s*)\S+",r"\1••••••••",value,flags=re.I)
        return value
    return walk(data)

@router.get("/session")
def session_info(request:Request):
    identity=authenticate(request)
    return {**identity,"test_mode":request.app.state.settings.test_mode,"agent_mode":request.app.state.settings.agent_mode,"live_allowed":request.app.state.settings.allow_live_sends,"local_mode":getattr(request.app.state.ai,"client",True) is None}

@router.post("/logout")
def logout(request:Request):
    request.app.state.sessions.logout(request.cookies.get(COOKIE))
    response=JSONResponse({"ok":True});response.delete_cookie(COOKIE,path="/admin");return response

@router.get("/history")
def history(request:Request):
    return masked(request,{"messages":request.app.state.history.list(),"workspace":request.app.state.history.workspace(),"media":request.app.state.media.list_unassigned()})

@router.get("/properties")
def properties(request:Request):return request.app.state.properties.list()

@router.get("/properties/{property_id}")
def property_detail(property_id:str,request:Request):
    try:return request.app.state.properties.detail(property_id)
    except ValueError:raise HTTPException(404,"Logement introuvable") from None

class RevealInput(BaseModel):
    field:str
@router.post("/properties/{property_id}/reveal")
def reveal(property_id:str,body:RevealInput,request:Request):
    try:
        value=request.app.state.properties.reveal(property_id,body.field)
        with request.app.state.db.session() as session:
            session.add(AuditLog(property_id=property_id,source="manager:secret_reveal",encrypted_change=request.app.state.vault.encrypt({"field":body.field,"actor":authenticate(request)["user_id"]})));session.commit()
        return {"value":value}
    except ValueError:raise HTTPException(404,"Champ introuvable") from None

@router.post("/actions")
async def actions(body:ManagerAction,request:Request):
    try:
        result=await request.app.state.manager.propose(body)
        request.app.state.history.append("assistant",result)
        return masked(request,result)
    except PermissionError as exc:raise HTTPException(403,str(exc)) from None
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

class ListingChoice(BaseModel):
    listing_id:str
    name:str|None=None
@router.post("/onboarding/select")
async def select_listing(body:ListingChoice,request:Request):
    try:
        result=await request.app.state.manager.select_listing(body.listing_id,body.name)
        request.app.state.history.append("assistant",result)
        return masked(request,result)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except Exception:raise HTTPException(503,"Guesty indisponible. Réessayez après reconnexion.") from None

@router.get("/alerts")
def alerts(request:Request):return masked(request,request.app.state.decisions.alerts())

@router.post("/decisions/{decision_id}/confirm")
async def confirm_decision(decision_id:str,body:Confirmation,request:Request):
    try:return masked(request,await request.app.state.decisions.confirm(decision_id,body.accept))
    except ValueError as exc:raise HTTPException(409,str(exc)) from None

@router.get("/stays")
def stays(request:Request):return {"groups":request.app.state.reservations.groups(),"sync":request.app.state.reservations.sync_status()}

@router.get("/stays/{reservation_id}")
def stay_detail(reservation_id:str,request:Request):
    try:return request.app.state.reservations.detail(reservation_id)
    except ValueError as exc:raise HTTPException(404,str(exc)) from None

@router.post("/stays/sync")
async def sync_stays(request:Request):
    await request.app.state.reservations.sync(request.app.state.guesty,request.app.state.builder)
    return request.app.state.reservations.sync_status()

@router.get("/discussion/{escalation_id}")
async def discussion(escalation_id:str,request:Request):
    with request.app.state.db.session() as session:
        escalation=session.get(Escalation,escalation_id)
        if not escalation:raise HTTPException(404,"Alerte introuvable")
        cid=escalation.guesty_conversation_id
        from app.db.models import ReservationSnapshot
        snapshot=session.scalar(select(ReservationSnapshot).where(ReservationSnapshot.conversation_id==cid))
        if snapshot:
            return masked(request,{"posts":request.app.state.vault.decrypt(snapshot.encrypted_context).get("recent_history",[])})
    try:
        posts=await request.app.state.guesty.posts(cid,100)
        return masked(request,{"posts":[{"sender":p.get("from",{}).get("type"),"body":p.get("body","")} for p in posts]})
    except Exception:raise HTTPException(503,"Discussion Guesty indisponible") from None

@router.post("/media")
async def upload_media(request:Request,file:UploadFile=File(...),kind:str=Form(...),property_id:str|None=Form(None)):
    try:
        data=await file.read(request.app.state.settings.media_max_mb*1024*1024+1)
        return await request.app.state.media.upload(file.filename or "fichier",data,kind,property_id)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    finally:await file.close()

@router.get("/media/{asset_id}")
async def media_download(asset_id:str,request:Request):
    try:
        content,metadata=await request.app.state.media.download(asset_id)
        from urllib.parse import quote
        return Response(content,media_type=metadata["type"],headers={"Content-Disposition":"attachment; filename*=UTF-8''"+quote(metadata["name"],safe=""),"Content-Security-Policy":"sandbox"})
    except ValueError:raise HTTPException(404,"Média introuvable") from None

@router.get("/settings")
def settings_summary(request:Request):
    s=request.app.state.settings
    identity=authenticate(request)
    return {"workspace":identity["workspace"],"role":identity["role"],"test_mode":s.test_mode,"agent_mode":s.agent_mode,"live_allowed":s.allow_live_sends,"openai_connected":bool(s.openai_api_key),
        "guesty_connected":bool(s.guesty_client_id and s.guesty_client_secret),"webhook_configured":bool(s.guesty_webhook_secret),"media_storage":s.media_storage,
        "webhook_path":"/guesty/webhook/"+identity["organization_id"],"model":s.openai_model}

class GuestyCredentials(BaseModel):
    client_id:str=Field(min_length=1,max_length=500)
    client_secret:str=Field(min_length=1,max_length=1000)
    webhook_secret:str=Field(default="",max_length=1000)
@router.post("/settings/guesty")
async def connect_guesty(body:GuestyCredentials,request:Request):
    from app.services.provider_connections import ProviderConnections
    from app.guesty.errors import GuestyError, ContractError
    service = ProviderConnections(request.app.state.db, request.app.state.vault, request.app.state.settings)
    try:
        await service.guesty({"guesty_client_id":body.client_id.strip(), "guesty_client_secret":body.client_secret.strip(), "guesty_webhook_secret":body.webhook_secret.strip() or request.app.state.settings.guesty_webhook_secret})
    except GuestyError as exc:
        raise HTTPException(422, f"Connexion Guesty refusée ou indisponible (HTTP {exc.status}). Vérifiez vos identifiants et l’accès Open API. La connexion précédente est conservée.") from None
    except ContractError:
        raise HTTPException(502, "Réponse Guesty inattendue. Connexion non enregistrée.") from None
    sync=await request.app.state.listing_sync.sync(force=True)
    return {"status":"connected", "sync":sync,"message":f"Guesty connecté. {sync.get('count',0)} logement(s) retrouvés automatiquement." if sync['state']=='ready' else 'Guesty connecté. La découverte des logements sera réessayée automatiquement.'}

@router.get('/properties/sync/status')
def listing_sync_status(request:Request):
    return request.app.state.listing_sync.status()

@router.post('/properties/sync')
async def synchronize_properties(request:Request):
    return await request.app.state.listing_sync.sync(force=True)

class OpenAICredentials(BaseModel):
    api_key: str = Field(min_length=20, max_length=1000)

@router.post("/settings/openai")
async def connect_openai(body:OpenAICredentials, request:Request):
    from app.services.provider_connections import ProviderConnections
    from openai import APIError
    service = ProviderConnections(request.app.state.db, request.app.state.vault, request.app.state.settings)
    try:
        await service.openai(body.api_key.strip())
    except APIError:
        raise HTTPException(422, "OpenAI inaccessible : vérifiez la clé, ses permissions et l’accès au modèle configuré. La connexion précédente est conservée.") from None
    return {"status":"connected", "message":"Clé OpenAI chiffrée enregistrée et accès au modèle vérifié. Le chat est disponible sans redémarrage."}

@router.post("/modes/{change_id}/confirm")
async def confirm_mode(change_id:str,body:Confirmation,request:Request):
    try:
        result=await request.app.state.modes.confirm(change_id,body.accept)
        request.app.state.history.append("assistant",result)
        return result
    except PermissionError as exc:raise HTTPException(403,str(exc)) from None
    except ValueError as exc:raise HTTPException(409,str(exc)) from None

@router.get("/previews")
def previews(request:Request):
    with request.app.state.db.session() as session:
        rows=session.scalars(select(SentMessage).where(SentMessage.status=="simulated").order_by(SentMessage.created_at.desc()).limit(50))
        return masked(request,[{"id":row.id,"conversation_id":row.conversation_id,"text":request.app.state.vault.decrypt(row.encrypted_body),"label":"AI WOULD REPLY","created_at":row.created_at} for row in rows])

@router.get("/feed")
def test_feed(request:Request,before:float|None=None):
    return masked(request,request.app.state.training.feed(before=before))

class FeedbackInput(BaseModel):
    kind:str
    corrected:str=Field(default="",max_length=8000)
    reason:str=Field(default="",max_length=2000)
@router.post("/feed/{observation_id}/feedback")
async def feedback(observation_id:str,body:FeedbackInput,request:Request):
    try:return await request.app.state.training.feedback(observation_id,body.kind,body.corrected,body.reason)
    except PermissionError as exc:raise HTTPException(403,str(exc)) from None
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@router.get("/settings/style")
def response_style(request:Request):return request.app.state.training.style()

class PreferenceInput(BaseModel):
    values:dict
@router.post("/settings/style")
async def set_style(body:PreferenceInput,request:Request):
    # Managers may edit style; integration settings remain Owner/Admin-only.
    try:return await request.app.state.training.preference(values=body.values)
    except PermissionError as exc:raise HTTPException(403,str(exc)) from None
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@router.post("/settings/style/learn")
async def relearn_style(request:Request):
    try:
        request.app.state.training.actor()
        return await request.app.state.training.learn()
    except PermissionError as exc:raise HTTPException(403,str(exc)) from None
    except Exception:raise HTTPException(503,"Extraction du style indisponible. Vos corrections restent enregistrées.") from None

@router.get("/listing-counts")
def listing_counts(request:Request,at:float|None=None):
    return request.app.state.manager.listings.counts(request.app.state.db,at)

@router.get("/properties/{property_id}/lifecycle")
def lifecycle(property_id:str,request:Request):
    from app.db.models import ListingLifecycle
    with request.app.state.db.session() as session:
        if not session.get(Property,property_id):raise HTTPException(404,"Logement introuvable")
        return [{"from_status":row.from_status,"to_status":row.to_status,"occurred_at":row.occurred_at,"source":row.source,"actor":row.actor} for row in session.scalars(select(ListingLifecycle).where(ListingLifecycle.property_id==property_id).order_by(ListingLifecycle.occurred_at))]

class GoogleConfiguration(BaseModel):
    client_id: str = Field(min_length=10, max_length=500)
    client_secret: str = Field(min_length=10, max_length=1000)

@router.get('/settings/drive')
def drive_summary(request:Request):
    base=request.app.state.settings.public_base_url or str(request.base_url).rstrip('/')
    return {**request.app.state.drive.summary(), 'redirect_uri':base+'/admin/drive/callback'}

@router.post('/settings/drive/configuration')
async def configure_drive(body:GoogleConfiguration,request:Request):
    async with request.app.state.db.lock('drive_authorization'):
        if request.app.state.drive.summary()['connected']:
            raise HTTPException(409,'Déconnectez Drive avant de changer l’application Google.')
        request.app.state.drive.write('provider:drive-config',body.model_dump())
    return {'message':'Application Google enregistrée. Cliquez maintenant sur Connecter Google Drive.'}

@router.post('/settings/drive/connect')
def connect_drive(request:Request):
    identity=authenticate(request)
    base=request.app.state.settings.public_base_url or str(request.base_url).rstrip('/')
    try:
        return {'url':request.app.state.drive.start(base+'/admin/drive/callback',identity,request.cookies.get(COOKIE,''))}
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

class GoogleCallback(BaseModel):
    code: str = Field(min_length=1,max_length=4000)
    state: str = Field(min_length=20,max_length=200)

@router.post('/settings/drive/finish')
async def finish_drive(body:GoogleCallback,request:Request):
    try:
        await request.app.state.drive.finish(body.code,body.state,authenticate(request),request.cookies.get(COOKIE,''))
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    return {'message':'Google Drive connecté. Vos prochains fichiers pourront être stockés automatiquement.'}

@router.post('/settings/drive/disconnect')
async def disconnect_drive(request:Request):
    async with request.app.state.db.lock('drive_authorization'):
        request.app.state.drive.write('provider:drive',{})
        with request.app.state.db.session() as session:
            session.add(AuditLog(source='settings:drive_disconnected',encrypted_change=request.app.state.vault.encrypt({'disconnected':True})))
            session.commit()
    return {'message':'Drive déconnecté de cet espace. Les fichiers et liens déjà créés restent dans votre Drive.'}

class MediaAnswer(BaseModel):
    field: str = Field(pattern='^(access_video|access_photos|arrival_guide)$')
    available: bool

@router.post('/properties/{property_id}/media-answer')
async def media_answer(property_id:str,body:MediaAnswer,request:Request):
    try:
        async with request.app.state.db.lock('manager_changes'):
            return request.app.state.property_media.answer(property_id,body.field,body.available)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@router.post('/properties/{property_id}/media')
async def property_upload(property_id:str,request:Request,file:UploadFile=File(...),kind:str=Form(...),confirm:bool=Form(...),share_guest:bool=Form(False)):
    try:
        if not confirm:raise ValueError('Confirmez le logement avant d’ajouter le fichier.')
        data=await file.read(request.app.state.settings.media_max_mb*1024*1024+1)
        return await request.app.state.property_media.upload(property_id,file.filename or 'fichier',data,kind,share_guest)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    finally:await file.close()

class MediaPublishing(BaseModel):
    share_guest: bool = False

@router.post('/media/{asset_id}/drive')
async def publish_media(asset_id:str,body:MediaPublishing,request:Request):
    try:return await request.app.state.property_media.publish(asset_id,body.share_guest)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@router.get('/overview')
def overview(request:Request):
    from app.db.models import ManagerWorkspace,SentMessage,AuditLog
    from sqlalchemy import func
    s=request.app.state.settings
    properties=request.app.state.properties.list()
    alerts=request.app.state.decisions.alerts('')
    with request.app.state.db.session() as session:
        row=session.get(ManagerWorkspace,'provider:guesty')
        verified=bool(row and request.app.state.vault.decrypt(row.encrypted_data).get('verified_at'))
        activity=[{'source':r.source,'at':r.created_at,'property_id':r.property_id} for r in session.scalars(select(AuditLog).where(AuditLog.source.like('manager:%')).order_by(AuditLog.created_at.desc()).limit(5))]
        simulated=session.scalar(select(func.count()).select_from(SentMessage).where(SentMessage.status=='simulated'))
        sent=session.scalar(select(func.count()).select_from(SentMessage).where(SentMessage.status=='sent'))
    return {'workspace':authenticate(request)['workspace'],'guesty_connected':verified,
        'attention':alerts,'activity':activity,'sync':request.app.state.listing_sync.status(),'handled':{'simulated':simulated,'sent':sent},
        'openai_connected':bool(s.openai_api_key),'drive':request.app.state.drive.summary(),
        'agent_mode':s.agent_mode,'live_allowed':s.allow_live_sends,'properties':properties,
        'counts':{'properties':len(properties),'active':sum(p['is_active'] for p in properties),
            'incomplete':sum(bool(p['configuration']['missing']) for p in properties),'alerts':len(alerts)}}

@router.post('/properties/{property_id}/teach')
async def teach_property(property_id:str,request:Request):
    app=request.app.state
    with app.db.session() as session:
        prop=session.get(Property,property_id)
        if not prop:raise HTTPException(404,'Logement introuvable.')
        app.history.set_workspace({'property_id':prop.id,'name':prop.name})
        progress=app.manager.onboarding.progress(session,prop)
        questions=progress['questions'][:2]
        from app.db.models import ManagerWorkspace
        from app.services.onboarding import QUESTIONS
        for row in session.scalars(select(ManagerWorkspace).where(ManagerWorkspace.id.like('learn:%'))):
            pending=app.vault.decrypt(row.encrypted_data)
            if pending.get('property_id')==property_id and pending.get('status')=='waiting' and pending.get('field') in progress['missing']:
                questions=[{'question':QUESTIONS[pending['field']]}];break
        message='Pour '+prop.name+', '+(' '.join(q['question'] for q in questions) if questions else 'je connais déjà les informations utiles. Quel changement souhaitez-vous me signaler ?')
    result={'status':'read','message':message,'property_id':property_id}
    app.history.append('assistant',result)
    return result

class WebhookSecret(BaseModel):
    secret: str = Field(min_length=10,max_length=1000)

@router.post('/settings/guesty/webhook')
async def save_webhook_secret(body:WebhookSecret,request:Request):
    from app.db.models import GuestyConnection
    async with request.app.state.db.lock('guesty_configuration'):
        with request.app.state.db.session() as session:
            row=session.scalar(select(GuestyConnection).where(GuestyConnection.enabled.is_(True)))
            if not row:raise HTTPException(422,'Connectez d’abord votre compte Guesty.')
            credentials=request.app.state.vault.decrypt(row.encrypted_credentials)
            credentials['guesty_webhook_secret']=body.secret.strip()
            row.encrypted_credentials=request.app.state.vault.encrypt(credentials)
            session.add(AuditLog(source='settings:webhook',encrypted_change=request.app.state.vault.encrypt({'signature_secret_updated':True})))
            session.commit()
    return {'message':'Secret de signature enregistré pour votre conciergerie.'}

@router.get('/observation')
def observation_status(request:Request):
    return request.app.state.observation.status()

@router.post('/observation/sync')
async def observe_inbox(request:Request):
    try:return await request.app.state.observation.sync()
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@router.get('/inbox')
def received_inbox(request:Request):
    return masked(request,request.app.state.observation.inbox())

class ObservationToggle(BaseModel):
    enabled: bool

@router.post('/settings/observation')
async def toggle_observation(body:ObservationToggle,request:Request):
    async with request.app.state.db.lock('observation_settings'):
        request.app.state.observation.configure(body.enabled)
    return request.app.state.observation.status()

@router.post('/inbox/{conversation_id}/identity')
async def identity_proposal(conversation_id:str,request:Request):
    from app.services.guesty_identity import GuestyIdentityService
    try:return await GuestyIdentityService(request.app.state.db,request.app.state.vault,request.app.state.guesty).propose(conversation_id)
    except PermissionError as exc:raise HTTPException(403,str(exc)) from None
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except Exception:raise HTTPException(503,'Guesty indisponible. Réessayez la vérification.') from None

@router.post('/identity/{change_id}/confirm')
async def identity_confirmation(change_id:str,body:Confirmation,request:Request):
    from app.services.guesty_identity import GuestyIdentityService
    try:return await GuestyIdentityService(request.app.state.db,request.app.state.vault,request.app.state.guesty).confirm(change_id,body.accept)
    except PermissionError as exc:raise HTTPException(403,str(exc)) from None
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
