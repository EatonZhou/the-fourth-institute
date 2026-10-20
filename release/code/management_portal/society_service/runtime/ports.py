from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import ResidentTerminalFact


class ResidentTerminalPort(Protocol):
    def consume_terminal(
        self,
        fact: ResidentTerminalFact,
    ) -> dict[str, Any]: ...


class SystemEventPort(Protocol):
    def poll_all(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class LineageRuntimePorts:
    resident_terminals: ResidentTerminalPort
    system_events: SystemEventPort
