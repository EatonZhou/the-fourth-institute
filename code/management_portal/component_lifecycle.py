from __future__ import annotations

from management_common.format_tags import schema_matches

import json
import re
import time
from pathlib import Path
from typing import Any

from management_portal.shared_state import atomic_replace_json, exclusive_file_lock

from management_common.instance_paths import instance_root


SCHEMA = "tfi.portal-component-lifecycle.v1"
DEFAULT_INSTANCE_ROOT = instance_root()
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _clean_name(value: object, label: str) -> str:
    clean = str(value or "").strip().lower()
    if not _SAFE_NAME.fullmatch(clean):
        raise ValueError(f"invalid Portal {label}: {value!r}")
    return clean


def lifecycle_path(
    instance: str,
    *,
    instance_root: Path = DEFAULT_INSTANCE_ROOT,
) -> Path:
    return Path(instance_root) / _clean_name(instance, "instance") / "component-lifecycle.json"


def _empty_payload(instance: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "instance": _clean_name(instance, "instance"),
        "updated_at": 0.0,
        "components": {},
    }


def read_lifecycle(
    instance: str,
    *,
    instance_root: Path = DEFAULT_INSTANCE_ROOT,
) -> dict[str, Any]:
    path = lifecycle_path(instance, instance_root=instance_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_payload(instance)
    if not isinstance(payload, dict) or not schema_matches(payload.get("schema"), "tfi.portal-component-lifecycle.v1"):
        return _empty_payload(instance)
    components = payload.get("components")
    if not isinstance(components, dict):
        payload["components"] = {}
    return payload


def read_component_state(
    instance: str,
    component: str,
    *,
    instance_root: Path = DEFAULT_INSTANCE_ROOT,
) -> dict[str, Any]:
    clean_component = _clean_name(component, "component")
    payload = read_lifecycle(instance, instance_root=instance_root)
    state = payload.get("components", {}).get(clean_component)
    return dict(state) if isinstance(state, dict) else {}


def publish_component_state(
    instance: str,
    component: str,
    state: str,
    *,
    message: str,
    operation_id: str,
    instance_root: Path = DEFAULT_INSTANCE_ROOT,
) -> dict[str, Any]:
    clean_instance = _clean_name(instance, "instance")
    clean_component = _clean_name(component, "component")
    clean_state = str(state or "").strip().lower()
    if clean_state not in {"restarting", "ready", "unavailable"}:
        raise ValueError(f"invalid Portal component state: {state!r}")
    path = lifecycle_path(clean_instance, instance_root=instance_root)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    now = time.time()
    with exclusive_file_lock(lock_path):
        payload = read_lifecycle(clean_instance, instance_root=instance_root)
        components = dict(payload.get("components") or {})
        entry = {
            "component": clean_component,
            "state": clean_state,
            "message": str(message or "").strip(),
            "operation_id": str(operation_id or "").strip(),
            "updated_at": now,
        }
        previous = components.get(clean_component)
        if clean_state == "restarting":
            entry["started_at"] = now
        elif isinstance(previous, dict):
            entry["started_at"] = float(previous.get("started_at") or now)
        components[clean_component] = entry
        payload.update(
            {
                "schema": SCHEMA,
                "instance": clean_instance,
                "updated_at": now,
                "components": components,
            }
        )
        atomic_replace_json(path, payload, indent=None)
    return entry
