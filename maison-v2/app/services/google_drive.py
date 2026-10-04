"""Workspace-specific Google authorization and media uploads; drive.file scope only."""
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlparse
import httpx
from app.core.security import fingerprint
from app.db.models import ManagerWorkspace, AuditLog, Property

SCOPE = 'https://www.googleapis.com/auth/drive.file'
API = 'https://www.googleapis.com/drive/v3'

class DriveError(ValueError):
    pass

class DriveService:
    def __init__(self, db, vault, settings, http=None):
        self.db,self.vault,self.settings,self.http=db,vault,settings,http

    def read(self, key):
        with self.db.session() as session:
            row=session.get(ManagerWorkspace,key)
            return self.vault.decrypt(row.encrypted_data) if row else {}

    def write(self, key, value):
        with self.db.session() as session:
            row=session.get(ManagerWorkspace,key)
            if row:row.encrypted_data,row.updated_at=self.vault.encrypt(value),time.time()
            else:session.add(ManagerWorkspace(id=key,encrypted_data=self.vault.encrypt(value)))
            session.commit()

    def configuration(self):
        value=self.read('provider:drive-config')
        return {'client_id':value.get('client_id') or self.settings.google_client_id,
                'client_secret':value.get('client_secret') or self.settings.google_client_secret}

    def summary(self):
        cfg=self.configuration();tokens=self.read('provider:drive')
        return {'configured':bool(cfg['client_id'] and cfg['client_secret']),
                'connected':bool(tokens.get('refresh_token')),
                'connected_at':tokens.get('connected_at')}

    async def request(self, method, url, **kwargs):
        try:
            if self.http:
                response=await self.http.request(method,url,**kwargs)
            else:
                async with httpx.AsyncClient(timeout=90,follow_redirects=False) as http:
                    response=await http.request(method,url,**kwargs)
        except httpx.HTTPError:
            raise DriveError('Google Drive est indisponible. Votre fichier local reste conservé.') from None
        if not 200 <= response.status_code < 300:
            raise DriveError(f'Google Drive a refusé la demande (HTTP {response.status_code}). Vérifiez la connexion et les autorisations.')
        return response

    def start(self, redirect_uri, identity, session_token):
        cfg=self.configuration()
        if not cfg['client_id'] or not cfg['client_secret']:
            raise DriveError('Configurez d’abord l’application Google, puis cliquez sur Connecter Google Drive.')
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        self.write('oauth:'+fingerprint(state)[:32],{'user_id':identity['user_id'],
            'session':fingerprint(session_token),'expires_at':time.time()+600,'verifier':verifier,
            'redirect_uri':redirect_uri,'client_id':cfg['client_id']})
        return 'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({
            'client_id':cfg['client_id'],'redirect_uri':redirect_uri,'response_type':'code',
            'scope':SCOPE,'access_type':'offline','prompt':'consent','state':state,
            'code_challenge':challenge,'code_challenge_method':'S256'})

    async def finish(self, code, state, identity, session_token):
        async with self.db.lock('drive_authorization'):
            key='oauth:'+fingerprint(state)[:32]
            value=self.read(key);cfg=self.configuration()
            if (not value or value['expires_at'] < time.time() or value['user_id']!=identity['user_id']
                    or value['session']!=fingerprint(session_token) or value['client_id']!=cfg['client_id']):
                raise DriveError('Connexion Google expirée ou invalide. Recommencez depuis votre espace.')
            # Consume before exchange: a code cannot be replayed by concurrent callbacks.
            self.write(key,{})
            response=await self.request('POST','https://oauth2.googleapis.com/token',data={
                'code':code,'client_id':cfg['client_id'],'client_secret':cfg['client_secret'],
                'redirect_uri':value['redirect_uri'],'grant_type':'authorization_code','code_verifier':value['verifier']})
            tokens=response.json()
            if not tokens.get('refresh_token') or not tokens.get('access_token') or SCOPE not in tokens.get('scope','').split():
                raise DriveError('Google n’a pas accordé l’accès requis. Reconnectez Drive et autorisez les fichiers de l’application.')
            self.write('provider:drive',{'access_token':tokens['access_token'],'refresh_token':tokens['refresh_token'],
                'expires_at':time.time()+int(tokens.get('expires_in',3600)), 'connected_at':time.time(), 'connection_id':secrets.token_hex(16)})
            with self.db.session() as session:
                session.add(AuditLog(source='settings:drive',encrypted_change=self.vault.encrypt({'connected':True,'scope':'drive.file'})))
                session.commit()

    async def token(self):
        async with self.db.lock('drive_tokens'):
            value=self.read('provider:drive')
            if not value.get('refresh_token'):
                raise DriveError('Connectez Google Drive avant de créer un lien Drive.')
            if value.get('expires_at',0)>time.time()+60:return value['access_token']
            cfg=self.configuration()
            response=await self.request('POST','https://oauth2.googleapis.com/token',data={
                'client_id':cfg['client_id'],'client_secret':cfg['client_secret'],
                'refresh_token':value['refresh_token'],'grant_type':'refresh_token'})
            data=response.json()
            if not data.get('access_token'):raise DriveError('Token Google invalide. Reconnectez Drive.')
            value.update(access_token=data['access_token'],expires_at=time.time()+int(data.get('expires_in',3600)))
            self.write('provider:drive',value)
            return value['access_token']

    async def folder(self, prop_id, token):
        async with self.db.lock('drive_folders'):
            return await self._folder(prop_id, token)

    async def _folder(self, prop_id, token):
        key='df:'+prop_id.replace('-','');saved=self.read(key)
        version=self.read('provider:drive').get('connection_id')
        if saved.get('id') and saved.get('connection_id')==version:return saved['id']
        from app.core.tenancy import organization_id
        root=self.read('drive:root')
        if root.get('connection_id')!=version:root={}
        headers={'Authorization':'Bearer '+token}
        if not root.get('id'):
            response=await self.request('POST',API+'/files',headers=headers,
                params={'fields':'id'},json={'name':'Maison · '+organization_id(),
                    'mimeType':'application/vnd.google-apps.folder'})
            root={**response.json(),'connection_id':version};self.write('drive:root',root)
        with self.db.session() as session:
            prop=session.get(Property,prop_id)
            if not prop:raise DriveError('Logement introuvable.')
            name=prop.name
        response=await self.request('POST',API+'/files',headers=headers,params={'fields':'id'},
            json={'name':name,'mimeType':'application/vnd.google-apps.folder','parents':[root['id']]})
        result={**response.json(),'connection_id':version};self.write(key,result)
        return result['id']

    async def upload(self, asset_id, property_id, name, content_type, data, share_guest=False):
        key='dm:'+asset_id.replace('-','')
        async with self.db.lock('drive_upload:'+asset_id):
            token=await self.token();headers={'Authorization':'Bearer '+token}
            saved=self.read(key)
            version=self.read('provider:drive').get('connection_id')
            if saved.get('connection_id')!=version:saved={}
            if not saved.get('id'):
                from app.core.tenancy import organization_id
                org=organization_id()
                # Recover a file created before a local crash, without duplicating uploads.
                response=await self.request('GET',API+'/files',headers=headers,params={
                    'q':"trashed = false and appProperties has { key='maison_media_id' and value='"+asset_id+"' } and appProperties has { key='maison_org_id' and value='"+org+"' }",
                    'fields':'files(id,webViewLink)','pageSize':2})
                files=response.json().get('files',[])
                if len(files)>1:raise DriveError('Plusieurs fichiers Drive correspondent à ce média. Vérification nécessaire.')
                if files:saved=files[0]
                else:
                    folder=await self.folder(property_id,token)
                    response=await self.request('POST','https://www.googleapis.com/upload/drive/v3/files',headers={
                        **headers,'X-Upload-Content-Type':content_type,'X-Upload-Content-Length':str(len(data))},
                        params={'uploadType':'resumable','fields':'id,webViewLink'},
                        json={'name':name,'parents':[folder], 'appProperties':{'maison_media_id':asset_id,'maison_org_id':org}})
                    location=response.headers.get('Location','');parsed=urlparse(location)
                    if parsed.scheme!='https' or parsed.netloc!='www.googleapis.com':raise DriveError('URL d’upload Google inattendue.')
                    response=await self.request('PUT',location,headers={**headers,'Content-Type':content_type,'Content-Length':str(len(data))},content=data)
                    saved=response.json()
                if not saved.get('id'):raise DriveError('Google n’a pas confirmé le fichier.')
                saved.update(shared=False,connection_id=version)
                self.write(key,saved)
            if share_guest and not saved.get('shared'):
                await self.request('POST',API+'/files/'+saved['id']+'/permissions',headers=headers,
                    json={'type':'anyone','role':'reader','allowFileDiscovery':False})
                saved['shared']=True
            response=await self.request('GET',API+'/files/'+saved['id'],headers=headers,params={'fields':'id,webViewLink'})
            metadata=response.json();link=metadata.get('webViewLink','');parsed=urlparse(link)
            if parsed.scheme!='https' or parsed.netloc!='drive.google.com':raise DriveError('Google n’a pas fourni de lien Drive valide.')
            saved.update(url=link,shared=bool(saved.get('shared')))
            self.write(key,saved)
            return {'drive_url':link,'shared_with_guests':saved['shared']}
