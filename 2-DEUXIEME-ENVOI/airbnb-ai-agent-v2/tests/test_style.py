import pytest
from sqlalchemy import select
from app.core.security import fingerprint
from app.db.models import SentMessage,StyleProfile
from app.schemas.actions import StyleTraits
from app.services.training import DEFAULTS
from app.services.style import StyleService

async def test_agent_and_automatic_posts_never_human_style(env):
    _,db,vault,_,ai=env
    with db.session() as session:
        session.add(SentMessage(batch_key='batch',conversation_id='cid',guesty_message_id='our-id',
            body_hash=fingerprint('Our own message'),encrypted_body=vault.encrypt('Our own message')))
        session.commit()
    posts=[{'_id':'our-id','body':'Our own message','from':{'type':'user'},'isAutomatic':False,'module':{'type':'airbnb2'}},
           {'_id':'auto','body':'Automatic reply','from':{'type':'user'},'isAutomatic':True,'module':{'type':'airbnb2'}},
           {'_id':'unknown','body':'Unknown provenance','from':{'type':'user'},'module':{'type':'airbnb2'}}]
    result=await StyleService(db,vault,ai).learn('cid',posts)
    assert result['learned']==0 and ai.calls==[]

async def test_human_style_secrets_redacted(env):
    _,db,vault,_,ai=env
    captured=[]
    async def parse(schema,prompt,data):
        captured.append(data)
        return StyleTraits(**{**DEFAULTS,"length":"short","smiley":True})
    ai.parse=parse
    post={'_id':'human','body':'Bonjour :) TEST-PASSWORD-unit-a TEST-BUILDING-CODE',
        'from':{'type':'user'},'isAutomatic':False,'module':{'type':'airbnb2'}}
    assert (await StyleService(db,vault,ai).learn('cid',[post],['human']))['learned']==1
    assert 'TEST-PASSWORD' not in str(captured) and 'TEST-BUILDING' not in str(captured)

async def test_absent_metadata_requires_manager_human_confirmation(env):
    _,db,vault,_,ai=env
    async def parse(*args): return StyleTraits(**DEFAULTS)
    ai.parse=parse
    post={'_id':'verified-by-manager','body':'Bonjour :)','from':{'type':'user'},'module':{'type':'airbnb2'}}
    svc=StyleService(db,vault,ai)
    assert (await svc.learn('cid',[post]))['learned']==0
    assert (await svc.learn('cid',[post],['verified-by-manager']))['learned']==1
