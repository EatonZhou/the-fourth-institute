from __future__ import annotations

import threading
from typing import Any, Callable


class _BoundPort:
    def __init__(self, name: str) -> None:
        self._name = name
        self._implementation: Any = None
        self._lock = threading.Lock()

    def bind(self, implementation: Any) -> None:
        if implementation is None:
            raise ValueError(f"{self._name} implementation is required")
        with self._lock:
            self._implementation = implementation

    def __getattr__(self, name: str) -> Any:
        implementation = self._implementation
        if implementation is None:
            raise RuntimeError(f"{self._name} is not configured")
        return getattr(implementation, name)


resource_port = _BoundPort("Lineage ResourceService port")
execution_port = _BoundPort("Lineage ExecutionService port")


class _ResidentDomainPort(_BoundPort):
    def __init__(self) -> None:
        super().__init__("Resident domain port")


resident_domain_port = _ResidentDomainPort()


class _ProjectDomainPort:
    def __init__(self) -> None:
        self._bound = _BoundPort("Project domain port")

    def bind(self, implementation: Any) -> None:
        self._bound.bind(implementation)

    def validate_existing_project_selection(self, payload: dict[str, Any]) -> Any:
        return self._bound.validate_existing_project_selection(payload)



project_domain_port = _ProjectDomainPort()


class _ResidentLineageHostPort(_BoundPort):
    def __init__(self) -> None:
        super().__init__("Resident Lineage host port")


resident_lineage_host_port = _ResidentLineageHostPort()

# Program runtime failures and unsettled Effect activations reach people here.
failure_notification_port = _BoundPort("Lineage failure notification port")


class _ResidentTerminalPort:
    def __init__(self) -> None:
        self._handler: Callable[..., object] | None = None
        self._lock = threading.Lock()

    def bind(self, handler: Callable[..., object] | None) -> None:
        with self._lock:
            self._handler = handler

    def report(self, *args: Any, **kwargs: Any) -> object | None:
        handler = self._handler
        if handler is None:
            return None
        from . import invariant_locks
        if invariant_locks.lock_held():
            invariant_locks.defer_until_state_unlock(lambda: handler(*args, **kwargs))
            return None
        return handler(*args, **kwargs)


resident_terminal_port = _ResidentTerminalPort()






def bind_domain_ports(
    *,
    resource: Any,
    execution: Any,
    project: Any,
    resident: Any,
    resident_lineage: Any,
    failure_notifications: Any,
) -> None:
    resource_port.bind(resource)
    execution_port.bind(execution)
    project_domain_port.bind(project)
    resident_domain_port.bind(resident)
    resident_lineage_host_port.bind(resident_lineage)
    failure_notification_port.bind(failure_notifications)
