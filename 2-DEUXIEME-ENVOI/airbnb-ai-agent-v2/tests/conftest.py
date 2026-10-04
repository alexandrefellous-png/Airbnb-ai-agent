import copy
import time
from datetime import datetime, timedelta, timezone
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from app.core.config import Settings
from app.core.security import Vault
from app.db.models import Base, GuestyMapping, Property, PropertyAccess, PropertyState, PropertyWifi
from app.db.session import Database
from app.core.tenancy import organization_scope
from scripts.create_workspace import provision
from app.schemas.actions import GuestPlan, GuestReply

class FakeGuesty:
    def __init__(self):
        self.reservations, self.conversations, self.histories = {}, {}, {}
        self.calendar_calls, self.send_calls = [], []
        self.calendar_data = None

    async def reservation(self, rid): return copy.deepcopy(self.reservations[rid])
    async def conversation(self, cid): return copy.deepcopy(self.conversations[cid])
    async def posts(self, cid, limit=100): return copy.deepcopy(self.histories[cid])
    async def listing(self, lid): return {"_id": lid}
    async def listings(self): return [{"_id": "new-listing", "nickname": "Nouveau logement"}]
    async def calendar(self, lid, start, end):
        self.calendar_calls.append((lid, start, end))
        if self.calendar_data is not None: return self.calendar_data
        from datetime import date
        a,b = date.fromisoformat(start),date.fromisoformat(end)
        return {"data": {"days": [{"date": (a+timedelta(days=i)).isoformat(), "listingId": lid,
            "status": "available", "allotment": 1, "cta": False, "ctd": False, "minNights": 1}
            for i in range((b-a).days+1)]}}
    async def send(self, cid, text, module):
        self.send_calls.append((cid,text,module)); return "sent-1"

class FakeAI:
    def __init__(self):
        self.plan = GuestPlan(availability_requested=False, requested_check_in=None, requested_check_out=None,
            manager_required=False, priority="normal", reason=None)
        self.reply = GuestReply(text="Merci pour votre message :)", escalation_required=False,
            escalation_summary=None, priority="normal")
        self.calls = []
        self.action = None
    async def parse(self, schema, instructions, data):
        self.calls.append((schema, data))
        if schema is GuestPlan: return self.plan.model_copy(deep=True)
        if schema is GuestReply: return self.reply.model_copy(deep=True)
        if self.action: return self.action
        raise RuntimeError("fixture_missing")

@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, database_url="sqlite:///"+str(tmp_path/'test.db'),
        token_encryption_key=Fernet.generate_key().decode(), admin_secret="test-admin-password-very-long-12345",
        worker_enabled=False, debounce_seconds=0)
    db, vault = Database(settings.database_url), Vault(settings.token_encryption_key)
    Base.metadata.create_all(db.engine)
    oid=provision(db,vault,"Test workspace","admin",settings.admin_secret)
    from app.db.models import User
    with db.system_session() as session:
        user=session.scalar(select(User).where(User.email=="admin"));identity={"organization_id":oid,"user_id":user.id,"role":"owner"}
    scope=organization_scope(oid,identity);scope.__enter__()
    guesty, ai = FakeGuesty(), FakeAI()
    with db.session() as session:
        for name,lid,unit,door,network,heater in [
            ("CAIRE1","listing-a","unit-a","porte en face","Freebox-090283","cuisine au-dessus de la machine à laver"),
            ("!RUE31","listing-b","unit-b","porte gauche","Bbox-827DAB93","toilettes")]:
            prop=Property(name=name,guesty_listing_id=lid,address="Même adresse de test",timezone="Europe/Paris",
                check_in="16:00",check_out="10:00",facts={"elevator":name=="CAIRE1", "water_heater_location":heater,
                    "electrical_panel_location":"entrée"},onboarding_step="ready")
            session.add(prop);session.flush()
            for kind,external in [("listing",lid),("unit",unit),("unit_type","type-"+unit)]:
                session.add(GuestyMapping(property_id=prop.id,kind=kind,external_id=external,source="explicit_test_fixture"))
            session.add(PropertyAccess(property_id=prop.id,encrypted_data=vault.encrypt({"door":door,
                "building_code":"TEST-BUILDING-CODE", "lockbox_code":"TEST-BOX-"+unit})))
            session.add(PropertyWifi(property_id=prop.id,encrypted_data=vault.encrypt({"wifi_network":network,
                "wifi_password":"TEST-PASSWORD-"+unit})))
            if name=="CAIRE1": session.add(PropertyState(property_id=prop.id,key="elevator",status="out_of_order"))
        session.commit()
    try:
        yield settings,db,vault,guesty,ai
    finally:
        scope.__exit__(None,None,None)
        db.engine.dispose()

def payload_for(env, name="CAIRE1", status="confirmed", text="Bonjour", suffix="1", now=None):
    settings,db,vault,guesty,ai=env
    now=now or datetime.now(timezone.utc)
    with db.session() as session:
        prop=session.scalar(select(Property).where(Property.name==name))
        unit=session.scalar(select(GuestyMapping).where(GuestyMapping.property_id==prop.id,GuestyMapping.kind=="unit")).external_id
        lid=prop.guesty_listing_id
    cid,rid,mid="conv-"+name,"res-"+name,"msg-"+suffix
    stay={"unitId":unit,"unitTypeId":"type-"+unit,
        "checkInDateLocalized":now.date().isoformat(),"checkOutDateLocalized":(now+timedelta(days=2)).date().isoformat()}
    guesty.reservations[rid]={"_id":rid,"conversationId":cid,"status":status,"stay":[stay]}
    guesty.conversations[cid]={"_id":cid,"conversationWith":"Guest","language":"fr","meta":{
        "guestName":"Voyageur test","reservations":[{"_id":rid,"listing":{"_id":lid}}]}}
    post={"_id":mid,"conversationId":cid,"body":text,"createdAt":now.isoformat(),
        "from":{"type":"guest"},"module":{"type":"airbnb2"}}
    guesty.histories.setdefault(cid,[]).append(post)
    return {"event":"reservation.messageReceived","reservationId":rid,
        "conversation":{"_id":cid,"conversationWith":"Guest","language":"fr"},
        "message":{"_id":mid,"type":"fromGuest","body":text,"module":"airbnb2","createdAt":now.isoformat()}}
