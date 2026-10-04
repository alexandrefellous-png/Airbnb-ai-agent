import asyncio
import time
from sqlalchemy import select,func
from app.db.models import Escalation,Event,SentMessage
from app.main import create_app
from app.schemas.actions import GuestPlan,GuestReply
from conftest import payload_for

def app_for(env): return create_app(env[0],env[1],env[3],env[4])
def count(env,model):
    with env[1].session() as session: return session.scalar(select(func.count()).select_from(model))

async def run(env,payload):
    app=app_for(env)
    app.state.queue.enqueue(payload)
    await app.state.processor.tick()
    return app

async def test_inquiry_correct_calendar_only(env):
    env[4].plan=GuestPlan(availability_requested=True,requested_check_in='2026-11-10',requested_check_out='2026-11-14',manager_required=False,priority='normal',reason=None)
    await run(env,payload_for(env,status='inquiry',text='Disponible du 10 au 14 novembre ?'))
    assert env[3].calendar_calls==[('listing-a','2026-11-10','2026-11-14')]
    assert not env[3].send_calls

async def test_confirmed_arrival_no_calendar(env):
    await run(env,payload_for(env,text="J'arrive à 18h"))
    assert env[3].calendar_calls==[] and count(env,SentMessage)==1

async def test_duplicate_webhook_single_response(env):
    app=app_for(env);payload=payload_for(env)
    assert app.state.queue.enqueue(payload,'delivery-1')['status']=='pending'
    assert app.state.queue.enqueue(payload,'delivery-2')['status']=='duplicate'
    await asyncio.gather(app.state.processor.tick(),app.state.processor.tick())
    assert count(env,SentMessage)==1

async def test_debounce_three_messages_one_reply(env):
    app=app_for(env)
    for i in range(3): app.state.queue.enqueue(payload_for(env,text='Question '+str(i),suffix=str(i)))
    await app.state.processor.tick()
    assert count(env,SentMessage)==1
    assert len(env[4].calls[0][1]['incoming_messages'])==3

async def test_debounce_waits_last_message(env):
    app=app_for(env)
    app.state.queue.enqueue(payload_for(env))
    with env[1].session() as session:
        event=session.scalar(select(Event));event.due_at=time.time()+100;session.commit()
    await app.state.processor.tick()
    assert count(env,SentMessage)==0

async def test_early_checkin_escalation(env):
    env[4].plan.manager_required=True;env[4].plan.reason='Early check-in exceptionnel'
    env[4].reply.text='Je regarde avec mon manager et reviens vers vous :)'
    await run(env,payload_for(env,text='Puis-je arriver à 12h ?'))
    assert count(env,Escalation)==1 and count(env,SentMessage)==1

async def test_refund_escalation(env):
    env[4].plan.manager_required=True;env[4].plan.reason='Remboursement'
    env[4].reply.text='Je vérifie avec mon manager.'
    await run(env,payload_for(env,text='Je veux un remboursement'))
    assert count(env,Escalation)==1

async def test_ai_failure_replies_and_escalates(env):
    async def broken(*args): raise RuntimeError('PRIVATE-ERROR-TEXT')
    env[4].parse=broken
    await run(env,payload_for(env))
    assert count(env,SentMessage)==1 and count(env,Escalation)==1

async def test_unknown_property_neutral_reply(env):
    payload=payload_for(env);env[3].reservations[payload['reservationId']]['stay'][0]['unitId']='unknown'
    await run(env,payload)
    assert count(env,SentMessage)==1 and count(env,Escalation)==1
    assert env[4].calls==[]

async def test_withheld_secret_output_replaced(env):
    env[4].reply.text='Le code est TEST-BUILDING-CODE'
    await run(env,payload_for(env,status='inquiry'))
    with env[1].session() as session:
        text=env[2].decrypt(session.scalar(select(SentMessage)).encrypted_body)
    assert 'TEST-BUILDING-CODE' not in text
    assert count(env,Escalation)==1

async def test_other_property_secret_output_replaced(env):
    env[4].reply.text='Votre wifi est TEST-PASSWORD-unit-b'
    await run(env,payload_for(env))
    with env[1].session() as session:
        assert 'TEST-PASSWORD-unit-b' not in env[2].decrypt(session.scalar(select(SentMessage)).encrypted_body)

async def test_logs_never_contain_reply_secret(env,caplog):
    env[4].reply.text='TEST-BUILDING-CODE'
    await run(env,payload_for(env))
    assert 'TEST-BUILDING-CODE' not in caplog.text

async def test_destination_uncertain_alert_only(env):
    payload=payload_for(env);payload['conversation']['_id']='wrong'
    await run(env,payload)
    assert count(env,Escalation)==1 and count(env,SentMessage)==0

async def test_channel_missing_alert_no_send(env):
    payload=payload_for(env);payload['message']['module']='note'
    await run(env,payload)
    assert count(env,SentMessage)==0 and count(env,Escalation)==1

