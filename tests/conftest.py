"""Infraestrutura dos testes: banco SQLite temporário e dependências substituídas."""
import os
import tempfile
import uuid
from pathlib import Path

TEST_DB = Path(tempfile.mkdtemp(prefix="fiapx-vms-")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app import main, security  # noqa: E402
from app.db import session  # noqa: E402
from app.models import Base, Video, VideoStatus  # noqa: E402

USER_ID = uuid.UUID("3f2504e0-4f89-11d3-9a0c-0305e82c3301")
OTHER_USER_ID = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
USER = {"active": True, "sub": str(USER_ID), "email": "ana@example.com", "name": "Ana"}

# NullPool porque o TestClient abre um event loop por requisição e uma conexão
# aiosqlite reaproveitada de outro loop quebra.
async_engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
session_maker = async_sessionmaker(async_engine, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
def schema():
    sync_engine = create_engine(f"sqlite:///{TEST_DB}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    yield
    TEST_DB.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def clean_tables(schema):
    sync_engine = create_engine(f"sqlite:///{TEST_DB}")
    with sync_engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM videos")
    sync_engine.dispose()


async def _session_override():
    async with session_maker() as active:
        yield active


@pytest.fixture
def published(monkeypatch):
    """Captura o que seria publicado no RabbitMQ."""
    messages: list[tuple[str, dict]] = []

    async def fake_publish(routing_key, payload):
        messages.append((routing_key, payload))

    monkeypatch.setattr(main.broker, "publish", fake_publish)
    return messages


@pytest.fixture
def uploaded(monkeypatch):
    """Captura o que seria enviado ao MinIO, sem tocar na rede."""
    objects: list[tuple[str, bytes, str]] = []

    async def fake_upload(fileobj, key, content_type):
        objects.append((key, fileobj.read(), content_type))

    async def fake_presigned_url(key, ttl_seconds):
        return f"http://localhost:9000/videos/{key}?expires={ttl_seconds}"

    monkeypatch.setattr(main.storage, "upload", fake_upload)
    monkeypatch.setattr(main.storage, "presigned_url", fake_presigned_url)
    return objects


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "session_factory", session_maker)

    async def fake_authenticate(authorization):
        return USER

    monkeypatch.setattr(security, "authenticate", fake_authenticate)
    main.app.dependency_overrides[session] = _session_override
    main.app.dependency_overrides[security.current_user] = lambda: USER
    # TestClient fora do `with` não executa o lifespan, então o broker real nunca conecta.
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def insert_video(**overrides) -> Video:
    """Grava um vídeo direto no banco, sem passar pela API."""
    video_id = overrides.pop("id", uuid.uuid4())
    fields = {
        "id": video_id,
        "user_id": USER_ID,
        "original_name": "ferias.mp4",
        "raw_file_path": f"raw/{USER_ID}/{video_id}.mp4",
        "zip_file_path": None,
        "status": VideoStatus.RECEIVED,
        "error_message": None,
        **overrides,
    }
    sync_engine = create_engine(f"sqlite:///{TEST_DB}")
    with sync_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO videos (id, user_id, original_name, raw_file_path, zip_file_path, status,"
            " error_message, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (
                fields["id"].hex,
                fields["user_id"].hex,
                fields["original_name"],
                fields["raw_file_path"],
                fields["zip_file_path"],
                fields["status"].value,
                fields["error_message"],
            ),
        )
    sync_engine.dispose()
    return Video(**fields)
