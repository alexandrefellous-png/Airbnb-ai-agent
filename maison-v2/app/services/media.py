import asyncio
import base64
import time
import uuid
from pathlib import PurePath
from sqlalchemy import select
from app.db.models import AuditLog, MediaAsset, MediaBlob, Property

KINDS = {"access_video", "access_photo", "arrival_guide", "document", "troubleshooting_media"}

class DatabaseStorage:
    name = "database"
    def __init__(self, db): self.db = db
    async def put(self, key, content):
        with self.db.session() as session:
            session.add(MediaBlob(object_key=key, encrypted_content=content)); session.commit()
    async def get(self, key):
        with self.db.session() as session:
            row=session.get(MediaBlob,key)
            if not row: raise ValueError("Média introuvable.")
            return row.encrypted_content
    async def remove(self, key):
        with self.db.session() as session:
            row=session.get(MediaBlob,key)
            if row:session.delete(row);session.commit()

class S3Storage:
    name = "s3"
    def __init__(self, settings):
        import boto3
        self.bucket = settings.media_s3_bucket
        self.client = boto3.client("s3", region_name=settings.media_s3_region, endpoint_url=settings.media_s3_endpoint or None)
    async def put(self,key,content):
        await asyncio.to_thread(self.client.put_object,Bucket=self.bucket,Key=key,Body=content.encode(),ContentType="application/octet-stream")
    async def get(self,key):
        def read():
            response=self.client.get_object(Bucket=self.bucket,Key=key)
            try:return response["Body"].read().decode()
            finally:response["Body"].close()
        return await asyncio.to_thread(read)
    async def remove(self,key):
        await asyncio.to_thread(self.client.delete_object,Bucket=self.bucket,Key=key)

def detect_type(data):
    if data.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if data.startswith(b"RIFF") and data[8:12]==b"WEBP":return "image/webp"
    if data.startswith(b"%PDF-"):return "application/pdf"
    if len(data)>12 and data[4:8]==b"ftyp":return "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):return "video/webm"
    raise ValueError("Format non autorisé. Utilisez JPEG, PNG, WebP, MP4, WebM ou PDF.")

class MediaService:
    def __init__(self, db, vault, settings, storage=None):
        self.db,self.vault,self.settings=db,vault,settings
        self.storage=storage or (S3Storage(settings) if settings.media_storage=="s3" else DatabaseStorage(db))

    async def upload(self,name,data,kind,property_id=None):
        if kind not in KINDS:raise ValueError("Type de média invalide.")
        if not data or len(data)>self.settings.media_max_mb*1024*1024:
            raise ValueError("Fichier vide ou trop volumineux.")
        content_type=detect_type(data)
        if kind=="access_video" and not content_type.startswith("video/"):
            raise ValueError("Une vidéo est requise.")
        if kind=="access_photo" and not content_type.startswith("image/"):
            raise ValueError("Une image est requise.")
        with self.db.session() as session:
            if property_id and not session.get(Property,property_id):raise ValueError("Logement introuvable.")
        from app.core.tenancy import organization_id
        key="manager-media/"+organization_id()+"/"+str(uuid.uuid4())
        encrypted=self.vault.encrypt(base64.b64encode(data).decode())
        await self.storage.put(key,encrypted)
        try:
            with self.db.session() as session:
                # Upload remains unassigned until a property-specific confirmation, even from a property page.
                row=MediaAsset(kind=kind,encrypted_name=self.vault.encrypt(PurePath(name.replace("\\","/")).name[:250]),
                    content_type=content_type,size=len(data),storage=self.storage.name,object_key=key)
                session.add(row);session.flush()
                session.add(AuditLog(source="media:upload",encrypted_change=self.vault.encrypt({"media_id":row.id,"kind":kind,"size":len(data)})))
                session.commit()
                return {"id":row.id,"name":self.vault.decrypt(row.encrypted_name),"kind":kind,"size":len(data),"status":"unassigned"}
        except Exception:
            await self.storage.remove(key)
            raise

    def list_unassigned(self):
        with self.db.session() as session:
            return [{"id":m.id,"name":self.vault.decrypt(m.encrypted_name),"kind":m.kind,"size":m.size}
                for m in session.scalars(select(MediaAsset).where(MediaAsset.status=="unassigned").order_by(MediaAsset.created_at.desc()).limit(50))]

    def assign(self,session,prop,action):
        asset=session.get(MediaAsset,action.media_id)
        if not asset or asset.status!="unassigned":raise ValueError("Ce média est introuvable ou déjà affecté.")
        if action.media_kind and action.media_kind!=asset.kind:raise ValueError("Le type de média ne correspond pas au fichier.")
        asset.property_id,asset.status=prop.id,"assigned"

    async def download(self,asset_id):
        with self.db.session() as session:
            row=session.get(MediaAsset,asset_id)
            if not row:raise ValueError("Média introuvable.")
            key,storage=row.object_key,row.storage
            metadata={"name":self.vault.decrypt(row.encrypted_name),"type":row.content_type,"property_id":row.property_id,"kind":row.kind}
        backend=DatabaseStorage(self.db) if storage=="database" else self.storage
        if backend.name!=storage:raise ValueError("Ancien stockage média non configuré.")
        return base64.b64decode(self.vault.decrypt(await backend.get(key))),metadata
