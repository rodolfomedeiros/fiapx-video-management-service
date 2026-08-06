"""Montagem dos eventos publicados no barramento.

O formato é o de `fiapx-platform/contracts/video-event.schema.json`, compartilhado
com o worker Rust e o notification-service.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from app import config


def build(event_type: str, *, video_id: Any, user_id: Any, attempt: int = 0, **extra: Any) -> dict[str, Any]:
    """Devolve o envelope do evento; campos opcionais nulos são omitidos."""
    payload: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "video_id": str(video_id),
        "user_id": str(user_id),
        "attempt": attempt,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def video_received(video: Any, *, user_email: str | None = None) -> dict[str, Any]:
    return build(
        config.RECEIVED_ROUTING_KEY,
        video_id=video.id,
        user_id=video.user_id,
        raw_file_path=video.raw_file_path,
        status=video.status.value,
        user_email=user_email,
    )
