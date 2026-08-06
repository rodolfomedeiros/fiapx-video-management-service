"""API de upload, consulta e atualização em tempo real de vídeos."""
import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import aio_pika
import boto3
import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect, status
from sqlalchemy import DateTime, Enum as SAEnum, String, Text, func, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://fiapx:fiapx@localhost:5432/fiapx")
AUTH_INTROSPECTION_URL = os.getenv("AUTH_INTROSPECTION_URL", "http://localhost:8081/api/v1/auth/introspect")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://fiapx:fiapx@localhost:5672/%2F")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
S3_PUBLIC_ENDPOINT = os.getenv("S3_PUBLIC_ENDPOINT_URL", S3_ENDPOINT_URL)
S3_BUCKET = os.getenv("S3_BUCKET", "videos")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm"}

engine = create_async_engine(DATABASE_URL)
sessions = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase): pass
class VideoStatus(str, Enum): RECEIVED="RECEIVED"; PROCESSING="PROCESSING"; COMPLETED="COMPLETED"; ERROR="ERROR"
class Video(Base):
    __tablename__ = "videos"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_file_path: Mapped[str] = mapped_column(Text, nullable=False)
    zip_file_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[VideoStatus] = mapped_column(SAEnum(VideoStatus, name="video_status", create_type=False), default=VideoStatus.RECEIVED)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Hub:
    def __init__(self): self.connections: dict[str, set[WebSocket]] = {}
    async def connect(self, user_id: str, ws: WebSocket): await ws.accept(); self.connections.setdefault(user_id, set()).add(ws)
    def disconnect(self, user_id: str, ws: WebSocket): self.connections.get(user_id, set()).discard(ws)
    async def broadcast(self, user_id: str, payload: dict[str, Any]):
        for ws in list(self.connections.get(user_id, set())):
            try: await ws.send_json(payload)
            except Exception: self.disconnect(user_id, ws)
hub = Hub()

def event(event_type: str, video: Video, **extra: Any) -> dict[str, Any]:
    return {"event_id":str(uuid.uuid4()), "event_type":event_type, "occurred_at":datetime.now(timezone.utc).isoformat(), "video_id":str(video.id), "user_id":str(video.user_id), "attempt":0, **extra}

async def user_from_token(authorization: str | None) -> dict[str, Any]:
    if not authorization: raise HTTPException(status_code=401, detail="Bearer token obrigatório")
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(AUTH_INTROSPECTION_URL, headers={"Authorization": authorization})
    data = response.json()
    if not data.get("active"): raise HTTPException(status_code=401, detail="Token inválido ou expirado")
    return data

async def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]: return await user_from_token(authorization)
async def db_session():
    async with sessions() as session: yield session
def s3_client(): return boto3.client("s3", endpoint_url=S3_ENDPOINT_URL, aws_access_key_id=os.getenv("S3_ACCESS_KEY", "fiapx"), aws_secret_access_key=os.getenv("S3_SECRET_KEY", "fiapx-minio-password"), region_name="us-east-1")
async def publish(message: dict[str, Any], routing_key: str):
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    async with connection:
        channel = await connection.channel(); exchange = await channel.declare_exchange("video.events", aio_pika.ExchangeType.TOPIC, durable=True)
        await exchange.publish(aio_pika.Message(body=json.dumps(message).encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT), routing_key=routing_key)

async def consume_statuses():
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL)
            break
        except Exception:
            await asyncio.sleep(3)
    channel = await connection.channel(); exchange = await channel.declare_exchange("video.events", aio_pika.ExchangeType.TOPIC, durable=True); queue = await channel.declare_queue("video-status-queue", durable=True); await queue.bind(exchange, "video.status.changed")
    async def handler(message: aio_pika.IncomingMessage):
        async with message.process():
            data = json.loads(message.body)
            print(f"received status event for {data['video_id']}: {data.get('status')}", flush=True)
            async with sessions() as session:
                video = await session.get(Video, uuid.UUID(data["video_id"]))
                if not video: return
                video.status = VideoStatus(data["status"])
                video.zip_file_path = data.get("zip_file_path") or video.zip_file_path
                video.error_message = data.get("error_message")
                await session.commit()
                print(f"persisted video {video.id} as {video.status.value}", flush=True)
                await hub.broadcast(str(video.user_id), {"video_id":str(video.id), "status":video.status.value, "zip_file_path":video.zip_file_path, "error_message":video.error_message})
    await queue.consume(handler)
    return connection