async def test_historical_message_quarantined(env):
    from datetime import datetime,timedelta,timezone
    payload=payload_for(env,now=datetime.now(timezone.utc)-timedelta(days=10))
    app=app_for(env)
    assert app.state.queue.enqueue(payload)['status']=='quarantined'
    await app.state.processor.tick()
    assert count(env,SentMessage)==0

async def test_confirmed_same_dates_no_calendar(env):
    payload=payload_for(env)
    stay=env[3].reservations[payload['reservationId']]['stay'][0]
    env[4].plan.availability_requested=True
    env[4].plan.requested_check_in=stay['checkInDateLocalized'];env[4].plan.requested_check_out=stay['checkOutDateLocalized']
    await run(env,payload)
    assert env[3].calendar_calls==[]

async def test_retry_after_schedules_without_loop(env):
    from app.guesty.errors import GuestyError
    app=app_for(env);payload=payload_for(env);app.state.queue.enqueue(payload)
    deadline=time.time()+500
    async def limited(*args): raise GuestyError('reservation',429,deadline)
    env[3].reservation=limited
    await app.state.processor.tick()
    with env[1].session() as session:
        event=session.scalar(select(Event));assert event.due_at>=deadline and event.attempts==1
    await app.state.processor.tick()
    with env[1].session() as session: assert session.scalar(select(Event)).attempts==1

async def test_financial_promise_blocked_even_if_ai_misses_intent(env):
    env[4].reply.text='Oui, je vous rembourse entièrement.'
    await run(env,payload_for(env,text='Je veux un remboursement'))
    with env[1].session() as session:
        assert 'rembourse entierement' not in env[2].decrypt(session.scalar(select(SentMessage)).encrypted_body)
    assert count(env,Escalation)==1

async def test_smoke_safety_and_urgent_escalation(env):
    env[4].reply.text='Ouvrez le tableau et touchez les fils.'
    await run(env,payload_for(env,text='Il y a de la fumée et des étincelles'))
    with env[1].session() as session:
        escalation=session.scalar(select(Escalation))
        text=env[2].decrypt(session.scalar(select(SentMessage)).encrypted_body)
    assert escalation.priority=='critical' and 'services d’urgence' in text
    assert 'touchez les fils' not in text

async def test_dangerous_model_troubleshooting_blocked(env):
    env[4].reply.text='Démontez le ballon.'
    await run(env,payload_for(env,text="Plus d'eau chaude"))
    with env[1].session() as session:
        assert 'Démontez' not in env[2].decrypt(session.scalar(select(SentMessage)).encrypted_body)
    assert count(env,Escalation)==1

async def test_danger_takes_priority_over_refund(env):
    await run(env,payload_for(env,text='Il y a de la fumée, je demande un remboursement'))
    with env[1].session() as session: assert session.scalar(select(Escalation)).priority=='critical'

async def test_messages_arriving_during_generation_regroup_before_send(env):
    app=app_for(env);first=payload_for(env,text='Premier',suffix='first');app.state.queue.enqueue(first)
    original=env[4].parse;arrived=False
    async def parse(schema,prompt,data):
        nonlocal arrived
        if schema is GuestReply and not arrived:
            arrived=True
            app.state.queue.enqueue(payload_for(env,text='Deuxième',suffix='second'))
        return await original(schema,prompt,data)
    env[4].parse=parse
    await app.state.processor.tick()
    assert count(env,SentMessage)==0
    await app.state.processor.tick()
    assert count(env,SentMessage)==1

async def test_early_arrival_promise_blocked_when_plan_misses_exception(env):
    env[4].reply.text='Oui, vous pouvez arriver à 12h.'
    await run(env,payload_for(env,text='Puis-je arriver à 12h ?'))
    with env[1].session() as session:
        assert 'vous pouvez arriver' not in env[2].decrypt(session.scalar(select(SentMessage)).encrypted_body)
    assert count(env,Escalation)==1

async def test_unknown_calendar_cannot_be_claimed_available(env):
    env[4].plan.availability_requested=True
    env[4].plan.requested_check_in='2026-11-10';env[4].plan.requested_check_out='2026-11-14'
    env[3].calendar_data={'data':{'days':[]}}
    env[4].reply.text='Oui, ces dates sont disponibles.'
    await run(env,payload_for(env,status='inquiry',text='Disponible du 10 au 14 novembre ?'))
    with env[1].session() as session:
        assert 'sont disponibles' not in env[2].decrypt(session.scalar(select(SentMessage)).encrypted_body)
    assert count(env,Escalation)==1

async def test_incomplete_dates_asks_without_manager(env):
    env[4].plan.availability_requested=True
    env[4].reply.text='C’est disponible.'
    await run(env,payload_for(env,status='inquiry',text='Disponible en novembre ?'))
    with env[1].session() as session:
        assert 'dates exactes' in env[2].decrypt(session.scalar(select(SentMessage)).encrypted_body)
    assert count(env,Escalation)==0 and env[3].calendar_calls==[]
