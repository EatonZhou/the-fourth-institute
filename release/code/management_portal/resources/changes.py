from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


CHANGE_KINDS = frozenset({"created", "updated", "deleted", "moved", "unknown"})


@dataclass(frozen=True)
class ResourceChange:
    kind: str = "unknown"
    path: str = ""
    resource_id: str = ""
    resource_type: str = ""

    def __post_init__(self) -> None:
        clean_kind = str(self.kind or "unknown").strip().lower()
        clean_path = str(self.path or "").strip()
        clean_id = str(self.resource_id or "").strip()
        clean_type = str(self.resource_type or "").strip()
        if clean_kind not in CHANGE_KINDS:
            raise ValueError(f"invalid resource change kind: {clean_kind}")
        if not clean_path and not clean_id and not clean_type:
            raise ValueError("resource change requires path, resource_id, or resource_type")
        object.__setattr__(self, "kind", clean_kind)
        object.__setattr__(self, "path", clean_path)
        object.__setattr__(self, "resource_id", clean_id)
        object.__setattr__(self, "resource_type", clean_type)


@dataclass(frozen=True)
class ChangeNotification:
    change: ResourceChange
    targets: tuple[str, ...]
    queued: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return bool(self.targets)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "tfi.resource_change_notification.v1",
            "accepted": self.accepted,
            "change": {
                "kind": self.change.kind,
                "path": self.change.path,
                "resource_id": self.change.resource_id,
                "resource_type": self.change.resource_type,
            },
            "targets": list(self.targets),
            "queued": list(self.queued),
        }


class ResourceChangeDispatcher:
    def __init__(
        self,
        route: Callable[[ResourceChange], Iterable[str]],
        enqueue: Callable[[tuple[str, ...], ResourceChange], Iterable[str]],
    ) -> None:
        self._route = route
        self._enqueue = enqueue

    def notify(self, change: ResourceChange) -> ChangeNotification:
        targets = tuple(dict.fromkeys(str(value) for value in self._route(change) if value))
        queued = tuple(self._enqueue(targets, change)) if targets else ()
        return ChangeNotification(change=change, targets=targets, queued=queued)
