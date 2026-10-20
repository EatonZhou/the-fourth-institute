from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RealtimeDrainResult:
    started_connections: int
    remaining_connections: int
    drained: bool
    elapsed_seconds: float


class RealtimeConnectionLifecycle:
    """Own admission and graceful draining for Portal realtime HTTP streams."""

    def __init__(self) -> None:
        self._accepting = True
        self._drain_event = asyncio.Event()
        self._drained_event = asyncio.Event()
        self._drained_event.set()
        self._next_connection_id = 0
        self._connections: set[int] = set()

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def drain_event(self) -> asyncio.Event:
        return self._drain_event

    @property
    def active_connections(self) -> int:
        return len(self._connections)

    def open_connection(self) -> int | None:
        if not self._accepting:
            return None
        self._next_connection_id += 1
        connection_id = self._next_connection_id
        self._connections.add(connection_id)
        self._drained_event.clear()
        return connection_id

    def close_connection(self, connection_id: int) -> None:
        self._connections.discard(connection_id)
        if not self._connections:
            self._drained_event.set()

    def begin_drain(self) -> int:
        self._accepting = False
        self._drain_event.set()
        return len(self._connections)

    async def wait_until_drained(self, timeout_seconds: float) -> RealtimeDrainResult:
        started = len(self._connections)
        started_at = time.monotonic()
        self.begin_drain()
        try:
            await asyncio.wait_for(
                self._drained_event.wait(),
                timeout=max(0.0, float(timeout_seconds)),
            )
        except TimeoutError:
            pass
        remaining = len(self._connections)
        return RealtimeDrainResult(
            started_connections=started,
            remaining_connections=remaining,
            drained=remaining == 0,
            elapsed_seconds=max(0.0, time.monotonic() - started_at),
        )
