from __future__ import annotations

import json
import sys
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_common.format_tags import schema_matches
from management_common.instance_paths import instance_root
from management_common.file_lock import lock_stream
from management_portal.shared_state import atomic_replace_json


DEV_INSTANCE_ROOT = instance_root() / 'dev'
REQUEST_PATH = DEV_INSTANCE_ROOT / "restart-request.json"
REQUEST_LOCK_PATH = DEV_INSTANCE_ROOT / "restart-request.lock"
REQUEST_SCHEMA = "tfi.portal-dev-restart-request.v2"
MERGE_QUIET_SECONDS = 5.0
MERGE_MAX_SECONDS = 30.0


def _normalized_components(values: Iterable[object]) -> list[str]:
    return sorted({str(value or "").strip() for value in values if str(value or "").strip()})


def read_restart_request(path: Path = REQUEST_PATH) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or not schema_matches(payload.get("schema"), "tfi.portal-dev-restart-request.v2"):
        return {}
    request_id = str(payload.get("request_id") or "").strip()
    components = payload.get("components")
    try:
        revision = int(payload.get("revision") or 0)
    except (TypeError, ValueError):
        return {}
    if not request_id or revision < 1 or not isinstance(components, list):
        return {}
    return {
        "schema": REQUEST_SCHEMA,
        "request_id": request_id,
        "revision": revision,
        "requested_at": str(payload.get("requested_at") or ""),
        "first_requested_at": str(payload.get("first_requested_at") or payload.get("requested_at") or ""),
        "components": _normalized_components(components),
        "auto": bool(payload.get("auto", not components)),
    }


def submit_restart_request(
    components: Iterable[object],
    *,
    path: Path = REQUEST_PATH,
    lock_path: Path = REQUEST_LOCK_PATH,
) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as stream:
        with lock_stream(stream):
            existing = read_restart_request(path)
            submitted = _normalized_components(components)
            merged = _normalized_components([*(existing.get("components") or []), *submitted])
            request_id = str(existing.get("request_id") or "").strip()
            payload: dict[str, object] = {
                "schema": REQUEST_SCHEMA,
                "request_id": request_id or f"restart_request_{uuid.uuid4().hex}",
                "revision": int(existing.get("revision") or 0) + 1,
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "first_requested_at": existing.get("first_requested_at") or datetime.now(timezone.utc).isoformat(),
                "components": merged,
                "auto": bool(existing.get("auto")) or not submitted,
            }
            atomic_replace_json(path, payload)
            return payload


def restart_request_delay(request: dict, *, now: float | None = None) -> float:
    """Bounded quiet window; timestamps survive coordinator replacement."""
    try:
        latest = datetime.fromisoformat(request["requested_at"]).timestamp()
        first = datetime.fromisoformat(request.get("first_requested_at") or request["requested_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0
    return max(0.0, min(latest + MERGE_QUIET_SECONDS, first + MERGE_MAX_SECONDS)
               - (time.time() if now is None else now))


def acknowledge_restart_request(
    request_id: object,
    revision: object,
    *,
    path: Path = REQUEST_PATH,
    lock_path: Path = REQUEST_LOCK_PATH,
) -> bool:
    target = str(request_id or "").strip()
    try:
        target_revision = int(revision)
    except (TypeError, ValueError):
        return False
    if not target or target_revision < 1:
        return False
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as stream:
        with lock_stream(stream):
            current = read_restart_request(path)
            if (
                str(current.get("request_id") or "") != target
                or int(current.get("revision") or 0) != target_revision
            ):
                return False
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            return True
