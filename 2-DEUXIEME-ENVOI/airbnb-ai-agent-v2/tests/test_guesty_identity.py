import pytest
from sqlalchemy import select, delete
from app.db.models import GuestyMapping, Property, AuditLog
from app.services.guesty_identity import GuestyIdentityService
from conftest import payload_for

async def test_verified_relationship_requires_confirmation_and_cannot_be_replayed(env):
    payload=payload_for(env)
    with env[1].session() as session:
        prop=session.scalar(select(Property).where(Property.name=='CAIRE1'))
        pid=prop.id
        session.execute(delete(GuestyMapping).where(GuestyMapping.property_id==pid,GuestyMapping.kind.in_(['unit','unit_type'])))
        session.commit()
    service=GuestyIdentityService(env[1],env[2],env[3])
    proposal=await service.propose(payload['conversation']['_id'])
    assert proposal['listing_id']=='listing-a'
    assert {p['kind'] for p in proposal['pairs']}=={'unit','unit_type'}
    with env[1].session() as session:
        assert len(list(session.scalars(select(GuestyMapping).where(GuestyMapping.property_id==pid))))==1
    assert (await service.confirm(proposal['change_id'],True))['status']=='confirmed'
    with env[1].session() as session:
        assert len(list(session.scalars(select(GuestyMapping).where(GuestyMapping.property_id==pid))))==3
        assert session.scalar(select(AuditLog).where(AuditLog.source=='guesty:identity_confirmation'))
    with pytest.raises(ValueError):await service.confirm(proposal['change_id'],True)
    assert not env[3].send_calls

async def test_conflicting_fresh_ids_and_changed_property_are_rejected(env):
    payload=payload_for(env)
    service=GuestyIdentityService(env[1],env[2],env[3])
    proposal=await service.propose(payload['conversation']['_id'])
    with env[1].session() as session:
        prop=session.get(Property,proposal['property_id']);prop.version+=1;session.commit()
    with pytest.raises(ValueError,match='fiche a changé'):await service.confirm(proposal['change_id'],True)
    env[3].reservations[payload['reservationId']]['stay'][0]['unitId']='unit-b'
    with pytest.raises(ValueError,match='autre logement'):await service.propose(payload['conversation']['_id'])
    assert not env[3].send_calls
