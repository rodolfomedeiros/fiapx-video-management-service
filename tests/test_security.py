"""Testes da introspecção do token no auth-service."""
import httpx
import pytest
from fastapi import HTTPException

from app import security


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def fake_client(monkeypatch, response=None, error=None):
    calls = []

    class Client:
        async def post(self, url, headers=None):
            calls.append((url, headers))
            if error is not None:
                raise error
            return response

    monkeypatch.setattr(security, "client", lambda: Client())
    return calls


@pytest.mark.asyncio
async def test_aceita_token_ativo_e_devolve_as_claims(monkeypatch):
    calls = fake_client(monkeypatch, FakeResponse(200, {"active": True, "sub": "abc", "email": "ana@example.com"}))

    user = await security.authenticate("Bearer valido")

    assert user["sub"] == "abc"
    assert calls[0][1] == {"Authorization": "Bearer valido"}


@pytest.mark.asyncio
async def test_recusa_requisicao_sem_header(monkeypatch):
    with pytest.raises(HTTPException) as erro:
        await security.authenticate(None)
    assert erro.value.status_code == 401


@pytest.mark.asyncio
async def test_recusa_token_marcado_como_inativo(monkeypatch):
    fake_client(monkeypatch, FakeResponse(200, {"active": False}))
    with pytest.raises(HTTPException) as erro:
        await security.authenticate("Bearer expirado")
    assert erro.value.status_code == 401


@pytest.mark.asyncio
async def test_recusa_resposta_ativa_sem_subject(monkeypatch):
    fake_client(monkeypatch, FakeResponse(200, {"active": True}))
    with pytest.raises(HTTPException) as erro:
        await security.authenticate("Bearer estranho")
    assert erro.value.status_code == 401


@pytest.mark.asyncio
async def test_trata_erro_http_do_auth_service_como_401(monkeypatch):
    fake_client(monkeypatch, FakeResponse(500, None))
    with pytest.raises(HTTPException) as erro:
        await security.authenticate("Bearer qualquer")
    assert erro.value.status_code == 401


@pytest.mark.asyncio
async def test_sinaliza_indisponibilidade_do_auth_service(monkeypatch):
    fake_client(monkeypatch, error=httpx.ConnectError("recusou a conexão"))
    with pytest.raises(HTTPException) as erro:
        await security.authenticate("Bearer qualquer")
    assert erro.value.status_code == 503
