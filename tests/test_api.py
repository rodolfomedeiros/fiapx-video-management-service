"""Testes dos endpoints HTTP."""
import io
import uuid

from app.models import VideoStatus
from tests.conftest import OTHER_USER_ID, USER_ID, insert_video


def video_file(name="ferias.mp4", size=1024, content_type="video/mp4"):
    return {"file": (name, io.BytesIO(b"\x00" * size), content_type)}


def test_health_responde_sem_autenticacao(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_upload_persiste_video_envia_ao_storage_e_publica_evento(client, published, uploaded):
    response = client.post("/api/v1/videos/upload", files=video_file())

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "RECEIVED"

    key, content, content_type = uploaded[0]
    assert key == f"raw/{USER_ID}/{body['id']}.mp4"
    assert len(content) == 1024
    assert content_type == "video/mp4"

    routing_key, payload = published[0]
    assert routing_key == "video.received"
    assert payload["video_id"] == body["id"]
    assert payload["user_id"] == str(USER_ID)
    assert payload["raw_file_path"] == key
    assert payload["user_email"] == "ana@example.com"
    assert payload["attempt"] == 0

    listed = client.get("/api/v1/videos").json()
    assert [item["id"] for item in listed["items"]] == [body["id"]]


def test_upload_recusa_extensao_nao_suportada(client, published, uploaded):
    response = client.post("/api/v1/videos/upload", files=video_file(name="malicioso.exe"))

    assert response.status_code == 400
    assert uploaded == [] and published == []


def test_upload_recusa_arquivo_acima_do_limite(client, published, uploaded, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 512)
    response = client.post("/api/v1/videos/upload", files=video_file(size=1024))

    assert response.status_code == 413
    assert uploaded == [] and published == []


def test_listagem_traz_apenas_videos_do_usuario_autenticado(client):
    meu = insert_video()
    insert_video(user_id=OTHER_USER_ID)

    body = client.get("/api/v1/videos").json()

    assert [item["id"] for item in body["items"]] == [str(meu.id)]
    assert body["total"] == 1


def test_listagem_filtra_por_status_e_informa_o_total(client):
    insert_video(status=VideoStatus.COMPLETED, zip_file_path="processed/a.zip")
    insert_video(status=VideoStatus.ERROR, error_message="ffmpeg falhou")
    insert_video(status=VideoStatus.RECEIVED)

    completos = client.get("/api/v1/videos", params={"status": "COMPLETED"}).json()
    assert completos["total"] == 1
    assert completos["items"][0]["zip_file_path"] == "processed/a.zip"

    com_erro = client.get("/api/v1/videos", params={"status": "ERROR"}).json()
    assert com_erro["items"][0]["error_message"] == "ffmpeg falhou"

    assert client.get("/api/v1/videos").json()["total"] == 3


def test_listagem_pagina_os_resultados(client):
    for _ in range(3):
        insert_video()

    primeira = client.get("/api/v1/videos", params={"page": 1, "size": 2}).json()
    segunda = client.get("/api/v1/videos", params={"page": 2, "size": 2}).json()

    assert len(primeira["items"]) == 2 and len(segunda["items"]) == 1
    assert primeira["total"] == segunda["total"] == 3
    assert not {item["id"] for item in primeira["items"]} & {item["id"] for item in segunda["items"]}


def test_listagem_recusa_status_invalido(client):
    assert client.get("/api/v1/videos", params={"status": "INVENTADO"}).status_code == 422


def test_download_devolve_url_assinada_quando_concluido(client, uploaded):
    video = insert_video(status=VideoStatus.COMPLETED, zip_file_path="processed/pronto.zip")

    body = client.get(f"/api/v1/videos/{video.id}/download").json()

    assert body["url"].startswith("http://localhost:9000/videos/processed/pronto.zip")
    assert body["expires_in"] == 3600


def test_download_bloqueia_video_ainda_em_processamento(client, uploaded):
    video = insert_video(status=VideoStatus.PROCESSING)

    assert client.get(f"/api/v1/videos/{video.id}/download").status_code == 409


def test_download_nao_vaza_video_de_outro_usuario(client, uploaded):
    alheio = insert_video(user_id=OTHER_USER_ID, status=VideoStatus.COMPLETED, zip_file_path="processed/alheio.zip")

    assert client.get(f"/api/v1/videos/{alheio.id}/download").status_code == 404


def test_download_de_video_inexistente_responde_404(client, uploaded):
    assert client.get(f"/api/v1/videos/{uuid.uuid4()}/download").status_code == 404
