"""Testes do consumo de video.status.changed e reflexo no WebSocket."""
import uuid

import pytest

from app import cache, main
from app.models import VideoStatus
from tests.conftest import USER_ID, insert_video, session_maker


class FakeSocket:
    def __init__(self):
        self.recebidos = []

    async def accept(self):
        pass

    async def send_json(self, payload):
        self.recebidos.append(payload)


@pytest.fixture
def publicadas(monkeypatch):
    """Atualizações que teriam ido ao canal do Redis."""
    enviadas: list[tuple[str, dict]] = []

    async def fake_publish(user_id, payload):
        enviadas.append((user_id, payload))
        return True

    monkeypatch.setattr(cache, "publish_update", fake_publish)
    return enviadas


@pytest.fixture
def consumidor(monkeypatch, publicadas):
    monkeypatch.setattr(main, "session_factory", session_maker)
    main.hub.connections.clear()
    yield main.apply_status_change
    main.hub.connections.clear()


@pytest.fixture
def consumidor_sem_redis(monkeypatch):
    """Redis fora: a atualização deve cair no broadcast local."""

    async def fake_publish(user_id, payload):
        return False

    monkeypatch.setattr(cache, "publish_update", fake_publish)
    monkeypatch.setattr(main, "session_factory", session_maker)
    main.hub.connections.clear()
    yield main.apply_status_change
    main.hub.connections.clear()


async def _read(video_id):
    from sqlalchemy import text

    async with session_maker() as session:
        row = await session.execute(
            text("SELECT status, zip_file_path, error_message FROM videos WHERE id = :id"),
            {"id": video_id.hex},
        )
        return row.one()


@pytest.mark.asyncio
async def test_conclusao_grava_zip_e_avisa_o_dono_em_todas_as_replicas(consumidor, publicadas):
    video = insert_video(status=VideoStatus.PROCESSING)

    await consumidor(
        {"video_id": str(video.id), "status": "COMPLETED", "zip_file_path": "processed/ana/pronto.zip"}
    )

    status, zip_path, erro = await _read(video.id)
    assert (status, zip_path, erro) == ("COMPLETED", "processed/ana/pronto.zip", None)

    dono, atualizacao = publicadas[0]
    assert dono == str(USER_ID)
    assert atualizacao["status"] == "COMPLETED"
    assert atualizacao["zip_file_path"] == "processed/ana/pronto.zip"


@pytest.mark.asyncio
async def test_com_redis_fora_a_atualizacao_ainda_chega_aos_sockets_locais(consumidor_sem_redis):
    video = insert_video(status=VideoStatus.PROCESSING)
    socket = FakeSocket()
    await main.hub.connect(str(USER_ID), socket)

    await consumidor_sem_redis(
        {"video_id": str(video.id), "status": "COMPLETED", "zip_file_path": "processed/ana/pronto.zip"}
    )

    assert socket.recebidos[0]["status"] == "COMPLETED"
    assert socket.recebidos[0]["zip_file_path"] == "processed/ana/pronto.zip"


@pytest.mark.asyncio
async def test_falha_grava_a_mensagem_de_erro(consumidor):
    video = insert_video(status=VideoStatus.PROCESSING)

    await consumidor({"video_id": str(video.id), "status": "ERROR", "error_message": "ffmpeg falhou"})

    status, _, erro = await _read(video.id)
    assert (status, erro) == ("ERROR", "ffmpeg falhou")


@pytest.mark.asyncio
async def test_transicao_intermediaria_preserva_o_zip_ja_gravado(consumidor):
    video = insert_video(status=VideoStatus.COMPLETED, zip_file_path="processed/ana/antigo.zip")

    await consumidor({"video_id": str(video.id), "status": "PROCESSING"})

    status, zip_path, _ = await _read(video.id)
    assert (status, zip_path) == ("PROCESSING", "processed/ana/antigo.zip")


@pytest.mark.asyncio
async def test_ignora_evento_de_video_desconhecido(consumidor):
    await consumidor({"video_id": str(uuid.uuid4()), "status": "COMPLETED"})


@pytest.mark.asyncio
async def test_ignora_evento_com_status_fora_do_enum(consumidor):
    video = insert_video(status=VideoStatus.RECEIVED)

    await consumidor({"video_id": str(video.id), "status": "INVENTADO"})

    status, _, _ = await _read(video.id)
    assert status == "RECEIVED"


@pytest.mark.asyncio
async def test_ignora_evento_sem_video_id(consumidor):
    await consumidor({"status": "COMPLETED"})


@pytest.mark.asyncio
async def test_nao_falha_quando_o_dono_nao_tem_socket_aberto(consumidor):
    video = insert_video(status=VideoStatus.PROCESSING)

    await consumidor({"video_id": str(video.id), "status": "COMPLETED", "zip_file_path": "processed/x.zip"})

    status, _, _ = await _read(video.id)
    assert status == "COMPLETED"
