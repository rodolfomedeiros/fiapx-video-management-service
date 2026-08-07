"""Publicação e consumo de eventos no RabbitMQ."""
import asyncio
import json
import logging
from functools import partial
from typing import Any, Awaitable, Callable

import aio_pika

from app import config

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]


async def dispatch(message: aio_pika.abc.AbstractIncomingMessage, handler: Handler) -> None:
    """Decide o destino de uma mensagem de status.

    Evento ilegível vai direto para a DLQ: retentar não vai torná-lo válido. Falha do
    handler — Postgres fora do ar, por exemplo — é retentada uma única vez, porque
    insistir em uma mensagem já reentregue transformaria a fila em um laço quente.
    Esgotada a tentativa, a mensagem para em `video-status-dlq` em vez de sumir: sem
    isso, uma indisponibilidade momentânea do banco deixava o vídeo travado no status
    anterior para sempre.
    """
    try:
        payload = json.loads(message.body)
    except json.JSONDecodeError:
        logger.error("evento de status ilegível, mandando para a DLQ: %r", message.body[:200])
        await message.reject(requeue=False)
        return
    try:
        await handler(payload)
    except Exception:
        logger.exception("falha ao aplicar o evento de status, reentrega=%s", message.redelivered)
        await message.reject(requeue=not message.redelivered)
        return
    await message.ack()


class Broker:
    """Conexão robusta única com o RabbitMQ.

    A versão anterior abria uma conexão TCP e negociava um canal a cada mensagem
    publicada, o que derrubava o broker por exaustão de file descriptors sob
    rajadas de upload. Aqui a conexão é aberta uma vez e reaproveitada.
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url or config.RABBITMQ_URL
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None
        self._lock = asyncio.Lock()

    async def connect(self, attempts: int = 20, delay: float = 3.0) -> None:
        """Conecta e declara o exchange, tolerando o RabbitMQ ainda subindo."""
        async with self._lock:
            if self._exchange is not None:
                return
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    self._connection = await aio_pika.connect_robust(self._url)
                    self._channel = await self._connection.channel(publisher_confirms=True)
                    self._exchange = await self._channel.declare_exchange(
                        config.EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                    )
                    return
                except Exception as error:  # broker indisponível durante o boot do Compose
                    last_error = error
                    logger.warning("RabbitMQ indisponível (tentativa %d/%d): %s", attempt, attempts, error)
                    await asyncio.sleep(delay)
            raise RuntimeError("não foi possível conectar ao RabbitMQ") from last_error

    async def publish(self, routing_key: str, payload: dict[str, Any]) -> None:
        if self._exchange is None:
            await self.connect()
        assert self._exchange is not None
        await self._exchange.publish(
            aio_pika.Message(
                body=json.dumps(payload).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=routing_key,
        )

    async def consume_status_changes(self, handler: Handler) -> None:
        """Assina `video.status.changed` na fila declarada em rabbitmq/definitions.json."""
        if self._connection is None:
            await self.connect()
        assert self._connection is not None
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=16)
        exchange = await channel.declare_exchange(config.EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        queue = await channel.declare_queue(
            config.STATUS_QUEUE, durable=True, arguments=config.STATUS_QUEUE_ARGUMENTS
        )
        await queue.bind(exchange, config.STATUS_ROUTING_KEY)
        await queue.consume(partial(dispatch, handler=handler))

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
        self._connection = self._channel = self._exchange = None
