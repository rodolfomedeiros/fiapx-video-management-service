"""Publicação e consumo de eventos no RabbitMQ."""
import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import aio_pika

from app import config

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]


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
        queue = await channel.declare_queue(config.STATUS_QUEUE, durable=True)
        await queue.bind(exchange, config.STATUS_ROUTING_KEY)

        async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            async with message.process(requeue=False):
                try:
                    payload = json.loads(message.body)
                except json.JSONDecodeError:
                    logger.error("evento de status ilegível, descartando: %r", message.body[:200])
                    return
                await handler(payload)

        await queue.consume(on_message)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
        self._connection = self._channel = self._exchange = None
