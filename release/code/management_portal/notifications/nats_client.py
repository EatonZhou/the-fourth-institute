from __future__ import annotations

from typing import Any

from nats.aio.client import Client


class NotificationNatsClient(Client):
    """Keep delayed PONG replies aligned after a timed-out flush."""

    async def _process_pong(self) -> None:
        if self._pongs and self._pongs[0].done():
            self._pongs.pop(0)
            self._pongs_received += 1
            self._pings_outstanding = 0
            return
        await super()._process_pong()


async def connect_notifications(**options: Any) -> NotificationNatsClient:
    client = NotificationNatsClient()
    await client.connect(**options)
    return client
