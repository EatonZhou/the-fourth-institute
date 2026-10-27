from __future__ import annotations

import asyncio


class RealtimeConnectionLifecycle:
    """Admission of Portal realtime event streams and the signal that ends them."""

    def __init__(self) -> None:
        self._accepting = True
        self._drain_event = asyncio.Event()

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def drain_event(self) -> asyncio.Event:
        return self._drain_event

    def begin_drain(self) -> None:
        self._accepting = False
        self._drain_event.set()
