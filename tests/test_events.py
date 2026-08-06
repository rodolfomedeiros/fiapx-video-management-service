"""Testes do envelope de eventos publicado no barramento."""
import json
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app import events
from app.models import Video, VideoStatus

REPO_CONTRACT = Path(__file__).parents[1] / "contracts" / "video-event.schema.json"
PLATFORM_CONTRACT = Path(__file__).parents[2] / "fiapx-platform" / "contracts" / "video-event.schema.json"


@pytest.fixture(scope="module")
def validator():
    return Draft202012Validator(json.loads(REPO_CONTRACT.read_text()))


@pytest.fixture
def video():
    return Video(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        original_name="ferias.mp4",
        raw_file_path="raw/ana/ferias.mp4",
        status=VideoStatus.RECEIVED,
    )


def test_evento_de_video_recebido_satisfaz_o_contrato(validator, video):
    payload = events.video_received(video, user_email="ana@example.com")

    validator.validate(payload)
    assert payload["event_type"] == "video.received"
    assert payload["video_id"] == str(video.id)
    assert payload["raw_file_path"] == "raw/ana/ferias.mp4"
    assert payload["user_email"] == "ana@example.com"


def test_omite_campos_opcionais_nulos_em_vez_de_enviar_null(validator, video):
    payload = events.video_received(video, user_email=None)

    validator.validate(payload)
    assert "user_email" not in payload


def test_cada_evento_recebe_um_identificador_proprio(video):
    primeiro = events.video_received(video)
    segundo = events.video_received(video)

    assert primeiro["event_id"] != segundo["event_id"]
    uuid.UUID(primeiro["event_id"])


def test_occurred_at_e_uma_data_iso_com_fuso(video):
    momento = datetime.fromisoformat(events.video_received(video)["occurred_at"])

    assert momento.tzinfo is not None


def test_contrato_vendorizado_acompanha_o_da_plataforma():
    if not PLATFORM_CONTRACT.exists():
        pytest.skip("fiapx-platform não está presente neste checkout")
    assert json.loads(REPO_CONTRACT.read_text()) == json.loads(PLATFORM_CONTRACT.read_text())
