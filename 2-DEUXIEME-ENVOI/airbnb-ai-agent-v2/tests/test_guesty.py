import asyncio
import time
import httpx
import pytest
from app.db.models import OAuthToken
from app.guesty.auth import TokenService
from app.guesty.client import GuestyClient
from app.guesty.errors import ContractError,GuestyError
from app.services.calendar import CalendarService

async def test_valid_token_no_oauth_request(env):
    settings,db,vault,*_=env
    settings.guesty_client_id='test-client'
    calls=[]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r))) as http:
        service=TokenService(settings,db,vault,http)
        with db.session() as session:
            session.add(OAuthToken(id=service.key,encrypted_token=vault.encrypt('TEST-TOKEN'),expires_at=time.time()+3000));session.commit()
        assert await service.get()=='TEST-TOKEN' and calls==[]
        restarted=TokenService(settings,db,vault,http)
        assert await restarted.get()=='TEST-TOKEN' and calls==[]

async def test_simultaneous_refresh_only_one_token(env):
    settings,db,vault,*_=env
    settings.guesty_client_id='test-client';settings.guesty_client_secret='TEST-SECRET'
    calls=[]
    async def handler(request):
        calls.append(request);await asyncio.sleep(.01)
        return httpx.Response(200,json={'access_token':'TEST-TOKEN','expires_in':86400})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        one=TokenService(settings,db,vault,http);two=TokenService(settings,db,vault,http)
        assert await asyncio.gather(one.get(),two.get())==['TEST-TOKEN','TEST-TOKEN']
    assert len(calls)==1

async def test_oauth_429_persists_retry_after(env):
    settings,db,vault,*_=env
    settings.guesty_client_id='test-client';settings.guesty_client_secret='TEST-SECRET'
    calls=[]
    def handler(request):
        calls.append(request);return httpx.Response(429,headers={'Retry-After':'120'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service=TokenService(settings,db,vault,http)
        with pytest.raises(GuestyError) as caught: await service.get()
        assert caught.value.retry_at>time.time()+110
        with pytest.raises(GuestyError): await TokenService(settings,db,vault,http).get()
    assert len(calls)==1

async def test_401_refresh_at_most_once(env):
    class Tokens:
        calls=0
        async def get(self,rejected=None): self.calls+=1;return 'TEST-'+str(self.calls)
    tokens=Tokens();calls=[]
    def handler(request):calls.append(request);return httpx.Response(401)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=GuestyClient(tokens,http,env[0])
        with pytest.raises(GuestyError):await client.request('GET','/listings')
    assert len(calls)==2 and tokens.calls==2

async def test_posts_documented_envelope_and_pagination(env):
    class Tokens:
        async def get(self,**kwargs):return 'TEST'
    calls=[]
    def handler(request):
        calls.append(request)
        index=len(calls)
        return httpx.Response(200,json={'data':{'posts':[{'_id':str(index),'conversationId':'cid','createdAt':str(index)}],
            'cursor':{'after':'next' if index==1 else ''}}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        posts=await GuestyClient(Tokens(),http,env[0]).posts('cid')
    assert len(posts)==2 and calls[1].url.params['cursorAfter']=='next'

async def test_reservation_v3_exact_id(env):
    class Tokens:
        async def get(self,**kwargs):return 'TEST'
    def handler(request):
        assert request.url.params['reservationIds[]']=='rid'
        return httpx.Response(200,json=[{'_id':'other'},{'_id':'rid','stay':[]}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result=await GuestyClient(Tokens(),http,env[0]).reservation('rid')
    assert result['_id']=='rid'

async def test_test_mode_client_cannot_send(env):
    client=GuestyClient(None,None,env[0])
    with pytest.raises(RuntimeError):await client.send('cid','body',{'type':'airbnb2'})

async def test_calendar_wrong_listing_unknown(env):
    guesty=env[3];guesty.calendar_data={'days':[{'listingId':'other','date':'2026-11-10','status':'available'}]}
    assert await CalendarService(guesty).availability('listing-a','2026-11-10','2026-11-14')=='unknown'

async def test_calendar_missing_day_unknown(env):
    guesty=env[3];guesty.calendar_data={'days':[]}
    assert await CalendarService(guesty).availability('listing-a','2026-11-10','2026-11-14')=='unknown'

async def test_calendar_allotment_overrides_status(env):
    guesty=env[3]
    guesty.calendar_data={'days':[{'listingId':'listing-a','date':f'2026-11-{i}','status':'available','allotment':0} for i in range(10,15)]}
    assert await CalendarService(guesty).availability('listing-a','2026-11-10','2026-11-14')=='unavailable'

async def test_calendar_checkout_not_occupied_night(env):
    guesty=env[3]
    guesty.calendar_data=await guesty.calendar('listing-a','2026-11-10','2026-11-14')
    guesty.calendar_data['data']['days'][-1]['status']='booked'
    guesty.calendar_data['data']['days'][-1]['allotment']=0
    assert await CalendarService(guesty).availability('listing-a','2026-11-10','2026-11-14')=='available'

async def test_api_429_account_cooldown_persistent(env):
    settings,db,vault,*_=env
    class Tokens:
        async def get(self,**kwargs):return 'TEST'
    tokens=Tokens();tokens.db=db;calls=[]
    def handler(request):
        calls.append(request);return httpx.Response(429,headers={'Retry-After':'200'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        one=GuestyClient(tokens,http,settings)
        with pytest.raises(GuestyError):await one.request('GET','/listings')
        two=GuestyClient(tokens,http,settings)
        with pytest.raises(GuestyError):await two.request('GET','/reservations-v3')
    assert len(calls)==1
