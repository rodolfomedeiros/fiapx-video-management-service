"""Testes do endpoint de métricas."""
import io

from tests.conftest import insert_video
from app.models import VideoStatus


def test_metrics_expoe_formato_do_prometheus(client):
    resposta = client.get("/metrics")

    assert resposta.status_code == 200
    assert "text/plain" in resposta.headers["content-type"]
    assert "fiapx_video_uploads_total" in resposta.text


def test_upload_bem_sucedido_incrementa_o_contador(client, published, uploaded):
    antes = _valor(client, "fiapx_video_uploads_total")

    client.post("/api/v1/videos/upload", files={"file": ("a.mp4", io.BytesIO(b"\x00" * 2048), "video/mp4")})

    assert _valor(client, "fiapx_video_uploads_total") == antes + 1


def test_upload_recusado_nao_incrementa_o_contador(client, published, uploaded):
    antes = _valor(client, "fiapx_video_uploads_total")

    client.post("/api/v1/videos/upload", files={"file": ("a.exe", io.BytesIO(b"\x00"), "application/x-msdownload")})

    assert _valor(client, "fiapx_video_uploads_total") == antes


def test_requisicoes_sao_medidas_por_rota_declarada(client):
    insert_video(status=VideoStatus.RECEIVED)

    client.get("/api/v1/videos")
    corpo = client.get("/metrics").text

    assert 'endpoint="/api/v1/videos"' in corpo
    assert 'method="GET"' in corpo


def test_metrica_de_rota_nao_carrega_o_id_do_video(client, uploaded):
    video = insert_video(status=VideoStatus.PROCESSING)

    client.get(f"/api/v1/videos/{video.id}/download")
    corpo = client.get("/metrics").text

    assert str(video.id) not in corpo, "o id no rótulo faria a cardinalidade crescer sem limite"
    assert 'endpoint="/api/v1/videos/{video_id}/download"' in corpo


def _valor(client, nome):
    for linha in client.get("/metrics").text.splitlines():
        if linha.startswith(nome + " "):
            return float(linha.split()[1])
    return 0.0