@asynccontextmanager
async def lifespan(_: FastAPI):
    connection = await consume_statuses()
    yield
    await connection.close(); await engine.dispose()
app = FastAPI(title="FIAP X Video Management Service", version="0.1.0", lifespan=lifespan)

@app.post("/api/v1/videos/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_video(file: UploadFile = File(...), user: dict[str, Any] = Depends(current_user), db: AsyncSession = Depends(db_session)):
    extension = os.path.splitext(file.filename or "")[1].lower()
    if extension not in ALLOWED_EXTENSIONS: raise HTTPException(400, "Formato de arquivo não suportado")
    contents = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES: raise HTTPException(413, "Arquivo excede o limite de 500 MB")
    video_id = uuid.uuid4(); key = f"raw/{user['sub']}/{video_id}{extension}"
    await asyncio.to_thread(s3_client().put_object, Bucket=S3_BUCKET, Key=key, Body=contents, ContentType=file.content_type or "application/octet-stream")
    video = Video(id=video_id, user_id=uuid.UUID(user["sub"]), original_name=file.filename or "video", raw_file_path=key)
    db.add(video); await db.commit(); await db.refresh(video)
    await publish(event("video.received", video, raw_file_path=key, user_email=user["email"]), "video.received")
    return {"id":str(video.id), "status":video.status.value}

@app.get("/api/v1/videos")
async def list_videos(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), status_filter: VideoStatus | None = Query(None, alias="status"), user: dict[str, Any] = Depends(current_user), db: AsyncSession = Depends(db_session)):
    query = select(Video).where(Video.user_id == uuid.UUID(user["sub"])).order_by(Video.created_at.desc())
    if status_filter: query=query.where(Video.status == status_filter)
    videos=(await db.scalars(query.offset((page-1)*size).limit(size))).all()
    return {"items":[serialize(v) for v in videos], "page":page, "size":size}

@app.get("/api/v1/videos/{video_id}/download")
async def download(video_id: uuid.UUID, user: dict[str, Any] = Depends(current_user), db: AsyncSession = Depends(db_session)):
    video=await db.get(Video, video_id)
    if not video or video.user_id != uuid.UUID(user["sub"]): raise HTTPException(404, "Vídeo não encontrado")
    if video.status != VideoStatus.COMPLETED or not video.zip_file_path: raise HTTPException(409, "Processamento ainda não concluído")
    url=await asyncio.to_thread(s3_client().generate_presigned_url, "get_object", Params={"Bucket":S3_BUCKET,"Key":video.zip_file_path}, ExpiresIn=3600)
    return {"url":url.replace(S3_ENDPOINT_URL, S3_PUBLIC_ENDPOINT), "expires_in":3600}

@app.websocket("/api/v1/videos/ws")
async def updates(ws: WebSocket):
    try:
        user=await user_from_token(ws.headers.get("authorization") or (f"Bearer {ws.query_params.get('token')}" if ws.query_params.get("token") else None)); await hub.connect(user["sub"],ws)
        while True: await ws.receive_text()
    except (HTTPException, WebSocketDisconnect): pass
    finally:
        if "user" in locals(): hub.disconnect(user["sub"],ws)

def serialize(video: Video) -> dict[str, Any]: return {"id":str(video.id),"original_name":video.original_name,"status":video.status.value,"zip_file_path":video.zip_file_path,"error_message":video.error_message,"created_at":video.created_at}
