import pytest
from sqlalchemy import select,func
from app.db.models import Property,PropertyAccess,PropertyState,AuditLog,ManagerChange
from app.schemas.actions import ManagerAction,FieldUpdate
from app.services.manager import ManagerService,ManagerError

BASE=dict(property='CAIRE1',updates=[],state_key=None,state_status=None,valid_until=None,note=None,
    mapping_kind=None,external_id=None,escalation_id=None,confidence=.99,message='')
def action(intent,**kwargs): return ManagerAction(intent=intent,**{**BASE,**kwargs})
def manager(env): return ManagerService(env[1],env[2],env[4],env[3])

async def test_elevator_repaired_preserves_existence(env):
    await manager(env).propose(action('update_property_state',state_key='elevator',state_status='working'))
    with env[1].session() as session:
        prop=session.scalar(select(Property).where(Property.name=='CAIRE1'))
        state=session.scalar(select(PropertyState).where(PropertyState.property_id==prop.id))
        assert prop.facts['elevator'] is True and state.status=='working'
        assert session.scalar(select(func.count()).select_from(AuditLog))==1

async def test_lockbox_requires_confirmation_then_applies(env):
    svc=manager(env)
    result=await svc.propose(action('update_fields',updates=[FieldUpdate(field='lockbox_code',value='TEST-NEW-CODE')]))
    assert result['status']=='pending_confirmation'
    with env[1].session() as session:
        prop=session.scalar(select(Property).where(Property.name=='CAIRE1'))
        assert env[2].decrypt(session.get(PropertyAccess,prop.id).encrypted_data)['lockbox_code']!='TEST-NEW-CODE'
    await svc.confirm(result['change_id'],True)
    with env[1].session() as session:
        assert env[2].decrypt(session.get(PropertyAccess,prop.id).encrypted_data)['lockbox_code']=='TEST-NEW-CODE'
    with pytest.raises(ManagerError): await svc.confirm(result['change_id'],True)

async def test_confirmation_rejected_no_write(env):
    svc=manager(env)
    result=await svc.propose(action('update_fields',updates=[FieldUpdate(field='address',value='Nouvelle adresse')]))
    await svc.confirm(result['change_id'],False)
    with env[1].session() as session:
        assert session.scalar(select(Property).where(Property.name=='CAIRE1')).address=='Même adresse de test'

async def test_stale_confirmation_rejected(env):
    svc=manager(env)
    result=await svc.propose(action('update_fields',updates=[FieldUpdate(field='building_code',value='TEST-NEW')]))
    await svc.propose(action('update_property_state',state_key='elevator',state_status='working'))
    with pytest.raises(ManagerError): await svc.confirm(result['change_id'],True)

async def test_add_new_property_without_code_change(env):
    result=await manager(env).propose(action('create_property',property='CAIRE2'))
    assert result['status']=='applied'
    with env[1].session() as session:
        prop=session.scalar(select(Property).where(Property.name=='CAIRE2'))
        assert prop and prop.guesty_listing_id is None

async def test_onboarding_unconfigured_listings(env):
    result=await manager(env).propose(action('onboarding',property=None))
    assert result['status']=='onboarding' and result['listings'][0]['id']=='new-listing'

async def test_natural_language_manager_uses_structured_action(env):
    env[4].action=action('update_property_state',state_key='elevator',state_status='working')
    result=await manager(env).chat("CAIRE1 l'ascenseur remarche")
    assert result['status']=='applied'
    assert env[4].calls[-1][1]['message']=="CAIRE1 l'ascenseur remarche"

async def test_invalid_field_rejected(env):
    with pytest.raises(ManagerError):
        await manager(env).propose(action('update_fields',updates=[FieldUpdate(field='sql',value='DROP TABLE')]))

async def test_low_confidence_no_write(env):
    with pytest.raises(ManagerError):
        await manager(env).propose(action('update_property_state',state_key='elevator',state_status='working',confidence=.5))

async def test_mapping_sensitive_and_conflict_rejected(env):
    svc=manager(env)
    result=await svc.propose(action('map_id',mapping_kind='unit',external_id='unit-b'))
    assert result['status']=='pending_confirmation'
    with pytest.raises(ManagerError): await svc.confirm(result['change_id'],True)
