"""API de upload, consulta e acompanhamento em tempo real de vídeos."""
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config, events, security, storage
from app.db import engine, session, session_factory
from app.hub import Hub
from app.messaging import Broker
from app.models import Video, VideoStatus

logger = logging.getLogger(__name__)

hub = Hub()
broker = Broker()


async def apply_status_change(payload: dict[str, Any]) -> None:
    """Persiste a transição publicada pelo worker e reflete no WebSocket do dono do vídeo."""
    video_id, new_status = payload.get("video_id"), payload.get("status")
    if not video_id or new_status not in VideoStatus.__members__:
        logger.warning("evento de status ignorado: video_id=%r status=%r", video_id, new_status)
        return
    async with session_factory() as active:
        video = await active.get(Video, uuid.UUID(video_id))
        if video is None:
            logger.warning("evento de status para vídeo desconhecido %s", video_id)
            return
        video.status = VideoStatus(new_status)
        video.zip_file_path = payload.get("zip_file_path") or video.zip_file_path
        video.error_message = payload.get("error_message")
        await active.commit()
        logger.info("vídeo %s agora está %s", video.id, video.status.value)
        await hub.broadcast(str(video.user_id), video.serialize())


@asynccontextmanager
async def lifespan(_: FastAPI):
    await broker.connect()
    await broker.consume_status_changes(apply_status_change)
    yield
    await broker.close()
    await security.close()
    await engine.dispose()


app = FastAPI(title="FIAP X Video Management Service", version="1.0.0", lifespan=lifespan)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _upload_size(file: UploadFile) -> int:
    """Mede o arquivo já recebido sem carregá-lo na memória."""
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    return size


@app.post("/api/v1/videos/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_video(
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(security.current_user),
    db: AsyncSession = Depends(session),
) -> dict[str, str]:
    extension = os.path.splitext(file.filename or "")[1].lower()
    if extension not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato de arquivo não suportado")
    if _upload_size(file) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Arquivo excede o limite permitido")

    video_id = uuid.uuid4()
    key = f"raw/{user['sub']}/{video_id}{extension}"
    await storage.upload(file.file, key, file.content_type or "application/octet-stream")

    video = Video(
        id=video_id,
        user_id=uuid.UUID(user["sub"]),
        original_name=file.filename or "video",
        raw_file_path=key,
        status=VideoStatus.RECEIVED,
    )
    db.add(video)
    await db.commit()
    await broker.publish(config.RECEIVED_ROUTING_KEY, events.video_received(video, user_email=user.get("email")))
    return {"id": str(video.id), "status": video.status.value}


@app.get("/api/v1/videos")
async def list_videos(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status_filter: VideoStatus | None = Query(None, alias="status"),
    user: dict[str, Any] = Depends(security.current_user),
    db: AsyncSession = Depends(session),
) -> dict[str, Any]:
    owned = Video.user_id == uuid.UUID(user["sub"])
    filters = [owned] if status_filter is None else [owned, Video.status == status_filter]
    total = await db.scalar(select(func.count()).select_from(Video).where(*filters))
    query = select(Video).where(*filters).order_by(Video.created_at.desc()).offset((page - 1) * size).limit(size)
    videos = (await db.scalars(query)).all()
    return {"items": [video.serialize() for video in videos], "page": page, "size": size, "total": total or 0}


@app.get("/api/v1/videos/{video_id}/download")
async def download(
    video_id: uuid.UUID,
    user: dict[str, Any] = Depends(security.current_user),
    db: AsyncSession = Depends(session),
) -> dict[str, Any]:
    video = await db.get(Video, video_id)
    if video is None or video.user_id != uuid.UUID(user["sub"]):
        raise HTTPException(status_code=404, detail="Vídeo não encontrado")
    if video.status != VideoStatus.COMPLETED or not video.zip_file_path:
        raise HTTPException(status_code=409, detail="Processamento ainda não concluído")
    url = await storage.presigned_url(video.zip_file_path, config.PRESIGNED_URL_TTL_SECONDS)
    return {"url": url, "expires_in": config.PRESIGNED_URL_TTL_SECONDS}


@app.websocket("/api/v1/videos/ws")
async def updates(websocket: WebSocket) -> None:
    header = websocket.headers.get("authorization")
    token = websocket.query_params.get("token")
    try:
        user = await security.authenticate(header or (f"Bearer {token}" if token else None))
    except HTTPException:
        await websocket.close(code=1008)
        return
    await hub.connect(user["sub"], websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(user["sub"], websocket)
