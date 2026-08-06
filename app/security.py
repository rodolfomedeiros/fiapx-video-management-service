"""Validação do Bearer token por introspecção no auth-service, com cache no Redis."""
import hashlib
from typing import Any

import httpx
from fastapi import Header, HTTPException

from app import cache, config

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    """Cliente HTTP reaproveitado entre requisições, para não abrir uma conexão por chamada."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=5)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def authenticate(authorization: str | None) -> dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Bearer token obrigatório")
    # O token nunca vai para o Redis: a chave é o digest, para que ler o cache não
    # entregue credenciais utilizáveis.
    digest = hashlib.sha256(authorization.encode()).hexdigest()
    cached = await cache.get_claims(digest)
    if cached is not None:
        return cached
    try:
        response = await client().post(config.AUTH_INTROSPECTION_URL, headers={"Authorization": authorization})
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail="Serviço de autenticação indisponível") from error
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")
    data = response.json()
    if not data.get("active") or not data.get("sub"):
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")
    await cache.put_claims(digest, data)
    return data


async def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return await authenticate(authorization)
