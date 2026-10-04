import time
from urllib.parse import urlparse,parse_qs
import httpx
import pytest
from sqlalchemy import select
from app.db.models import Property,PropertyAccess,MediaAsset,ManagerWorkspace
from app.services.google_drive import DriveService,DriveError,SCOPE
from app.services.property_media import PropertyMediaService
from app.services.media import MediaService
from app.services.onboarding import OnboardingService
from app.core.tenancy import organization_id,organization_scope
from test_site import site


def new_property(env):
    with env[1].session() as session:
        prop=Property(name='MEDIA-TEST',is_active=False,status='onboarding')
        session.add(prop);session.commit();return prop.id


def drive_service(env,handler):
    http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service=DriveService(env[1],env[2],env[0],http)
    service.write('provider:drive-config',{'client_id':'SYNTHETIC-GOOGLE-ID','client_secret':'SYNTHETIC-GOOGLE-SECRET'})
    return service,http


async def test_drive_authorization_binds_state_session_owner_and_rejects_replay(env):
    async def handler(request):
        assert request.url.host=='oauth2.googleapis.com'
        assert b'code_verifier=' in request.content
        return httpx.Response(200,json={'access_token':'SYNTHETIC-TOKEN','refresh_token':'SYNTHETIC-REFRESH','scope':SCOPE,'expires_in':3600})
    service,http=drive_service(env,handler)
    identity={'user_id':'owner-id'}
    url=service.start('http://127.0.0.1:8000/admin/drive/callback',identity,'session-secret')
    query=parse_qs(urlparse(url).query)
    assert query['scope']==[SCOPE] and query['code_challenge_method']==['S256']
    state=query['state'][0]
    with pytest.raises(DriveError):await service.finish('code',state,{'user_id':'foreign-owner'},'session-secret')
    with pytest.raises(DriveError):await service.finish('code',state,identity,'foreign-session')
    await service.finish('code',state,identity,'session-secret')
    assert service.summary()['connected']
    with pytest.raises(DriveError):await service.finish('code',state,identity,'session-secret')
    with env[1].session() as session:
        row=session.get(ManagerWorkspace,'provider:drive')
        assert 'SYNTHETIC-TOKEN' not in row.encrypted_data
    await http.aclose()


async def test_drive_upload_stores_actual_link_recovers_without_duplicates_and_shares_only_on_request(env):
    calls=[];files=[]
    async def handler(request):
        calls.append((request.method,request.url.path))
        path=request.url.path
        if request.method=='GET' and path=='/drive/v3/files':return httpx.Response(200,json={'files':files})
        if request.method=='POST' and path=='/drive/v3/files':return httpx.Response(200,json={'id':'synthetic-folder'})
        if request.method=='POST' and path=='/upload/drive/v3/files':
            return httpx.Response(200,headers={'Location':'https://www.googleapis.com/upload/drive/v3/files?upload_id=synthetic'})
        if request.method=='PUT':
            assert request.content==b'synthetic-video'
            files.append({'id':'synthetic-file','webViewLink':'https://drive.google.com/file/d/synthetic-file/view'})
            return httpx.Response(200,json=files[0])
        if request.method=='POST' and path.endswith('/permissions'):
            assert b'"anyone"' in request.content
            return httpx.Response(200,json={'id':'synthetic-permission'})
        if request.method=='GET':return httpx.Response(200,json=files[0])
        raise AssertionError(str(request.url))
    service,http=drive_service(env,handler)
    service.write('provider:drive',{'refresh_token':'synthetic','access_token':'synthetic','expires_at':time.time()+3600,'connection_id':'synthetic-connection'})
    pid=new_property(env)
    result=await service.upload('00000000-0000-0000-0000-000000000001',pid,'video.mp4','video/mp4',b'synthetic-video')
    assert result['drive_url']==files[0]['webViewLink'] and not result['shared_with_guests']
    assert not any(path.endswith('/permissions') for _,path in calls)
    result=await service.upload('00000000-0000-0000-0000-000000000001',pid,'video.mp4','video/mp4',b'synthetic-video',True)
    assert result['shared_with_guests']
    assert sum(method=='PUT' for method,_ in calls)==1
    await http.aclose()


