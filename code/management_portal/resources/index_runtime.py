from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .contracts import ResourceQuery
from .errors import ResourceServiceUnavailable
from .timing import lap


def _text(value: object) -> str:
    return str(value or "").strip()


def _dedupe(values: Iterable[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _text(value)
        if clean and clean not in seen:
            seen.add(clean)
            output.append(clean)
    return output


class ResourceIndexRuntime:
    """Business-agnostic runtime around a projection store and Adapter layer."""

    def __init__(
        self,
        *,
        store: Any,
        adapters: Iterable[Any],
        adapter_layer_factory: Callable[..., Any],
        start_worker: bool = True,
        on_projection_changed: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.store = store
        self.adapter_layer = adapter_layer_factory(
            self.store,
            list(adapters),
            start_worker=start_worker,
            on_projection_changed=on_projection_changed,
        )

    def indexed_types(self) -> frozenset[str]:
        return frozenset(
            resource_type
            for adapter in self.adapter_layer.adapters.values()
            for resource_type in adapter.resource_types
        )

    def type_counts(self) -> dict[str, int]:
        return self.store.type_counts()

    def supports_type(self, resource_type: str) -> bool:
        return _text(resource_type) in self.indexed_types()

    def _target_adapters(self, resource_types: Iterable[str] = ()) -> list[str]:
        requested = {_text(value) for value in resource_types if _text(value)}
        return [
            name
            for name, adapter in self.adapter_layer.adapters.items()
            if not requested or requested.intersection(adapter.resource_types)
        ]

    def _require_ready(
        self,
        adapters: Iterable[str],
        *,
        timeout: float = 90.0,
    ) -> None:
        missing = [
            name
            for name in adapters
            if not self.store.adapter_has_snapshot(name)
        ]
        if not missing:
            return
        self.adapter_layer.request_refresh(missing)
        self.adapter_layer.wait(missing, timeout=timeout)
        unavailable = [
            name for name in missing if not self.store.adapter_has_snapshot(name)
        ]
        if unavailable:
            raise ResourceServiceUnavailable(
                "resource projection unavailable: " + ", ".join(unavailable)
            )

    def query(self, **kwargs: Any) -> dict[str, Any]:
        query = ResourceQuery.from_kwargs(kwargs)
        self._require_ready(self._target_adapters(query.resource_types))
        return self.store.query(**kwargs)

    def project_changes(self, projects: Iterable[str], **kwargs: Any) -> dict[str, str]:
        self._require_ready(self._target_adapters())
        return self.store.project_changes(projects, **kwargs)

    def suggest(self, **kwargs: Any) -> dict[str, Any]:
        resource_type = _text(kwargs.get("resource_type"))
        self._require_ready(self._target_adapters((resource_type,) if resource_type else ()))
        return self.store.suggest(**kwargs)

    def authorization_snapshot(self) -> dict[str, Any]:
        self._require_ready(self._target_adapters())
        return self.store.authorization_snapshot()

    def get_resource(
        self,
        resource_id: str,
        *,
        resource_types: Iterable[str] = (),
        actor: str = "",
    ) -> dict[str, Any]:
        try:
            record = self.store.internal_get(resource_id)
        except KeyError:
            targets = self._target_adapters(resource_types)
        else:
            adapter = _text((record.get("source") or {}).get("adapter"))
            targets = [adapter] if adapter in self.adapter_layer.adapters else []
        self._require_ready(targets)
        return self.store.get(resource_id, actor=actor)

    def refresh(
        self,
        *,
        adapters: Iterable[str] = (),
        path: str = "",
        wait: bool = False,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        names = [_text(value) for value in adapters if _text(value)]
        targets = (
            self.adapter_layer.matching_adapters(path=path, adapters=names)
            if path
            else (names or list(self.adapter_layer.adapters))
        )
        if path and not targets:
            raise ValueError(f"no resource index adapter owns path: {path}")
        queued = (
            self.adapter_layer.request_refresh(
                targets, force=True,
                paths=[path] if path and not Path(path).expanduser().is_dir() else (),
            )
            if targets
            else []
        )
        finished = (
            self.adapter_layer.wait(targets, timeout=timeout)
            if wait and targets
            else False
        )
        return {
            "adapters": targets,
            "queued": queued,
            "waited": bool(wait),
            "finished": finished,
            "status": self.adapter_layer.status(),
        }

    def wait_for_refresh(
        self,
        adapters: Iterable[str] = (),
        *,
        timeout: float = 90.0,
    ) -> bool:
        return self.adapter_layer.wait(adapters, timeout=timeout)

    def status(self) -> dict[str, Any]:
        status = self.adapter_layer.status()
        status["resource_types"] = sorted(self.indexed_types())
        adapters = status.get("adapters") if isinstance(status, dict) else {}
        if isinstance(adapters, dict):
            status["adapters"] = {
                str(name): {
                    key: value
                    for key, value in details.items()
                    if key
                    in {
                        "last_checked_at",
                        "last_success_at",
                        "last_error",
                        "resource_error_count",
                        "resource_count",
                    }
                }
                for name, details in adapters.items()
                if isinstance(details, dict)
            }
        return status

    def projection_state(self, adapter: str) -> dict[str, Any]:
        return self.store.adapter_source_state(_text(adapter))

    def project_registered(
        self, resource_type: str, record: dict[str, Any],
    ) -> dict[str, Any]:
        return self.adapter_layer.project_registered(resource_type, record)

    def project_unregistered(self, resource_type: str, resource_id: str) -> dict[str, Any]:
        return self.adapter_layer.project_unregistered(resource_type, resource_id)

    def change_targets(
        self,
        *,
        path: str = "",
        resource_id: str = "",
        resource_type: str = "",
    ) -> list[str]:
        mark = time.perf_counter()
        targets = (
            self.adapter_layer.matching_adapters(path=path)
            if path
            else []
        )
        mark = lap("notify.route.adapters", mark)
        detected_type = _text(resource_type)
        clean_id = _text(resource_id)
        if clean_id:
            try:
                record = self.store.internal_get(clean_id)
            except KeyError:
                record = {}
            lap("notify.route.lookup", mark)
            if record:
                stored_type = _text(record.get("resource_type"))
                if detected_type and stored_type != detected_type:
                    raise ValueError(
                        f"resource change type {detected_type!r} does not match "
                        f"indexed type {stored_type!r}"
                    )
                adapter = _text((record.get("source") or {}).get("adapter"))
                if adapter:
                    targets.append(adapter)
                detected_type = detected_type or stored_type
        if detected_type:
            targets.extend(
                name
                for name, adapter in self.adapter_layer.adapters.items()
                if detected_type in set(adapter.resource_types)
            )
        return _dedupe(targets)

    def queue_change(
        self,
        targets: Iterable[str],
        *,
        path: str = "",
    ) -> list[str]:
        clean_path = _text(path)
        incremental_paths = (
            [clean_path]
            if clean_path and not Path(clean_path).expanduser().is_dir()
            else []
        )
        return self.adapter_layer.request_refresh(
            targets,
            force=True,
            paths=incremental_paths,
        )
