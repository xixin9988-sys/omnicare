import logging

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class N8NClient:
    def __init__(self, settings: Settings) -> None:
        self.webhook_url = settings.n8n_webhook_url.strip()
        self.timeout = settings.n8n_webhook_timeout_seconds

    async def dispatch(self, event_type: str, payload: dict) -> bool:
        if not self.webhook_url:
            logger.info("n8n webhook not configured; event=%s kept in audit log", event_type)
            return False
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.webhook_url, json={"event_type": event_type, **payload}
                )
                response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("n8n dispatch failed: %s", type(exc).__name__)
            return False
