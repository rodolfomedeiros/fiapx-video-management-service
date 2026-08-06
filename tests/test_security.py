"""Testes da introspecção do token no auth-service e do seu cache."""
import httpx
import pytest
from fastapi import HTTPException

from app import cache, security

CACHE_REAL = (cache.get_claims, cache.put_claims)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def cache_vazio(monkeypatch):
    """Por padrão os testes rodam sem cache; quem precisa dele substitui o armazenamento."""
    guardado: dict[str, dict] = {}

    async def get_claims(digest):
        return guardado.get(digest)

    async def put_claims(digest, claims):
        guardado[digest] = claims

    monkeypatch.setattr(cache, "get_claims", get_claims)
    monkeypatch.setattr(cache, "put_claims", put_claims)
    return guardado


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
async def test_recusa_requisicao_sem_header():
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


@pytest.mark.asyncio
async def test_segunda_chamada_com_o_mesmo_token_nao_bate_no_auth_service(monkeypatch):
    calls = fake_client(monkeypatch, FakeResponse(200, {"active": True, "sub": "abc"}))

    primeiro = await security.authenticate("Bearer valido")
    segundo = await security.authenticate("Bearer valido")

    assert primeiro == segundo
    assert len(calls) == 1, "a introspecção deveria ter vindo do cache"


@pytest.mark.asyncio
async def test_tokens_diferentes_nao_compartilham_entrada_de_cache(monkeypatch):
    calls = fake_client(monkeypatch, FakeResponse(200, {"active": True, "sub": "abc"}))

    await security.authenticate("Bearer um")
    await security.authenticate("Bearer outro")

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_token_em_claro_nunca_vira_chave_no_redis(monkeypatch, cache_vazio):
    fake_client(monkeypatch, FakeResponse(200, {"active": True, "sub": "abc"}))

    await security.authenticate("Bearer segredo-do-usuario")

    assert all("segredo-do-usuario" not in chave for chave in cache_vazio)


@pytest.mark.asyncio
async def test_resposta_negativa_nao_e_cacheada(monkeypatch, cache_vazio):
    fake_client(monkeypatch, FakeResponse(200, {"active": False}))

    with pytest.raises(HTTPException):
        await security.authenticate("Bearer expirado")

    assert cache_vazio == {}


@pytest.mark.asyncio
async def test_redis_fora_do_ar_nao_impede_a_autenticacao(monkeypatch):
    def explode():
        raise ConnectionError("redis fora")

    # Restaura o cache real para exercitar o tratamento de falha dentro dele.
    monkeypatch.setattr(cache, "get_claims", CACHE_REAL[0])
    monkeypatch.setattr(cache, "put_claims", CACHE_REAL[1])
    monkeypatch.setattr(cache, "client", explode)
    calls = fake_client(monkeypatch, FakeResponse(200, {"active": True, "sub": "abc"}))

    user = await security.authenticate("Bearer valido")

    assert user["sub"] == "abc"
    assert len(calls) == 1
