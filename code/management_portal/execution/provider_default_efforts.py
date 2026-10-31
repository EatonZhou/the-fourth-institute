"""The reasoning effort a Provider picks when a Resident leaves Effort on default.

A Resident whose Effort is "default" sends no effort; the Provider then
chooses one for the model, and Portal has no way to ask which beforehand.
Claude Code, for one, records the effort of every request in its session
transcript, and the turn's usage carries it as ``reasoningEffort``.

When such a turn ends, the Worker marks the usage with the model the request
named and that the effort was the Provider's choice.  The ExecutionService
keeps the latest choice per account route and model, and the runtime catalog
offers it as ``default_efforts_by_model`` where the Provider declares none,
so Resident Settings shows "medium (default)" instead of an unnamed default.
The value is what the last such turn ran with: it follows the Provider's own
default when the Provider changes it, one turn later.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .scheduler_rows import compact_json, json_object

_KEY_PREFIX = "provider_default_effort:"


def mark_provider_default(usage: Mapping[str, Any], *, model: str) -> dict[str, Any]:
    """The usage of a turn that requested no effort, naming the effort the Provider chose."""

    marked = dict(usage)
    if not str(marked.get("reasoningEffort") or "").strip() or not str(model or "").strip():
        return marked
    marked["providerDefaultEffort"] = {
        "model": str(model).strip(),
        "effort": str(marked["reasoningEffort"]).strip(),
    }
    return marked


def _route_key(route_id: str) -> str:
    return _KEY_PREFIX + route_id


def record(changes: Any, result: Mapping[str, Any]) -> bool:
    """Keep the default effort an ended turn ran with; whether it changed."""

    usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
    observed = usage.get("providerDefaultEffort")
    route_id = str(result.get("backend_route_id") or "").strip()
    if not isinstance(observed, Mapping) or not route_id:
        return False
    model = str(observed.get("model") or "").strip()
    effort = str(observed.get("effort") or "").strip()
    if not model or not effort:
        return False
    key = _route_key(route_id)
    with changes.connect() as connection:
        row = connection.execute(
            "SELECT value FROM execution_store_meta WHERE key=%s FOR UPDATE", (key,),
        ).fetchone()
        efforts = json_object(row["value"]) if row else {}
        # A write wakes the Scheduler's listeners; an unchanged default is not written.
        if efforts.get(model) == effort:
            return False
        efforts[model] = effort
        connection.execute(
            "INSERT INTO execution_store_meta(key, value) VALUES (%s, %s) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, compact_json(efforts)),
        )
        connection.commit()
    return True


def _observed(changes: Any, route_ids: list[str]) -> dict[str, dict[str, str]]:
    keys = [_route_key(route_id) for route_id in dict.fromkeys(route_ids) if route_id]
    if not keys:
        return {}
    with changes.connect() as connection:
        rows = connection.execute(
            "SELECT key, value FROM execution_store_meta WHERE key = ANY(%s)", (keys,),
        ).fetchall()
    return {
        str(row["key"])[len(_KEY_PREFIX):]: {
            str(model): str(effort)
            for model, effort in json_object(row["value"]).items()
            if isinstance(effort, str) and effort
        }
        for row in rows
    }


def _fill(catalog: dict[str, Any], observed: Mapping[str, str]) -> None:
    declared = catalog.get("default_efforts_by_model")
    defaults = dict(declared) if isinstance(declared, Mapping) else {}
    offered = catalog.get("efforts_by_model") if isinstance(catalog.get("efforts_by_model"), Mapping) else {}
    for model, effort in observed.items():
        # A declared default wins; an effort the model does not offer could not be saved back.
        if not defaults.get(model) and effort in (offered.get(model) or []):
            defaults[model] = effort
    if defaults:
        catalog["default_efforts_by_model"] = defaults


def apply(changes: Any, catalog: dict[str, Any]) -> dict[str, Any]:
    """The runtime catalog, with the observed default efforts of its routes."""

    routes = [catalog]
    for backend in catalog.get("backends") or []:
        if isinstance(backend, dict):
            routes.extend(route for route in backend.get("routes") or [] if isinstance(route, dict))
    route_ids = [str(route.get("backend_route_id") or "") for route in routes]
    observed = _observed(changes, route_ids)
    for route in routes:
        efforts = observed.get(str(route.get("backend_route_id") or ""))
        if efforts:
            _fill(route, efforts)
    return catalog
