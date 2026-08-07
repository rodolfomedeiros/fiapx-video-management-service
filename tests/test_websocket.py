"""Testes do endpoint de acompanhamento em tempo real."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main, security
from tests.conftest import USER, USER_ID


@pytest.fixture
def socket_client(monkeypatch):
    """TestClient com a introspecção substituída e o hub limpo."""

    async def fake_authenticate(authorization):
        if authorization in ("Bearer valido", "Bearer do-header"):
            return USER
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    monkeypatch.setattr(security, "authenticate", fake_authenticate)
    main.hub.connections.clear()
    yield TestClient(main.app)
    main.hub.connections.clear()


def test_aceita_o_token_pela_query_string(socket_client):
    # O navegador não deixa definir cabeçalhos na abertura de um WebSocket, então o token
    # precisa poder viajar na query string.
    with socket_client.websocket_connect("/api/v1/videos/ws?token=valido"):
        assert str(USER_ID) in main.hub.connections


def test_aceita_o_token_pelo_cabecalho(socket_client):
    with socket_client.websocket_connect(
        "/api/v1/videos/ws", headers={"authorization": "Bearer do-header"}
    ):
        assert str(USER_ID) in main.hub.connections


def test_recusa_conexao_sem_token(socket_client):
    with pytest.raises(Exception):
        with socket_client.websocket_connect("/api/v1/videos/ws"):
            pass

    assert main.hub.connections == {}


def test_recusa_conexao_com_token_invalido(socket_client):
    with pytest.raises(Exception):
        with socket_client.websocket_connect("/api/v1/videos/ws?token=falsificado"):
            pass

    assert main.hub.connections == {}


def test_desconectar_libera_o_registro_do_usuario(socket_client):
    with socket_client.websocket_connect("/api/v1/videos/ws?token=valido"):
        assert main.hub.connections[str(USER_ID)]

    assert main.hub.connections.get(str(USER_ID), set()) == set()
