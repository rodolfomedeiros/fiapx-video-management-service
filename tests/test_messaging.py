"""Testes da publicação de eventos e do destino dado a cada mensagem da fila de status."""
import json

import aio_pika
import pytest

from app import config
from app.messaging import Broker, dispatch


class FakeMessage:
    """Mensagem do broker reduzida ao que `dispatch` usa."""

    def __init__(self, body: bytes, redelivered: bool = False):
        self.body = body
        self.redelivered = redelivered
        self.confirmada = False
        self.rejeitada: bool | None = None  # requeue escolhido; None se nunca rejeitada

    async def ack(self):
        self.confirmada = True

    async def reject(self, requeue: bool):
        self.rejeitada = requeue


def evento(**campos):
    return json.dumps({"video_id": "abc", "status": "COMPLETED", **campos}).encode()


async def aplica(payload):
    pass


async def banco_fora_do_ar(payload):
    raise RuntimeError("connection refused")


@pytest.mark.asyncio
async def test_evento_aplicado_e_confirmado():
    mensagem = FakeMessage(evento())

    await dispatch(mensagem, aplica)

    assert (mensagem.confirmada, mensagem.rejeitada) == (True, None)


@pytest.mark.asyncio
async def test_evento_ilegivel_vai_direto_para_a_dlq():
    mensagem = FakeMessage(b"{ isto nao e json")

    await dispatch(mensagem, aplica)

    assert (mensagem.confirmada, mensagem.rejeitada) == (False, False)


@pytest.mark.asyncio
async def test_falha_do_handler_devolve_a_mensagem_para_uma_nova_tentativa():
    mensagem = FakeMessage(evento(), redelivered=False)

    await dispatch(mensagem, banco_fora_do_ar)

    assert mensagem.rejeitada is True


@pytest.mark.asyncio
async def test_falha_na_reentrega_para_na_dlq_em_vez_de_sumir():
    mensagem = FakeMessage(evento(), redelivered=True)

    await dispatch(mensagem, banco_fora_do_ar)

    assert (mensagem.confirmada, mensagem.rejeitada) == (False, False)


class FakeExchange:
    def __init__(self):
        self.publicadas = []

    async def publish(self, message, routing_key):
        self.publicadas.append((routing_key, message))


class FakeChannel:
    def __init__(self, exchange):
        self._exchange = exchange
        self.publisher_confirms = None

    async def declare_exchange(self, name, tipo, durable):
        self.declarado = (name, tipo, durable)
        return self._exchange


class FakeConnection:
    def __init__(self, channel):
        self._channel = channel
        self.fechada = False

    async def channel(self, publisher_confirms=False):
        self._channel.publisher_confirms = publisher_confirms
        return self._channel

    async def close(self):
        self.fechada = True


@pytest.fixture
def broker_conectado(monkeypatch):
    """Broker com o aio_pika substituído, sem tocar na rede."""
    exchange = FakeExchange()
    channel = FakeChannel(exchange)
    connection = FakeConnection(channel)

    async def fake_connect_robust(url):
        return connection

    monkeypatch.setattr(aio_pika, "connect_robust", fake_connect_robust)
    return Broker("amqp://irrelevante"), exchange, channel, connection


@pytest.mark.asyncio
async def test_declara_o_exchange_duravel_com_confirmacao_do_publisher(broker_conectado):
    broker, _, channel, _ = broker_conectado

    await broker.connect()

    assert channel.publisher_confirms is True
    assert channel.declarado == (config.EXCHANGE, aio_pika.ExchangeType.TOPIC, True)


@pytest.mark.asyncio
async def test_publica_mensagem_persistente_com_a_chave_de_roteamento(broker_conectado):
    broker, exchange, _, _ = broker_conectado

    await broker.publish(config.RECEIVED_ROUTING_KEY, {"video_id": "abc"})

    routing_key, message = exchange.publicadas[0]
    assert routing_key == config.RECEIVED_ROUTING_KEY
    assert json.loads(message.body) == {"video_id": "abc"}
    assert message.content_type == "application/json"
    # Sem PERSISTENT, um restart do broker apagaria os uploads ainda na fila.
    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT


@pytest.mark.asyncio
async def test_publicar_sem_conectar_antes_abre_a_conexao(broker_conectado):
    broker, exchange, _, _ = broker_conectado

    await broker.publish(config.RECEIVED_ROUTING_KEY, {"video_id": "abc"})

    assert len(exchange.publicadas) == 1


@pytest.mark.asyncio
async def test_conectar_duas_vezes_reaproveita_a_mesma_conexao(broker_conectado):
    broker, _, channel, _ = broker_conectado

    await broker.connect()
    channel.declarado = None
    await broker.connect()

    assert channel.declarado is None, "a segunda chamada não deve redeclarar nada"


@pytest.mark.asyncio
async def test_desiste_depois_de_esgotar_as_tentativas(monkeypatch):
    async def sempre_falha(url):
        raise ConnectionError("recusada")

    monkeypatch.setattr(aio_pika, "connect_robust", sempre_falha)

    with pytest.raises(RuntimeError, match="não foi possível conectar"):
        await Broker("amqp://irrelevante").connect(attempts=2, delay=0)


@pytest.mark.asyncio
async def test_fechar_libera_a_conexao(broker_conectado):
    broker, _, _, connection = broker_conectado

    await broker.connect()
    await broker.close()

    assert connection.fechada
