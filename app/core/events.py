from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket


def utc_now() -> datetime:
    return datetime.now(UTC)


class EventBroker:
    """In-process WS fan-out for a single API worker. Use Redis/NATS for multi-worker deployment."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def publish(self, event: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for connection in tuple(self._connections):
            try:
                await connection.send_json(event)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


broker = EventBroker()
