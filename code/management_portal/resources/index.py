from __future__ import annotations

from typing import Any, Callable, Iterable, Protocol


ResourceRecordsProjector = Callable[
    [Iterable[dict[str, Any]]], list[dict[str, Any]]
]


class ResourceIndex(Protocol):
    """Operations the resource facade requires from an index implementation."""

    def indexed_types(self) -> frozenset[str]: ...

    def type_counts(self) -> dict[str, int]: ...

    def supports_type(self, resource_type: str) -> bool: ...

    def authorization_snapshot(self) -> dict[str, Any]: ...

    def query(
        self,
        *,
        records_projector: ResourceRecordsProjector | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Project immutable records as one query snapshot before filtering."""
        ...

    def suggest(
        self,
        *,
        records_projector: ResourceRecordsProjector | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    def get_resource(
        self,
        resource_id: str,
        *,
        resource_types: Iterable[str] = (),
        actor: str = "",
    ) -> dict[str, Any]: ...

    def refresh(self, **kwargs: Any) -> dict[str, Any]: ...

    def wait_for_refresh(
        self,
        adapters: Iterable[str] = (),
        *,
        timeout: float = 90.0,
    ) -> bool: ...

    def status(self) -> dict[str, Any]: ...

    def projection_state(self, adapter: str) -> dict[str, Any]: ...

    def project_registered(
        self, resource_type: str, record: dict[str, Any],
    ) -> dict[str, Any]: ...

    def change_targets(
        self,
        *,
        path: str = "",
        resource_id: str = "",
        resource_type: str = "",
    ) -> list[str]: ...

    def queue_change(self, targets: Iterable[str], *, path: str = "") -> list[str]: ...
