"""Registro das conexões WebSocket abertas, agrupadas por usuário."""
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class Hub:
    def __init__(self) -> None:
        self.connections: dict[str, set[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        remaining = self.connections.get(user_id)
        if remaining is None:
            return
        remaining.discard(websocket)
        if not remaining:
            self.connections.pop(user_id, None)

    async def broadcast(self, user_id: str, payload: dict[str, Any]) -> None:
        """Entrega a todos os sockets do usuário e descarta os que já morreram."""
        for websocket in list(self.connections.get(user_id, ())):
            try:
                await websocket.send_json(payload)
            except Exception:
                logger.debug("socket de %s indisponível, removendo do hub", user_id, exc_info=True)
                self.disconnect(user_id, websocket)