def test_media_yes_does_not_mark_complete_and_no_records_absence(env):
    pid=new_property(env);service=PropertyMediaService(env[1],env[2],None,None)
    answer=service.answer(pid,'access_video',True)
    assert answer['status']=='upload_required'
    with env[1].session() as session:
        prop=session.get(Property,pid)
        progress=OnboardingService(env[2]).progress(session,prop)
        assert 'access_video' in progress['missing'] and progress['media_requested']['access_video']
    service.answer(pid,'access_video',False)
    with env[1].session() as session:
        prop=session.get(Property,pid)
        assert 'access_video' not in OnboardingService(env[2]).progress(session,prop)['missing']
        assert env[2].decrypt(session.get(PropertyAccess,pid).encrypted_data)['access_video'] is False


async def test_explicit_property_upload_assigns_file_and_only_shared_link_enters_access_context(env):
    pid=new_property(env)
    class Drive:
        shared=False
        def summary(self):return {'connected':True}
        async def upload(self,*args,**kwargs):
            return {'drive_url':'https://drive.google.com/file/d/synthetic/view','shared_with_guests':args[-1]}
    service=PropertyMediaService(env[1],env[2],MediaService(env[1],env[2],env[0]),Drive())
    result=await service.upload(pid,'photo.png',b'\x89PNG\r\n\x1a\nsynthetic','access_photo',False)
    with env[1].session() as session:
        assert session.get(MediaAsset,result['id']).property_id==pid
        assert not session.get(PropertyAccess,pid)
    result=await service.publish(result['id'],True)
    with env[1].session() as session:
        values=env[2].decrypt(session.get(PropertyAccess,pid).encrypted_data)
        assert values['access_photos']==[result['drive_url']]


def test_upload_requires_confirmation_rejects_wrong_type_and_foreign_ids(env):
    pid=new_property(env);c,headers=site(env)
    file={'file':('photo.png',b'\x89PNG\r\n\x1a\nsynthetic','image/png')}
    assert c.post('/admin/properties/'+pid+'/media',headers=headers,data={'kind':'access_photo','confirm':'false'},files=file).status_code==422
    assert c.post('/admin/properties/'+pid+'/media',headers=headers,data={'kind':'access_video','confirm':'true'},files=file).status_code==422
    assert c.post('/admin/properties/foreign-id/media',headers=headers,data={'kind':'access_photo','confirm':'true'},files=file).status_code==422
    result=c.post('/admin/properties/'+pid+'/media',headers=headers,data={'kind':'access_photo','confirm':'true'},files=file)
    assert result.status_code==200 and result.json()['drive_status']=='not_connected'


def test_drive_connection_isolated_and_manager_cannot_change_it(env):
    c,headers=site(env)
    assert c.post('/admin/settings/drive/configuration',headers=headers,json={'client_id':'synthetic-id','client_secret':'synthetic-secret'}).status_code==200
    own=c.get('/admin/session').json()
    from app.db.models import Organization,Membership
    with env[1].system_session() as session:
        other=Organization(name='Other');session.add(other);session.commit();oid=other.id
        member=session.get(Membership,(own['organization_id'],own['user_id']));member.role='manager';session.commit()
    assert c.post('/admin/settings/drive/connect',headers=headers).status_code==403
    with organization_scope(oid):
        service=DriveService(env[1],env[2],env[0])
        assert not service.summary()['connected'] and not service.summary()['configured']


async def test_drive_failure_retains_assigned_local_file_for_retry(env):
    pid=new_property(env)
    class Drive:
        def summary(self):return {'connected':True}
        async def upload(self,*args):raise DriveError('Temporary Google failure')
    service=PropertyMediaService(env[1],env[2],MediaService(env[1],env[2],env[0]),Drive())
    result=await service.upload(pid,'photo.png',b'\x89PNG\r\n\x1a\nsynthetic','access_photo')
    assert result['drive_status']=='retry'
    with env[1].session() as session:
        asset=session.get(MediaAsset,result['id']);assert asset.status=='assigned' and asset.property_id==pid
