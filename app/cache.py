"""Cache de introspecção e distribuição de atualizações entre réplicas, sobre Redis.

Duas funções distintas, ambas opcionais: se o Redis estiver fora, a API continua
respondendo — a introspecção volta a bater no auth-service a cada requisição e o
WebSocket passa a alcançar apenas os sockets da própria réplica.
"""
import json
import logging
from typing import Any, Awaitable, Callable

import redis.asyncio as redis

from app import config

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(config.REDIS_URL, decode_responses=True)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def get_claims(token_digest: str) -> dict[str, Any] | None:
    """Claims já validadas para este token, ou None se não houver cache utilizável."""
    try:
        cached = await client().get(f"auth:token:{token_digest}")
    except Exception:
        logger.debug("Redis indisponível na leitura do cache de token", exc_info=True)
        return None
    if not cached:
        return None
    try:
        return json.loads(cached)
    except json.JSONDecodeError:
        return None


async def put_claims(token_digest: str, claims: dict[str, Any]) -> None:
    try:
        await client().set(f"auth:token:{token_digest}", json.dumps(claims), ex=config.TOKEN_CACHE_TTL_SECONDS)
    except Exception:
        logger.debug("Redis indisponível na escrita do cache de token", exc_info=True)


async def publish_update(user_id: str, payload: dict[str, Any]) -> bool:
    """Distribui a atualização a todas as réplicas. Devolve False se o Redis não aceitou."""
    try:
        await client().publish(config.UPDATES_CHANNEL, json.dumps({"user_id": user_id, "payload": payload}))
        return True
    except Exception:
        logger.warning("Redis indisponível, atualização ficará restrita a esta réplica", exc_info=True)
        return False


async def subscribe_updates(handler: Callable[[str, dict[str, Any]], Awaitable[None]]) -> None:
    """Escuta o canal de atualizações até ser cancelado."""
    pubsub = client().pubsub()
    await pubsub.subscribe(config.UPDATES_CHANNEL)
    async for message in pubsub.listen():
        if message.get("type") != "message":
            continue
        try:
            envelope = json.loads(message["data"])
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("atualização ilegível no canal %s", config.UPDATES_CHANNEL)
            continue
        await handler(envelope["user_id"], envelope["payload"])
