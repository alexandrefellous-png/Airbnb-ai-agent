import time
from sqlalchemy import select,text
from app.core.tenancy import organization_id,manager_identity
from app.db.models import Organization,Membership,AgentModeChange,AuditLog

class AgentModeService:
    def __init__(self,db,vault,settings):
        self.db,self.vault,self.settings=db,vault,settings

    def authorized(self,session):
        identity=manager_identity()
        if not identity or identity.get("organization_id")!=organization_id():raise PermissionError("Session Owner/Admin requise")
        member=session.get(Membership,(organization_id(),identity["user_id"]))
        if not member or member.role not in {"owner","admin"}:raise PermissionError("Seul un Owner/Admin peut changer le mode")
        return identity["user_id"]

    def transaction(self):
        session=self.db.system_session()
        if self.db.engine.dialect.name=="postgresql":
            session.execute(text("SELECT set_config('app.organization_id', :tenant, true)"),{"tenant":organization_id()})
        return session

    async def propose(self,mode):
        if mode not in {"test","live"}:raise ValueError("Mode invalide")
        async with self.db.lock("agent_mode"):
            with self.transaction() as session:
                actor=self.authorized(session)
                org=session.get(Organization,organization_id(),with_for_update=True)
                if org.agent_mode==mode:return {"status":"unchanged","message":"L’agent est déjà en "+mode.upper()+".","agent_mode":mode}
                for pending in session.scalars(select(AgentModeChange).where(AgentModeChange.organization_id==org.id,AgentModeChange.status=="pending")):
                    pending.status="superseded"
                if mode=="test":
                    old=org.agent_mode;org.agent_mode="test";org.mode_revision+=1
                    session.add(AuditLog(organization_id=org.id,source="manager:agent_mode",manager=actor,encrypted_change=self.vault.encrypt({"old":old,"new":"test","actor":actor})))
                    session.commit()
                    return {"status":"applied","agent_mode":"test","message":"TEST MODE activé. Les prochaines réponses seront générées en aperçu : AI WOULD REPLY. Aucun nouvel envoi Guesty."}
                row=AgentModeChange(organization_id=org.id,requested_mode=mode,expected_revision=org.mode_revision,proposed_by=actor,expires_at=time.time()+900)
                session.add(row);session.commit()
                consequence="En LIVE, l’agent peut envoyer automatiquement ses réponses aux voyageurs dans Guesty. Les anciennes simulations ne seront pas renvoyées."
                if not self.settings.allow_live_sends:consequence+=" La sécurité serveur ALLOW_LIVE_SENDS est désactivée : les envois resteront bloqués même en LIVE."
                return {"status":"pending_mode","mode_change_id":row.id,"agent_mode":"live","live_allowed":self.settings.allow_live_sends,"message":consequence+" Confirmez-vous ce changement pour votre workspace ?"}

    async def confirm(self,change_id,accept):
        async with self.db.lock("agent_mode"):
            with self.transaction() as session:
                actor=self.authorized(session)
                row=session.scalar(select(AgentModeChange).where(AgentModeChange.organization_id==organization_id(),AgentModeChange.id==change_id))
                if not row or row.status!="pending" or row.expires_at<time.time():raise ValueError("Confirmation expirée ou déjà traitée")
                org=session.get(Organization,organization_id(),with_for_update=True)
                if not accept:row.status="rejected";session.commit();return {"status":"rejected","message":"Passage en LIVE annulé."}
                if org.mode_revision!=row.expected_revision:raise ValueError("Le mode a changé entre-temps. Reformulez la demande.")
                old=org.agent_mode;org.agent_mode=row.requested_mode;org.mode_revision+=1;row.status="applied"
                session.add(AuditLog(organization_id=org.id,source="manager:agent_mode",manager=actor,encrypted_change=self.vault.encrypt({"old":old,"new":org.agent_mode,"actor":actor,"change_id":row.id,"allow_live_sends":self.settings.allow_live_sends})))
                session.commit()
                return {"status":"applied","agent_mode":"live","live_allowed":self.settings.allow_live_sends,"message":"LIVE activé pour votre workspace."+(" Les envois restent bloqués par ALLOW_LIVE_SENDS=false." if not self.settings.allow_live_sends else " Les prochaines réponses pourront être envoyées aux voyageurs.")}
