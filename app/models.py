"""Mapeamento das tabelas criadas por fiapx-platform/postgres/init.sql."""
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import DateTime, Enum as SAEnum, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class VideoStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_file_path: Mapped[str] = mapped_column(Text, nullable=False)
    zip_file_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[VideoStatus] = mapped_column(SAEnum(VideoStatus, name="video_status"), default=VideoStatus.RECEIVED)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def serialize(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "original_name": self.original_name,
            "status": self.status.value,
            "zip_file_path": self.zip_file_path,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
