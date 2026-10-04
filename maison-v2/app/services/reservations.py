import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.db.models import Escalation, Property, ReservationSnapshot, ManagerWorkspace
from app.services.properties import PropertyService

class ReservationService:
    def __init__(self, db, vault, settings):
        self.db,self.vault,self.settings=db,vault,settings
        self.properties=PropertyService(db,vault)

    def remember(self, context, reservation):
        if not context.reservation_id:return
        with self.db.session() as session:
            row=session.get(ReservationSnapshot,context.reservation_id)
            if not row:
                row=ReservationSnapshot(id=context.reservation_id,status=context.reservation_status or "unknown",encrypted_context="")
                session.add(row)
            stays=reservation.get("stay",[]) if reservation else []
            # Dates from the documented single stay only; never infer a multi-stay assignment.
            date_in=stays[0].get("checkInDateLocalized") if len(stays)==1 else None
            date_out=stays[0].get("checkOutDateLocalized") if len(stays)==1 else None
            row.property_id,row.conversation_id=context.property_id,context.conversation_id
            row.check_in=context.reservation_check_in or date_in
            row.check_out=context.reservation_check_out or date_out
            row.status,row.updated_at=context.reservation_status or "unknown",time.time()
            data=context.model_dump(exclude={"access","wifi","property_notes"})
            secrets=self.properties.secrets(session)
            for item in data.get("recent_history",[]):
                item["body"]=self.vault.redact(item.get("body",""),secrets)
            row.encrypted_context=self.vault.encrypt(data)
            session.commit()

    def view(self,session,row):
        context=self.vault.decrypt(row.encrypted_context)
        prop=session.get(Property,row.property_id) if row.property_id else None
        alerts=[e.id for e in session.scalars(select(Escalation).where(Escalation.reservation_id==row.id,Escalation.status=="open"))]
        return {"id":row.id,"guest_name":context.get("guest_name") or "Voyageur", "property_name":prop.name if prop else "Logement à vérifier",
            "property_id":row.property_id,"conversation_id":row.conversation_id,"check_in":row.check_in,"check_out":row.check_out,
            "status":row.status,"alerts":alerts,"updated_at":row.updated_at,"context":context,
            "last_message":context.get("recent_history",[])[-1].get("body","") if context.get("recent_history") else ""}

    def groups(self):
        groups={"current":[],"upcoming":[],"recent":[],"unresolved":[]}
        with self.db.session() as session:
            for row in session.scalars(select(ReservationSnapshot).order_by(ReservationSnapshot.check_in)):
                item=self.view(session,row)
                prop=session.get(Property,row.property_id) if row.property_id else None
                tz=ZoneInfo(prop.timezone) if prop and prop.timezone else ZoneInfo("UTC")
                now=datetime.now(tz)
                try:
                    arrival=datetime.fromisoformat(row.check_in);departure=datetime.fromisoformat(row.check_out)
                    if arrival.tzinfo is None:arrival=arrival.replace(tzinfo=tz)
                    if departure.tzinfo is None:departure=departure.replace(tzinfo=tz)
                except (TypeError,ValueError):
                    groups["unresolved"].append(item);continue
                if row.status not in {"confirmed","reserved","awaiting_payment"} or departure<=now:
                    group="recent"
                elif arrival<=now<departure:group="current"
                else:group="upcoming"
                groups[group].append(item)
        return groups

    def detail(self,reservation_id):
        with self.db.session() as session:
            row=session.get(ReservationSnapshot,reservation_id)
            if not row:raise ValueError("Séjour introuvable. Synchronisez Guesty ou attendez un événement voyageur.")
            return self.view(session,row)

    def sync_status(self):
        with self.db.session() as session:
            row=session.get(ManagerWorkspace,"reservation_sync")
            return self.vault.decrypt(row.encrypted_data) if row else {"status":"never"}

    def set_sync(self,data):
        with self.db.session() as session:
            row=session.get(ManagerWorkspace,"reservation_sync")
            if not row:row=ManagerWorkspace(id="reservation_sync",encrypted_data=self.vault.encrypt(data));session.add(row)
            row.encrypted_data,row.updated_at=self.vault.encrypt(data),time.time()
            session.commit()

    async def sync(self,guesty,builder):
        async with self.db.lock("reservation_sync"):
            self.set_sync({"status":"running","count":0,"updated_at":time.time()})
            count=0
            try:
                with self.db.session() as session:
                    ids=[p.guesty_listing_id for p in session.scalars(select(Property).where(Property.archived_at.is_(None))) if p.guesty_listing_id]
                for rid in await guesty.reservation_ids(ids):
                    reservation=await guesty.reservation(rid)
                    if not reservation.get("conversationId"):continue
                    await builder.build({"reservationId":rid,"conversation":{"_id":reservation["conversationId"]}})
                    count+=1
                    self.set_sync({"status":"running","count":count,"updated_at":time.time()})
                self.set_sync({"status":"done","count":count,"updated_at":time.time()})
            except Exception as exc:
                self.set_sync({"status":"failed","count":count,"error":type(exc).__name__,"updated_at":time.time()})
