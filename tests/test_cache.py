"""Testes do cache no Redis e da distribuição de atualizações entre réplicas."""
import json

import pytest

from app import cache, config


class FakeRedis:
    def __init__(self, indisponivel=False):
        self.armazenado: dict[str, str] = {}
        self.expiracoes: dict[str, int] = {}
        self.publicados: list[tuple[str, str]] = []
        self.indisponivel = indisponivel

    def _checa(self):
        if self.indisponivel:
            raise ConnectionError("redis fora do ar")

    async def get(self, key):
        self._checa()
        return self.armazenado.get(key)

    async def set(self, key, value, ex=None):
        self._checa()
        self.armazenado[key] = value
        self.expiracoes[key] = ex

    async def publish(self, channel, message):
        self._checa()
        self.publicados.append((channel, message))


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache, "client", lambda: fake)
    return fake


@pytest.fixture
def redis_fora(monkeypatch):
    fake = FakeRedis(indisponivel=True)
    monkeypatch.setattr(cache, "client", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_claims_gravadas_sao_lidas_de_volta(redis):
    await cache.put_claims("digest", {"active": True, "sub": "abc"})

    assert await cache.get_claims("digest") == {"active": True, "sub": "abc"}


@pytest.mark.asyncio
async def test_entrada_de_token_expira(redis):
    await cache.put_claims("digest", {"sub": "abc"})

    assert redis.expiracoes["auth:token:digest"] == config.TOKEN_CACHE_TTL_SECONDS


@pytest.mark.asyncio
async def test_digest_desconhecido_devolve_none(redis):
    assert await cache.get_claims("nunca-visto") is None


@pytest.mark.asyncio
async def test_conteudo_corrompido_e_tratado_como_ausencia(redis):
    redis.armazenado["auth:token:digest"] = "isto não é json"

    assert await cache.get_claims("digest") is None


@pytest.mark.asyncio
async def test_leitura_e_escrita_sobrevivem_ao_redis_fora_do_ar(redis_fora):
    await cache.put_claims("digest", {"sub": "abc"})

    assert await cache.get_claims("digest") is None


@pytest.mark.asyncio
async def test_atualizacao_vai_para_o_canal_com_dono_e_conteudo(redis):
    enviado = await cache.publish_update("ana", {"id": "video-1", "status": "COMPLETED"})

    assert enviado is True
    canal, mensagem = redis.publicados[0]
    assert canal == config.UPDATES_CHANNEL
    assert json.loads(mensagem) == {"user_id": "ana", "payload": {"id": "video-1", "status": "COMPLETED"}}


@pytest.mark.asyncio
async def test_publicacao_avisa_quando_o_redis_recusa(redis_fora):
    assert await cache.publish_update("ana", {"status": "COMPLETED"}) is False


class FakePubSub:
    def __init__(self, mensagens):
        self.mensagens = mensagens
        self.inscrito = None

    async def subscribe(self, channel):
        self.inscrito = channel

    async def listen(self):
        for mensagem in self.mensagens:
            yield mensagem


@pytest.mark.asyncio
async def test_assinatura_entrega_apenas_mensagens_bem_formadas(monkeypatch):
    mensagens = [
        {"type": "subscribe", "data": 1},
        {"type": "message", "data": json.dumps({"user_id": "ana", "payload": {"status": "COMPLETED"}})},
        {"type": "message", "data": "isto não é json"},
        {"type": "message", "data": json.dumps({"user_id": "bruno", "payload": {"status": "ERROR"}})},
    ]
    pubsub = FakePubSub(mensagens)
    monkeypatch.setattr(cache, "client", lambda: type("C", (), {"pubsub": lambda _self: pubsub})())

    entregues = []

    async def handler(user_id, payload):
        entregues.append((user_id, payload))

    await cache.subscribe_updates(handler)

    assert pubsub.inscrito == config.UPDATES_CHANNEL
    assert entregues == [("ana", {"status": "COMPLETED"}), ("bruno", {"status": "ERROR"})]
