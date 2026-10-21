from __future__ import annotations

import json
import os
import random
import re
import subprocess
import threading
import time
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from management_common.portal_paths import PORTAL_ROOT, path as portal_path
from management_common.site_compatibility import compatibility_value


from . import device_identity_backend
from .execution.service import ExecutionService
from .execution.account_resources import with_account_resources
from .society_service import get_society_service
from . import status_cache_store, token_usage_snapshot_cache, usage_read_model


STATUS_CACHE_PATH = status_cache_store.default_path()
AUTO_REFRESH_MIN_SECONDS = int(os.getenv("AGENT_STATUS_AUTO_REFRESH_MIN_SECONDS", "1200"))
AUTO_REFRESH_MAX_SECONDS = int(os.getenv("AGENT_STATUS_AUTO_REFRESH_MAX_SECONDS", "2400"))
MANUAL_REFRESH_MIN_SECONDS = int(os.getenv("AGENT_STATUS_MANUAL_REFRESH_MIN_SECONDS", "300"))
GPU_STATUS_SCRIPT = Path(os.getenv(
    "AGENT_STATUS_GPU_SCRIPT",
    str(PORTAL_ROOT / "scripts" / "thor_gpu_status.py"),
)).expanduser()
GPU_STATUS_TIMEOUT_SECONDS = float(os.getenv("AGENT_STATUS_GPU_TIMEOUT_SECONDS", "60"))
GPU_STATUS_CACHE_SECONDS = int(os.getenv("AGENT_STATUS_GPU_CACHE_SECONDS", "60"))
GPU_BUSY_UTIL_PERCENT = float(os.getenv("AGENT_STATUS_GPU_BUSY_UTIL_PERCENT", "50"))
GPU_BUSY_MEMORY_PERCENT = float(os.getenv("AGENT_STATUS_GPU_BUSY_MEMORY_PERCENT", "50"))
GPU_EMPTY_UTIL_PERCENT = float(os.getenv("AGENT_STATUS_GPU_EMPTY_UTIL_PERCENT", "5"))
GPU_EMPTY_MEMORY_PERCENT = float(os.getenv("AGENT_STATUS_GPU_EMPTY_MEMORY_PERCENT", "5"))
DISK_STATUS_TIMEOUT_SECONDS = float(os.getenv("AGENT_STATUS_DISK_TIMEOUT_SECONDS", "24"))
DISK_STATUS_CACHE_SECONDS = int(os.getenv("AGENT_STATUS_DISK_CACHE_SECONDS", "300"))
DISK_WARN_USED_PERCENT = float(os.getenv("AGENT_STATUS_DISK_WARN_USED_PERCENT", "85"))
DISK_CRITICAL_USED_PERCENT = float(os.getenv("AGENT_STATUS_DISK_CRITICAL_USED_PERCENT", "92"))
DISK_TRIAGE_LARGE_FILE_BYTES = int(os.getenv("AGENT_STATUS_DISK_TRIAGE_LARGE_FILE_BYTES", str(5 * 1024 * 1024 * 1024)))
DISK_TRIAGE_MAX_LARGE_FILES = int(os.getenv("AGENT_STATUS_DISK_TRIAGE_MAX_LARGE_FILES", "10"))
AGENT_STATUS_SUMMARY_WAIT_SECONDS = 1.0
RESIDENT_USAGE_REFRESH_SECONDS = int(os.getenv("AGENT_STATUS_RESIDENT_USAGE_REFRESH_SECONDS", "600"))
PORTAL_INSTANCE = re.sub(
    r"[^a-zA-Z0-9_.-]+",
    "-",
    str(os.getenv("PORTAL_INSTANCE", "local") or "local").strip(),
)
MAX_PROVIDER_ERROR_CHARS = 180
CHART_TOP_RESIDENT_LIMIT = 20
_CACHE_LOCK = threading.RLock()
_REFRESH_LOCK = threading.RLock()
_REFRESHING_KEYS: set[str] = set()
_REFRESH_DONE: dict[str, threading.Event] = {}


DISK_RESOURCE_POOL: tuple[dict[str, Any], ...] = (
    {
        "resource_id": "nas_storage",
        "display_name": "NAS",
        "host_label": compatibility_value("status_local_host_label", "local"),
        "kind": "local",
        "paths": [str(portal_path(key)) for key in ("share_root", "staging_root", "instances_root")],
    },
    {
        "resource_id": "thor_storage",
        "display_name": "Thor",
        "host_label": "dirtythor0",
        "kind": "ssh",
        "ssh_config": str(compatibility_value("status_remote_ssh_config", str(Path.home() / ".ssh/config"))),
        "ssh_host": "thor",
        "paths": ["/home", "/srv", "/tmp"],
    },
)


DISK_RESOURCE_MAP = {str(item["resource_id"]): item for item in DISK_RESOURCE_POOL}


def _now_ts() -> float:
    return time.time()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_status_cache() -> dict[str, Any]:
    try:
        payload = json.loads(STATUS_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_status_cache(payload: dict[str, Any]) -> None:
    status_cache_store.write(STATUS_CACHE_PATH, payload)


def _jittered_auto_refresh_seconds() -> int:
    lower = max(60, AUTO_REFRESH_MIN_SECONDS)
    upper = max(lower, AUTO_REFRESH_MAX_SECONDS)
    return random.randint(lower, upper)




def _cached_payload_state(key: str) -> tuple[dict[str, Any] | None, bool]:
    now = _now_ts()
    with _CACHE_LOCK:
        cache = _read_status_cache()
        entry = cache.get(key) if isinstance(cache.get(key), dict) else {}
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else None
        next_refresh_at = float(entry.get("next_refresh_at") or 0)
        return (dict(payload) if payload else None), now >= next_refresh_at


def _schedule_background_refresh(key: str, refresh_fn) -> threading.Event:
    """Start ``refresh_fn`` unless one already runs for ``key``; the event is set when it ends."""
    with _REFRESH_LOCK:
        if key in _REFRESHING_KEYS:
            return _REFRESH_DONE.setdefault(key, threading.Event())
        _REFRESHING_KEYS.add(key)
        done = _REFRESH_DONE[key] = threading.Event()

    def run() -> None:
        try:
            refresh_fn()
        finally:
            with _REFRESH_LOCK:
                _REFRESHING_KEYS.discard(key)
            done.set()

    threading.Thread(target=run, name=f"agent-status-refresh-{key}", daemon=True).start()
    return done


def _refresh_in_progress(key: str) -> bool:
    with _REFRESH_LOCK:
        return key in _REFRESHING_KEYS


def _store_payload(
    key: str,
    payload: dict[str, Any],
    cache: dict[str, Any],
    *,
    force: bool,
    refresh_after_seconds: int | None = None,
) -> dict[str, Any]:
    now = _now_ts()
    stored = dict(payload)
    stored["updated_at"] = stored.get("updated_at") or _now_iso()
    next_refresh_at = now + (refresh_after_seconds if refresh_after_seconds is not None else _jittered_auto_refresh_seconds())
    with _CACHE_LOCK, status_cache_store.locked(STATUS_CACHE_PATH):
        latest = status_cache_store.read(STATUS_CACHE_PATH)
        entry = latest.get(key) if isinstance(latest.get(key), dict) else (
            cache.get(key) if isinstance(cache.get(key), dict) else {}
        )
        latest[key] = {
            "payload": stored,
            "stored_at": now,
            "stored_at_iso": _now_iso(),
            "next_refresh_at": next_refresh_at,
            "next_refresh_at_iso": datetime.fromtimestamp(next_refresh_at, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "last_forced_at": now if force else float(entry.get("last_forced_at") or 0),
        }
        _write_status_cache(latest)
    return stored


def _date_today() -> date:
    return datetime.now().date()




def _nonnegative_int(value: object) -> int:
    try:
        number = int(value or 0)
    except Exception:
        return 0
    return number if number > 0 else 0


def _safe_error(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:MAX_PROVIDER_ERROR_CHARS]


def _short_text(value: object, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]






def _gpu_devices() -> list[dict[str, Any]]:
    return device_identity_backend.load_device_records()


def _gpu_servers(
    records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    source = records if records is not None else _gpu_devices()
    return [item for item in source if item.get("device_kind") == "gpu_server"]


def _gpu_target(
    device_id: str,
    records: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    clean_id = str(device_id or "").strip()
    target = next(
        (item for item in records if item.get("device_id") == clean_id),
        None,
    )
    if target is None:
        return None
    if target.get("device_kind") == "gpu_server":
        return target, None
    if target.get("device_kind") != "gpu":
        return None
    parent_id = str(target.get("parent_device_id") or "").strip()
    parent = next(
        (
            item
            for item in records
            if item.get("device_id") == parent_id
            and item.get("device_kind") == "gpu_server"
        ),
        None,
    )
    return (parent, target) if parent is not None else None


def _gpu_server_public(server: dict[str, Any]) -> dict[str, Any]:
    network = server.get("network") if isinstance(server.get("network"), dict) else {}
    monitor = server.get("monitor") if isinstance(server.get("monitor"), dict) else {}
    return {
        "device_id": str(server.get("device_id") or ""),
        "device_kind": "gpu_server",
        "display_name": str(server.get("name") or server.get("device_id") or ""),
        "host_label": str(server.get("hostname") or ""),
        "ip": str(network.get("primary_ip") or ""),
        "expected_gpu_count": _nonnegative_int(monitor.get("expected_gpu_count")),
    }


_GPU_OBSOLETE_STATUS_KEYS = (
    "available",
    "available_for_new_checkin",
    "busy",
    "checkins",
    "gpu_lock_enabled",
    "lock_enabled",
    "lock_mode",
    "physical_available_gpu_count",
    "reserved",
)


def _gpu_memory_used_percent(gpu: dict[str, Any]) -> float | None:
    used_raw = gpu.get("memory_used_mb")
    total_raw = gpu.get("memory_total_mb")
    if used_raw is not None and total_raw is not None:
        used = float(_nonnegative_int(used_raw))
        total = float(_nonnegative_int(total_raw))
        if total > 0:
            return used / total * 100
    if gpu.get("unified_memory_used_percent") is not None:
        try:
            return float(gpu.get("unified_memory_used_percent") or 0)
        except Exception:
            return None
    if gpu.get("memory_util_percent") is not None:
        return float(_nonnegative_int(gpu.get("memory_util_percent")))
    return None


def _gpu_state(gpu: dict[str, Any]) -> str:
    if gpu.get("gpu_util_percent") is None:
        return "unknown"
    util = float(_nonnegative_int(gpu.get("gpu_util_percent")))
    memory = _gpu_memory_used_percent(gpu)
    if memory is None:
        return "unknown"
    if util < GPU_EMPTY_UTIL_PERCENT and memory < GPU_EMPTY_MEMORY_PERCENT:
        return "empty"
    if util > GPU_BUSY_UTIL_PERCENT or memory > GPU_BUSY_MEMORY_PERCENT:
        return "busy"
    return "idle"


def _normalize_gpu_observation(
    payload: dict[str, Any],
    server: dict[str, Any],
    *,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    all_devices = records if records is not None else _gpu_devices()
    server_id = str(server.get("device_id") or "").strip()
    registered_gpus = device_identity_backend.gpu_children(
        server_id,
        records=all_devices,
    )
    by_index = {int(item["gpu_index"]): item for item in registered_gpus}
    out = dict(payload)
    for key in _GPU_OBSOLETE_STATUS_KEYS:
        out.pop(key, None)
    out.pop("resource_id", None)
    out.update(_gpu_server_public(server))
    observed_at = str(
        out.get("updated_at") or out.get("generated_at") or _now_iso()
    ).strip()
    gpus = []
    for item in out.get("gpus") if isinstance(out.get("gpus"), list) else []:
        if not isinstance(item, dict):
            continue
        gpu = dict(item)
        gpu.pop("locked", None)
        try:
            gpu_index = int(gpu.get("index"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"GPU server {server_id} returned an invalid GPU index") from exc
        registered = by_index.get(gpu_index)
        if registered is None:
            raise ValueError(
                f"GPU server {server_id} returned unregistered GPU index {gpu_index}"
            )
        observed_uuid = str(gpu.get("uuid") or "").strip()
        registered_uuid = str(registered.get("gpu_uuid") or "").strip()
        if observed_uuid.casefold() != registered_uuid.casefold():
            raise ValueError(
                f"GPU identity mismatch at {server_id}/gpu/{gpu_index}"
            )
        state = _gpu_state(gpu)
        gpu["device_id"] = registered["device_id"]
        gpu["device_kind"] = "gpu"
        gpu["parent_device_id"] = server_id
        gpu["index"] = gpu_index
        gpu["state"] = state
        gpu["busy"] = state == "busy"
        gpu["updated_at"] = observed_at
        gpus.append(gpu)
    expected = _nonnegative_int((server.get("monitor") or {}).get("expected_gpu_count"))
    if str(out.get("status") or "") != "error" and len(gpus) != expected:
        raise ValueError(
            f"GPU server {server_id} returned {len(gpus)} GPUs; expected {expected}"
        )
    total = expected
    available = 0
    busy_indexes = []
    state_counts = {"empty": 0, "idle": 0, "busy": 0, "unknown": 0}
    for gpu in gpus:
        state = str(gpu.get("state") or "idle")
        if state in state_counts:
            state_counts[state] += 1
        if state == "busy":
            busy_indexes.append(gpu.get("index"))
        elif state in {"empty", "idle"}:
            available += 1
    out["gpu_count"] = total
    out["available_gpu_count"] = available
    out["busy_gpu_count"] = state_counts["busy"]
    out["unknown_gpu_count"] = state_counts["unknown"]
    out["empty_gpu_count"] = state_counts["empty"]
    out["idle_gpu_count"] = state_counts["idle"]
    out["busy_gpu_indexes"] = busy_indexes
    out["gpu_device_ids"] = [item["device_id"] for item in registered_gpus]
    out["gpus"] = gpus
    out["updated_at"] = observed_at
    out["ttl_seconds"] = GPU_STATUS_CACHE_SECONDS
    return out


def _summarize_gpu_status(
    payload: dict[str, Any],
    server: dict[str, Any],
    *,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _normalize_gpu_observation(payload, server, records=records)


def _read_gpu_status_direct(
    server: dict[str, Any],
    *,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not GPU_STATUS_SCRIPT.exists():
        raise FileNotFoundError(str(GPU_STATUS_SCRIPT))
    command = [str(GPU_STATUS_SCRIPT)] if os.access(GPU_STATUS_SCRIPT, os.X_OK) else ["python3", str(GPU_STATUS_SCRIPT)]
    access_methods = (
        server.get("access_methods")
        if isinstance(server.get("access_methods"), dict)
        else {}
    )
    ssh = (
        access_methods.get("ssh")
        if isinstance(access_methods.get("ssh"), dict)
        else {}
    )
    config_path = str(ssh.get("config_path") or "").strip()
    host = str(ssh.get("host") or "").strip()
    if config_path:
        command.extend(["--ssh-config", config_path])
    if host:
        command.extend(["--host", host])
    command.extend(["--timeout", str(max(2, int(GPU_STATUS_TIMEOUT_SECONDS)))])
    result = subprocess.run(command, text=True, capture_output=True, timeout=GPU_STATUS_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"gpu status exited {result.returncode}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise ValueError("gpu status returned non-object json")
    return _summarize_gpu_status(payload, server, records=records)


def _gpu_status_placeholder(server: dict[str, Any]) -> dict[str, Any]:
    public = _gpu_server_public(server)
    expected = public["expected_gpu_count"]
    return {
        **public,
        "status": "not_checked",
        "updated_at": "",
        "ttl_seconds": GPU_STATUS_CACHE_SECONDS,
        "gpu_count": expected,
        "available_gpu_count": 0,
        "busy_gpu_count": 0,
        "empty_gpu_count": 0,
        "idle_gpu_count": 0,
        "gpus": [],
    }


def _collect_gpu_status(
    server: dict[str, Any],
    *,
    force: bool = False,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    server_id = str(server.get("device_id") or "").strip()
    cached_payload = device_identity_backend.cached_gpu_server_status(server_id)
    if not force:
        return (
            _normalize_gpu_observation(cached_payload, server, records=records)
            if cached_payload is not None
            else _gpu_status_placeholder(server)
        )
    try:
        payload = _read_gpu_status_direct(server, records=records)
    except Exception as exc:
        if cached_payload is not None:
            stale = _normalize_gpu_observation(cached_payload, server, records=records)
            stale["stale"] = True
            stale["refresh_error"] = _safe_error(exc)
            return stale
        payload = {
            **_gpu_server_public(server),
            "status": "error",
            "updated_at": _now_iso(),
            "ttl_seconds": GPU_STATUS_CACHE_SECONDS,
            "error": _safe_error(exc),
            "gpu_count": _nonnegative_int((server.get("monitor") or {}).get("expected_gpu_count")),
            "available_gpu_count": 0,
            "busy_gpu_count": 0,
            "empty_gpu_count": 0,
            "idle_gpu_count": 0,
            "gpus": [],
        }
    return device_identity_backend.store_gpu_server_status(server_id, payload)


def _disk_resource_by_id(resource_id: str) -> dict[str, Any] | None:
    return DISK_RESOURCE_MAP.get(str(resource_id or "").strip())


def _disk_resource_public(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "resource_id": str(resource.get("resource_id") or ""),
        "display_name": str(resource.get("display_name") or resource.get("resource_id") or ""),
        "host_label": str(resource.get("host_label") or ""),
        "kind": str(resource.get("kind") or "local"),
        "paths": [str(item) for item in (resource.get("paths") or []) if str(item or "").strip()],
    }


def _disk_status_cache_key(resource: dict[str, Any]) -> str:
    return f"disk_status:{resource.get('resource_id')}"


def _disk_status_placeholder(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        **_disk_resource_public(resource),
        "status": "loading",
        "updated_at": "",
        "ttl_seconds": DISK_STATUS_CACHE_SECONDS,
        "filesystems": [],
        "transfer_targets": [],
        "suggestions": [],
        "triage": {"findings": [], "recommendations": [], "errors": []},
    }


def _run_disk_script(resource: dict[str, Any], script: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    kind = str(resource.get("kind") or "local")
    timeout = max(6.0, DISK_STATUS_TIMEOUT_SECONDS)
    if kind == "ssh":
        ssh_config = str(resource.get("ssh_config") or "").strip()
        ssh_host = str(resource.get("ssh_host") or "").strip()
        if not ssh_host:
            raise ValueError("missing ssh host")
        command = ["ssh"]
        if ssh_config:
            command.extend(["-F", ssh_config])
        command.extend([
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={max(1, min(15, int(timeout)))}",
            ssh_host,
            "sh",
            "-s",
            "--",
            *args,
        ])
    else:
        command = ["sh", "-s", "--", *args]
    return subprocess.run(
        command,
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _parse_df_output(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_path = ""
    for raw_line in str(text or "").splitlines():
        if raw_line.startswith("\x1fPORTAL_PATH\t"):
            current_path = raw_line.split("\t", 1)[1].strip()
            continue
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Filesystem ") or line.startswith("Filesystem\t"):
            continue
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        filesystem, fs_type, size, used, available, capacity, mountpoint = parts
        try:
            size_bytes = int(size)
            used_bytes = int(used)
            available_bytes = int(available)
        except Exception:
            continue
        try:
            used_percent = float(str(capacity).strip().rstrip("%"))
        except Exception:
            used_percent = (used_bytes / size_bytes * 100) if size_bytes > 0 else 0.0
        if used_percent >= DISK_CRITICAL_USED_PERCENT:
            status = "critical"
        elif used_percent >= DISK_WARN_USED_PERCENT:
            status = "warn"
        else:
            status = "ok"
        rows.append({
            "path": current_path or mountpoint,
            "filesystem": filesystem,
            "fstype": fs_type,
            "mount": mountpoint,
            "size_bytes": size_bytes,
            "used_bytes": used_bytes,
            "available_bytes": available_bytes,
            "used_percent": round(used_percent, 1),
            "status": status,
        })
    return rows


def _collect_df_rows(resource: dict[str, Any], paths: list[str]) -> tuple[list[dict[str, Any]], str]:
    clean_paths = [path for path in paths if str(path or "").strip()]
    if not clean_paths:
        return [], ""
    script = """
for p in "$@"; do
  if [ -e "$p" ]; then
    printf '\\037PORTAL_PATH\\t%s\\n' "$p"
    df -PT -B1 "$p" 2>/dev/null | tail -n +2
  fi
done
"""
    try:
        result = _run_disk_script(resource, script, clean_paths)
    except Exception as exc:
        return [], _safe_error(exc)
    rows = _parse_df_output(result.stdout)
    if result.returncode != 0 and not rows:
        return [], _short_text(result.stderr or result.stdout or f"df exited {result.returncode}", 180)
    return rows, ""


def _collapse_df_rows_by_mount(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_volume: dict[str, dict[str, Any]] = {}
    path_order: dict[str, list[str]] = {}
    mount_order: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        mount = str(row.get("mount") or row.get("path") or "").strip()
        if not mount:
            continue
        key = "|".join([
            str(row.get("filesystem") or ""),
            str(row.get("fstype") or ""),
            str(row.get("size_bytes") or ""),
        ]) or mount
        path = str(row.get("path") or "").strip()
        if key not in by_volume:
            item = dict(row)
            item["path"] = mount
            by_volume[key] = item
            path_order[key] = []
            mount_order[key] = []
        elif mount == "/" and str(by_volume[key].get("mount") or "") != "/":
            item = dict(row)
            item["path"] = mount
            by_volume[key].update(item)
        if path and path not in path_order[key]:
            path_order[key].append(path)
        if mount and mount not in mount_order[key]:
            mount_order[key].append(mount)
    out: list[dict[str, Any]] = []
    for key, item in by_volume.items():
        paths = path_order.get(key) or []
        mounts = sorted(mount_order.get(key) or [], key=lambda value: (value != "/", len(value), value))
        item["paths"] = paths
        item["watched_paths"] = paths
        item["mounts"] = mounts
        item["volume_label"] = str(item.get("filesystem") or item.get("mount") or item.get("path") or "")
        out.append(item)
    out.sort(key=lambda item: str(item.get("volume_label") or item.get("mount") or item.get("path") or ""))
    return out


def _flatten_lsblk_nodes(nodes: list[Any], inherited: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    inherited = inherited or {}
    out: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        item = dict(node)
        for key in ("tran", "rm", "model"):
            if item.get(key) in (None, "") and inherited.get(key) not in (None, ""):
                item[key] = inherited.get(key)
        out.append(item)
        children = item.get("children") if isinstance(item.get("children"), list) else []
        if children:
            out.extend(_flatten_lsblk_nodes(children, item))
    return out


def _mounted_path_from_lsblk(item: dict[str, Any]) -> str:
    mount = item.get("mountpoint")
    if isinstance(mount, list):
        mount = next((str(value) for value in mount if str(value or "").strip()), "")
    return str(mount or "").strip()


def _looks_like_external_target(item: dict[str, Any], mountpoint: str) -> bool:
    if not mountpoint:
        return False
    if mountpoint.startswith(("/media/", "/mnt/", "/run/media/")):
        return True
    transport = str(item.get("tran") or "").strip().lower()
    removable = str(item.get("rm") or "").strip().lower()
    return transport in {"usb", "firewire", "thunderbolt"} or removable in {"1", "true", "yes"}


def _collect_transfer_targets(resource: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    script = "lsblk -J -b -o NAME,TYPE,SIZE,MOUNTPOINT,TRAN,RM,MODEL,LABEL,FSTYPE 2>/dev/null\n"
    try:
        result = _run_disk_script(resource, script, [])
    except Exception as exc:
        return [], _safe_error(exc)
    if result.returncode != 0:
        return [], _short_text(result.stderr or result.stdout or f"lsblk exited {result.returncode}", 180)
    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        return [], _safe_error(exc)
    nodes = _flatten_lsblk_nodes(payload.get("blockdevices") if isinstance(payload, dict) and isinstance(payload.get("blockdevices"), list) else [])
    candidates: list[dict[str, Any]] = []
    seen_mounts: set[str] = set()
    for item in nodes:
        mountpoint = _mounted_path_from_lsblk(item)
        if not _looks_like_external_target(item, mountpoint) or mountpoint in seen_mounts:
            continue
        seen_mounts.add(mountpoint)
        candidates.append({
            "name": str(item.get("name") or ""),
            "mount": mountpoint,
            "model": str(item.get("model") or "").strip(),
            "label": str(item.get("label") or "").strip(),
            "fstype": str(item.get("fstype") or "").strip(),
            "transport": str(item.get("tran") or "").strip(),
            "removable": str(item.get("rm") or "").strip().lower() in {"1", "true", "yes"},
            "size_bytes": _nonnegative_int(item.get("size")),
        })
    if not candidates:
        return [], ""
    df_rows, error = _collect_df_rows(resource, [item["mount"] for item in candidates])
    by_mount = {row.get("mount"): row for row in df_rows}
    for candidate in candidates:
        row = by_mount.get(candidate["mount"]) or {}
        candidate["available_bytes"] = _nonnegative_int(row.get("available_bytes"))
        candidate["used_percent"] = row.get("used_percent")
    candidates.sort(key=lambda item: _nonnegative_int(item.get("available_bytes")), reverse=True)
    return candidates, error


def _human_bytes(value: object) -> str:
    number = float(_nonnegative_int(value))
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    if index == 0:
        return f"{int(number)} {units[index]}"
    return f"{number:.1f} {units[index]}"


def _human_size_to_bytes(text: str) -> int:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?)(?:i?B|B)?", str(text or ""), re.I)
    if not match:
        return 0
    try:
        number = float(match.group(1))
    except Exception:
        return 0
    unit = match.group(2).upper()
    scale = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5, "E": 1024 ** 6}.get(unit, 1)
    return int(number * scale)


def _disk_triage_run(resource: dict[str, Any], script: str, args: list[str] | None = None) -> tuple[str, str]:
    try:
        result = _run_disk_script(resource, script, args or [])
    except Exception as exc:
        return "", _safe_error(exc)
    if result.returncode != 0:
        return result.stdout or "", _short_text(result.stderr or result.stdout or f"triage exited {result.returncode}", 180)
    return result.stdout or "", ""


def _disk_triage_deleted_open(resource: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    script = r"""
if ! command -v lsof >/dev/null 2>&1; then
  exit 0
fi
lsof -nP +L1 2>/dev/null | awk '
  NR > 1 && $7 ~ /^[0-9]+$/ {
    sum += $7; count += 1;
    if ($7 > max) {
      max = $7; cmd = $1; pid = $2; name = "";
      for (i = 9; i <= NF; i++) name = name (i > 9 ? " " : "") $i;
    }
  }
  END { if (count > 0) print count "\t" sum "\t" max "\t" cmd "\t" pid "\t" name; }
'
"""
    stdout, error = _disk_triage_run(resource, script)
    line = next((item for item in stdout.splitlines() if item.strip()), "")
    if not line:
        return None, error
    parts = line.split("\t", 5)
    if len(parts) < 3:
        return None, error
    count = _nonnegative_int(parts[0])
    total = _nonnegative_int(parts[1])
    largest = _nonnegative_int(parts[2])
    if total <= 0:
        return None, error
    command = parts[3] if len(parts) > 3 else ""
    pid = parts[4] if len(parts) > 4 else ""
    path = parts[5] if len(parts) > 5 else ""
    return {
        "kind": "deleted_open_files",
        "severity": "warn",
        "title": "Deleted files still held open",
        "detail": f"{count} deleted open file(s) still occupy {_human_bytes(total)}.",
        "items": [{
            "label": f"{command} pid {pid}".strip(),
            "detail": f"largest {_human_bytes(largest)} · {path}".strip(),
            "size_bytes": largest,
        }] if largest else [],
    }, error


def _disk_triage_journal(resource: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    script = "if command -v journalctl >/dev/null 2>&1; then journalctl --disk-usage 2>/dev/null | sed -n '1p'; fi\n"
    stdout, error = _disk_triage_run(resource, script)
    line = next((item.strip() for item in stdout.splitlines() if item.strip()), "")
    if not line:
        return None, error
    size = _human_size_to_bytes(line)
    if size < 1024 ** 3:
        return None, error
    return {
        "kind": "journal_usage",
        "severity": "info",
        "title": "System journal is sizable",
        "detail": line,
        "size_bytes": size,
    }, error


def _disk_triage_docker(resource: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    script = "if command -v docker >/dev/null 2>&1; then docker system df 2>/dev/null | sed -n '1,6p'; fi\n"
    stdout, error = _disk_triage_run(resource, script)
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None, error
    return {
        "kind": "docker_usage",
        "severity": "info",
        "title": "Docker has reclaimable storage data",
        "detail": " | ".join(lines[:4]),
    }, error


def _disk_triage_large_files(resource: dict[str, Any], roots: list[str]) -> tuple[dict[str, Any] | None, str]:
    threshold_mb = max(1, DISK_TRIAGE_LARGE_FILE_BYTES // (1024 * 1024))
    limit = max(1, min(50, DISK_TRIAGE_MAX_LARGE_FILES))
    script = r"""
threshold="$1"
limit="$2"
shift 2
for root in "$@"; do
  if [ -d "$root" ]; then
    find "$root" -xdev -type f -size +"${threshold}"M -printf '%s\t%TY-%Tm-%Td\t%p\n' 2>/dev/null
  fi
done | sort -nr | head -n "$limit"
"""
    stdout, error = _disk_triage_run(resource, script, [str(threshold_mb), str(limit), *roots])
    items = []
    for line in stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        size = _nonnegative_int(parts[0])
        if size <= 0:
            continue
        items.append({
            "label": parts[2],
            "detail": f"{_human_bytes(size)} · modified {parts[1]}",
            "size_bytes": size,
        })
    if not items:
        return None, error
    largest = items[0]
    return {
        "kind": "large_files",
        "severity": "info",
        "title": f"Large files over {_human_bytes(DISK_TRIAGE_LARGE_FILE_BYTES)}",
        "detail": f"{len(items)} candidate file(s). Largest: {largest.get('detail')}",
        "items": items,
    }, error


def _disk_triage_roots(resource: dict[str, Any], filesystems: list[dict[str, Any]]) -> list[str]:
    roots: list[str] = []
    for path in resource.get("paths") or []:
        text = str(path or "").strip()
        if text and text not in roots:
            roots.append(text)
    for item in filesystems:
        for mount in item.get("mounts") or []:
            text = str(mount or "").strip()
            if text and text not in roots:
                roots.append(text)
    return roots[:8]


def _disk_triage_recommendations(filesystems: list[dict[str, Any]], targets: list[dict[str, Any]], findings: list[dict[str, Any]]) -> list[str]:
    recommendations = []
    kinds = {str(item.get("kind") or "") for item in findings}
    if "deleted_open_files" in kinds:
        recommendations.append("有 deleted-but-open 文件时，优先重启或平滑重载持有文件的进程；这类空间通常不会通过删除目录释放。")
    if "journal_usage" in kinds:
        recommendations.append("journal 占用较大时，可先设定保留上限，例如 `journalctl --vacuum-size=2G`，再配置长期日志上限。")
    if "docker_usage" in kinds:
        recommendations.append("Docker 占用需要先确认镜像和 volume 是否仍被实验使用，再执行 prune 类清理。")
    if "large_files" in kinds:
        if targets:
            recommendations.append("大文件候选优先检查 checkpoint、数据副本、日志归档和缓存；确认不用后迁移到外接盘或归档盘。")
        else:
            recommendations.append("大文件候选优先检查 checkpoint、数据副本、日志归档和缓存；未检测到外接盘时先不要批量迁移。")
    if not recommendations and any(str(item.get("status") or "") in {"warn", "critical"} for item in filesystems):
        recommendations.append("未发现明确快速清理项；下一步再手动触发目录级扫描，或先检查最近实验输出目录。")
    return recommendations


def _disk_suggestions(filesystems: list[dict[str, Any]], targets: list[dict[str, Any]]) -> list[str]:
    tight_by_mount: dict[str, dict[str, Any]] = {}
    for item in filesystems:
        if str(item.get("status") or "") not in {"warn", "critical"}:
            continue
        key = str(item.get("mount") or item.get("path") or "")
        existing = tight_by_mount.get(key)
        if existing is None or float(item.get("used_percent") or 0) > float(existing.get("used_percent") or 0):
            tight_by_mount[key] = item
    tight = list(tight_by_mount.values())
    if not tight:
        return []
    suggestions: list[str] = []
    target = next((item for item in targets if _nonnegative_int(item.get("available_bytes")) > 0), None)
    for item in sorted(tight, key=lambda row: float(row.get("used_percent") or 0), reverse=True)[:3]:
        mount = str(item.get("mount") or item.get("path") or "")
        path = mount or str(item.get("path") or "")
        available = _human_bytes(item.get("available_bytes"))
        used_percent = item.get("used_percent")
        if target:
            target_label = str(target.get("label") or target.get("model") or target.get("mount") or "external disk").strip()
            suggestions.append(
                f"{path} 已使用 {used_percent}%，仅余 {available}；可先把历史实验产物、日志、缓存惰性迁移到 {target.get('mount')}（{target_label}，可用 {_human_bytes(target.get('available_bytes'))}），原位置保留软链接或 README。"
            )
        else:
            suggestions.append(
                f"{path} 已使用 {used_percent}%，仅余 {available}；未检测到已挂载外接盘，建议先挂载移动硬盘，再迁移历史产物、日志和缓存，避免移动正在运行的工作目录。"
            )
    return suggestions


def _aggregate_disk_resource_status(filesystems: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in filesystems}
    if "critical" in statuses:
        return "critical"
    if "warn" in statuses:
        return "warn"
    if "ok" in statuses:
        return "ok"
    return "not_checked"


def _refresh_disk_status(resource: dict[str, Any], payload: dict[str, Any], *, include_triage: bool) -> None:
    key = _disk_status_cache_key(resource)

    def publish() -> None:
        _store_payload(key, payload, {}, force=include_triage, refresh_after_seconds=DISK_STATUS_CACHE_SECONDS)

    def stage(name: str, collect, apply) -> str:
        payload["stages"][name] = {"status": "running", "started_at": _now_iso()}
        publish()
        try:
            value, error = collect()
            if error and not value:
                raise RuntimeError(error)
            apply(value)
            state = "partial" if error and value else "error" if error else "ok"
        except Exception as exc:
            error = _safe_error(exc)
            state = "error"
        payload["stages"][name].update(status=state, updated_at=_now_iso())
        if error:
            payload["stages"][name]["error"] = error
        publish()
        return state

    def apply_capacity(rows: list[dict[str, Any]]) -> None:
        if not rows:
            raise RuntimeError("No filesystem data returned")
        payload["filesystems"] = _collapse_df_rows_by_mount(rows)
        payload["status"] = _aggregate_disk_resource_status(payload["filesystems"])
        payload["updated_at"] = _now_iso()
        for field in ("stale", "refresh_error", "error", "warning"):
            payload.pop(field, None)

    try:
        paths = [str(item) for item in (resource.get("paths") or []) if str(item or "").strip()]
        capacity = stage("capacity", lambda: _collect_df_rows(resource, paths), apply_capacity)
        if capacity == "error":
            error = payload["stages"]["capacity"]["error"]
            if payload["filesystems"]:
                payload.update(stale=True, refresh_error=error)
            else:
                payload.update(status="error", error=error)
            return
        stage("transfer_targets", lambda: _collect_transfer_targets(resource), lambda targets: payload.update(transfer_targets=targets))
        filesystems = payload["filesystems"]
        triage = payload["triage"]
        if include_triage:
            for name, finder in (
                ("deleted_open_files", lambda: _disk_triage_deleted_open(resource)),
                ("journal_usage", lambda: _disk_triage_journal(resource)),
                ("docker_usage", lambda: _disk_triage_docker(resource)),
                ("large_files", lambda: _disk_triage_large_files(resource, _disk_triage_roots(resource, filesystems))),
            ):
                if payload["stages"][name]["status"] == "skipped":
                    continue
                stage(name, finder, lambda finding: triage["findings"].append(finding) if finding else None)
                error = payload["stages"][name].get("error")
                if error:
                    triage["errors"].append(error)
                triage["updated_at"] = _now_iso()

        def apply_suggestions(suggestions: list[str]) -> None:
            payload["suggestions"] = suggestions
            if include_triage:
                triage["recommendations"] = _disk_triage_recommendations(filesystems, payload["transfer_targets"], triage["findings"])

        if payload["stages"]["transfer_targets"]["status"] == "ok":
            stage("suggestions", lambda: (_disk_suggestions(filesystems, payload["transfer_targets"]), ""), apply_suggestions)
        else:
            payload["stages"]["suggestions"] = {"status": "skipped", "reason": "External target observation is incomplete"}
    finally:
        for progress in payload["stages"].values():
            if progress["status"] in {"pending", "running"}:
                progress.update(status="skipped", reason="Capacity observation failed")
        payload["refreshing"] = False
        publish()


def _collect_disk_status(
    resource: dict[str, Any],
    force: bool = False,
    *,
    include_triage: bool = False,
) -> dict[str, Any]:
    key = _disk_status_cache_key(resource)
    with _REFRESH_LOCK:
        payload, should_refresh = _cached_payload_state(key)
        if _refresh_in_progress(key):
            return payload if payload is not None else _disk_status_placeholder(resource)
        if payload and not force and not should_refresh and not payload.get("refreshing"):
            return payload
        payload = payload or _disk_status_placeholder(resource)
        payload.update(refreshing=True, transfer_targets=[], suggestions=[])
        payload["triage"] = {
            "mode": "lightweight" if include_triage else "on_demand",
            "findings": [], "recommendations": [], "errors": [],
        }
        payload["stages"] = {
            name: {"status": "pending"}
            for name in ("capacity", "transfer_targets", "deleted_open_files", "journal_usage", "docker_usage", "large_files", "suggestions")
        }
        for name in ("deleted_open_files", "journal_usage", "docker_usage", "large_files"):
            if not include_triage or (name == "large_files" and resource.get("kind") == "ssh"):
                payload["stages"][name] = {"status": "skipped", "reason": "On demand" if not include_triage else "Remote scan disabled"}
        snapshot = deepcopy(_store_payload(key, payload, {}, force=force, refresh_after_seconds=DISK_STATUS_CACHE_SECONDS))
        _schedule_background_refresh(key, lambda: _refresh_disk_status(resource, payload, include_triage=include_triage))
        return snapshot


def _aggregate_disk_resources(resources: list[dict[str, Any]]) -> dict[str, Any]:
    checked = [item for item in resources if str(item.get("status") or "") not in {"not_checked", "loading"}]
    statuses = {str(item.get("status") or "") for item in checked}
    if "critical" in statuses:
        status = "critical"
    elif "warn" in statuses:
        status = "warn"
    elif "error" in statuses and len(statuses) == 1:
        status = "error"
    elif "error" in statuses:
        status = "partial"
    elif checked:
        status = "ok"
    else:
        status = "loading"
    filesystems = [
        {**dict(item), "resource_id": resource.get("resource_id"), "resource_name": resource.get("display_name")}
        for resource in resources
        for item in (resource.get("filesystems") if isinstance(resource.get("filesystems"), list) else [])
        if isinstance(item, dict)
    ]
    targets = [
        {**dict(item), "resource_id": resource.get("resource_id"), "resource_name": resource.get("display_name")}
        for resource in resources
        for item in (resource.get("transfer_targets") if isinstance(resource.get("transfer_targets"), list) else [])
        if isinstance(item, dict)
    ]
    triage_modes = {
        str((resource.get("triage") or {}).get("mode") or "")
        for resource in resources
        if isinstance(resource.get("triage"), dict)
    }
    return {
        "status": status,
        "refreshing": any(resource.get("refreshing") for resource in resources),
        "updated_at": _now_iso(),
        "ttl_seconds": DISK_STATUS_CACHE_SECONDS,
        "resources": resources,
        "filesystems": filesystems,
        "transfer_targets": targets,
        "suggestions": [
            suggestion
            for resource in resources
            for suggestion in (resource.get("suggestions") if isinstance(resource.get("suggestions"), list) else [])
        ],
        "triage": {
            "mode": "lightweight" if "lightweight" in triage_modes else "on_demand",
            "updated_at": _now_iso(),
            "findings": [
                finding
                for resource in resources
                for finding in ((resource.get("triage") or {}).get("findings") if isinstance(resource.get("triage"), dict) else [])
            ],
            "recommendations": [
                recommendation
                for resource in resources
                for recommendation in ((resource.get("triage") or {}).get("recommendations") if isinstance(resource.get("triage"), dict) else [])
            ],
        },
    }


def get_disk_resource_status(*, force_refresh: bool = False, resource_id: str = "") -> dict[str, Any]:
    selected = _disk_resource_by_id(resource_id) if resource_id else None
    resources = [selected] if selected else list(DISK_RESOURCE_POOL)
    resources = [item for item in resources if item]
    statuses = [
        _collect_disk_status(
            resource,
            force=force_refresh,
            include_triage=force_refresh,
        )
        for resource in resources
    ]
    if selected:
        return statuses[0]
    return _aggregate_disk_resources(statuses)


def _aggregate_gpu_devices(servers: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(_nonnegative_int(item.get("gpu_count")) for item in servers)
    available = sum(_nonnegative_int(item.get("available_gpu_count")) for item in servers)
    empty = sum(_nonnegative_int(item.get("empty_gpu_count")) for item in servers)
    idle = sum(_nonnegative_int(item.get("idle_gpu_count")) for item in servers)
    busy = sum(_nonnegative_int(item.get("busy_gpu_count")) for item in servers)
    unknown = sum(_nonnegative_int(item.get("unknown_gpu_count")) for item in servers)
    checked = [item for item in servers if str(item.get("status") or "") not in {"not_checked", "loading"}]
    errors = [item for item in servers if str(item.get("status") or "") == "error"]
    if checked and not errors:
        status = "ok"
    elif checked:
        status = "partial"
    elif errors:
        status = "error"
    else:
        status = "not_checked"
    gpus = []
    for server in servers:
        for gpu in server.get("gpus") if isinstance(server.get("gpus"), list) else []:
            if isinstance(gpu, dict):
                item = dict(gpu)
                item["server_name"] = server.get("display_name")
                gpus.append(item)
    return {
        "status": status,
        "updated_at": _now_iso(),
        "ttl_seconds": GPU_STATUS_CACHE_SECONDS,
        "gpu_count": total,
        "available_gpu_count": available,
        "busy_gpu_count": busy,
        "unknown_gpu_count": unknown,
        "empty_gpu_count": empty,
        "idle_gpu_count": idle,
        "servers": servers,
        "gpus": gpus,
    }


def get_gpu_device_status(*, force_refresh: bool = False, device_id: str = "") -> dict[str, Any]:
    records = _gpu_devices()
    selected = _gpu_target(device_id, records) if device_id else None
    if device_id and not selected:
        return {
            "status": "error",
            "error": "unknown GPU device",
            "device_id": str(device_id),
            "known_device_ids": [str(item.get("device_id") or "") for item in records],
        }
    servers = [selected[0]] if selected else _gpu_servers(records)
    statuses = [
        _collect_gpu_status(server, force=force_refresh, records=records)
        for server in servers
    ]
    if selected:
        server_status = statuses[0]
        gpu_device = selected[1]
        if gpu_device is None:
            return server_status
        selected_gpu = next(
            (
                item
                for item in server_status.get("gpus") or []
                if item.get("device_id") == gpu_device.get("device_id")
            ),
            None,
        )
        if selected_gpu is not None:
            return {**selected_gpu, "status": selected_gpu.get("state")}
        return {
            "status": server_status.get("status") or "not_checked",
            "device_id": gpu_device.get("device_id"),
            "device_kind": "gpu",
            "parent_device_id": gpu_device.get("parent_device_id"),
            "index": gpu_device.get("gpu_index"),
            "uuid": gpu_device.get("gpu_uuid"),
            "name": gpu_device.get("model"),
            "updated_at": server_status.get("updated_at") or "",
            "error": server_status.get("error") or "",
        }
    return _aggregate_gpu_devices(statuses)


def _cap_token_map(values: dict[str, Any], limit: int) -> dict[str, int]:
    normalized = {
        str(key): _nonnegative_int(value)
        for key, value in values.items()
        if str(key or "") and _nonnegative_int(value)
    }
    raw_total = sum(normalized.values())
    limit = _nonnegative_int(limit)
    if raw_total <= limit:
        return normalized
    if not limit:
        return {}

    scaled = {
        key: (value * limit) // raw_total
        for key, value in normalized.items()
    }
    remainder = limit - sum(scaled.values())
    ranked = sorted(
        normalized,
        key=lambda key: (normalized[key] * limit) % raw_total,
        reverse=True,
    )
    for key in ranked[:remainder]:
        scaled[key] += 1
    return {key: value for key, value in scaled.items() if value}


def _account_daily_supplement(
    resident: dict[str, Any], accounts: list[dict[str, Any]],
) -> tuple[dict[str, int], bool]:
    """Reconcile settled native account totals against the inclusive ledger."""
    by_provider = resident.get("daily_by_provider_utc")
    if not isinstance(by_provider, dict):
        raise ValueError("Execution usage is missing the daily provider breakdown")
    settled_before = _date_today() - timedelta(days=1)
    account_days: dict[tuple[str, str], int] = {}
    complete = True
    for account in accounts:
        provider = str(account.get("provider") or "")
        usage = account.get("usage") or {}
        buckets = usage.get("daily_buckets")
        if not provider or not isinstance(buckets, list):
            complete = False
            continue
        if usage.get("status") != "ok":
            complete = False
        daily: dict[str, int] = {}
        for bucket in buckets:
            try:
                day = date.fromisoformat(str(bucket.get("date") or ""))
            except (AttributeError, ValueError):
                complete = False
                continue
            if day < settled_before:
                daily[day.isoformat()] = _nonnegative_int(bucket.get("tokens"))
        for key, tokens in daily.items():
            account_days[(key, provider)] = account_days.get((key, provider), 0) + tokens
    # The historical compact ledger has only a provider dimension. Subtract
    # each provider once after pooling its accounts, including private sessions.
    supplement: dict[str, int] = {}
    for (day, provider), tokens in account_days.items():
        gap = max(0, tokens - _nonnegative_int((by_provider.get(day) or {}).get(provider)))
        supplement[day] = supplement.get(day, 0) + gap
    return {key: tokens for key, tokens in supplement.items() if tokens}, complete


def _daily_usage_sources(resident: dict[str, Any], day: date) -> dict[str, Any]:
    """Execution tokens plus the native account gap (only the view with cache has one)."""
    key = day.isoformat()
    assigned = _nonnegative_int((resident.get("daily_assigned") or {}).get(key))
    others = _nonnegative_int((resident.get("daily_others") or {}).get(key))
    others += _nonnegative_int((resident.get("daily_account_supplement") or {}).get(key))
    return {
        "total_tokens": assigned + others,
        "assigned_tokens": assigned,
        "other_tokens": others,
        "by_resident": {
            resident_id: _nonnegative_int(tokens)
            for resident_id, tokens in ((resident.get("daily_by_resident") or {}).get(key) or {}).items()
        },
    }


def _resident_usage_ranking(resident: dict[str, Any]) -> list[dict[str, Any]]:
    starts = usage_read_model.period_starts(_date_today())
    items: dict[str, dict[str, Any]] = {}
    for item in resident.get("residents") or []:
        resident_id = str(item.get("resident_id") or "")
        items[resident_id] = {**item, "periods": dict.fromkeys(starts, 0)}
    days = resident.get("daily_by_resident") or {}
    for key in sorted(days):
        day = date.fromisoformat(key)
        if not min(starts.values()) <= day <= _date_today():
            continue
        for resident_id, tokens in _daily_usage_sources(resident, day)["by_resident"].items():
            if resident_id in items:
                for period, start in starts.items():
                    if day >= start:
                        items[resident_id]["periods"][period] += tokens
    return sorted(
        (item for item in items.values() if any(item["periods"].values())),
        key=lambda item: item["periods"]["year"], reverse=True,
    )


def _make_token_windows(resident: dict[str, Any]) -> dict[str, Any]:
    today = _date_today()
    starts = usage_read_model.period_starts(today)
    out = {}
    for key, start in starts.items():
        totals = {
            "total_tokens": 0,
            "assigned_tokens": 0,
            "other_tokens": 0,
        }
        day = start
        while day <= today:
            merged = _daily_usage_sources(resident, day)
            for field in totals:
                totals[field] += merged[field]
            day += timedelta(days=1)
        out[key] = totals
    return out


def _resident_title_map(resident: dict[str, Any]) -> dict[str, str]:
    titles = {
        str(resident_id): str(title or resident_id)
        for resident_id, title in (resident.get("resident_titles") or {}).items()
        if str(resident_id or "")
    }
    titles.update({
        str(item.get("resident_id") or ""): str(item.get("title") or item.get("resident_id") or "")
        for item in resident.get("residents", [])
        if isinstance(item, dict)
        if str(item.get("resident_id") or "")
    })
    return titles


def _top_resident_keys(points: list[dict[str, Any]], limit: int = 3) -> list[str]:
    totals: dict[str, int] = {}
    for point in points:
        by_resident = point.get("by_resident") if isinstance(point.get("by_resident"), dict) else {}
        for resident_id, value in by_resident.items():
            totals[str(resident_id)] = totals.get(str(resident_id), 0) + _nonnegative_int(value)
    return [key for key, _ in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]]


def _segment_point(point: dict[str, Any], top_keys: list[str], titles: dict[str, str]) -> dict[str, Any]:
    raw_by_resident = point.get("by_resident") if isinstance(point.get("by_resident"), dict) else {}
    total = _nonnegative_int(point.get("tokens"))
    assigned_total = min(
        _nonnegative_int(point.get("assigned_tokens", sum(
            _nonnegative_int(value) for value in raw_by_resident.values()
        ))),
        total,
    )
    by_resident = _cap_token_map(raw_by_resident, assigned_total)
    segments = []
    assigned_top = 0
    for resident_id, value in by_resident.items():
        tokens = _nonnegative_int(value)
        if resident_id in top_keys and tokens:
            assigned_top += tokens
            segments.append({
                "key": resident_id,
                "label": titles.get(resident_id) or resident_id,
                "tokens": tokens,
            })
    other_residents = assigned_total - assigned_top
    if other_residents:
        segments.append({"key": "other_residents", "label": "Other resident(s)", "tokens": other_residents})
    unregistered = total - assigned_total
    if unregistered:
        segments.append({"key": "unregistered", "label": "Unregistered session(s)", "tokens": unregistered})
    return {
        "date": str(point.get("date") or ""),
        "label": str(point.get("label") or point.get("date") or ""),
        "tokens": total,
        "assigned_tokens": assigned_total,
        "other_tokens": unregistered,
        "segments": segments,
    }


# Each grouping above a Resident: its Resident -> group membership and group titles.
USAGE_GROUPINGS = {
    "lineage": ("resident_lineages", "lineage_titles"),
    "project": ("resident_projects", "project_titles"),
}


def _group_token_map(tokens: dict[str, int], resident: dict[str, Any], group_by: str) -> dict[str, int]:
    membership_key, titles_key = USAGE_GROUPINGS[group_by]
    membership = resident.get(membership_key) or {}
    titles = resident.get(titles_key) or {}
    grouped: dict[str, int] = {}
    for resident_id, value in tokens.items():
        group_id = membership.get(resident_id)
        if group_id and group_id in titles:
            grouped[group_id] = grouped.get(group_id, 0) + _nonnegative_int(value)
    return grouped


def _group_usage_ranking(residents: list[dict[str, Any]], resident: dict[str, Any], group_by: str) -> list[dict[str, Any]]:
    titles = resident.get(USAGE_GROUPINGS[group_by][1]) or {}
    groups: dict[str, dict[str, Any]] = {}
    for period in ("today", "week", "month", "year"):
        tokens = {item["resident_id"]: item["periods"].get(period, 0) for item in residents}
        for group_id, value in _group_token_map(tokens, resident, group_by).items():
            item = groups.setdefault(group_id, {
                f"{group_by}_id": group_id,
                "title": titles[group_id],
                "periods": {},
            })
            item["periods"][period] = value
    return list(groups.values())


def _build_chart_series(resident: dict[str, Any], *, group_by: str = "resident") -> dict[str, list[dict[str, Any]]]:
    titles = _resident_title_map(resident)

    def daily_points(start: date) -> list[dict[str, Any]]:
        days = max(1, (_date_today() - start).days + 1)
        points = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            key = day.isoformat()
            merged = _daily_usage_sources(resident, day)
            points.append({
                "date": key,
                "label": key,
                "tokens": merged["total_tokens"],
                "assigned_tokens": merged["assigned_tokens"],
                "unregistered_tokens": merged["other_tokens"],
                "by_resident": merged["by_resident"],
            })
        return points

    def monthly_points(start: date) -> list[dict[str, Any]]:
        months: dict[str, dict[str, Any]] = {}
        for point in daily_points(start):
            month_key = str(point.get("date") or "")[:7]
            month = months.setdefault(month_key, {
                "date": month_key,
                "label": month_key,
                "tokens": 0,
                "assigned_tokens": 0,
                "unregistered_tokens": 0,
                "by_resident": {},
            })
            month["tokens"] += _nonnegative_int(point.get("tokens"))
            month["assigned_tokens"] += _nonnegative_int(point.get("assigned_tokens"))
            month["unregistered_tokens"] += _nonnegative_int(
                point.get("unregistered_tokens")
            )
            for resident_id, value in (point.get("by_resident") or {}).items():
                month["by_resident"][resident_id] = (
                    month["by_resident"].get(resident_id, 0)
                    + _nonnegative_int(value)
                )
        return list(months.values())

    hourly_points = []
    for hour in range(24):
        key = f"{hour:02d}:00"
        by_resident = (resident.get("hourly_today_by_resident") or {}).get(key) or {}
        assigned = max(
            sum(_nonnegative_int(value) for value in by_resident.values()),
            _nonnegative_int((resident.get("hourly_today_assigned") or {}).get(key)),
        )
        tracked_other = _nonnegative_int((resident.get("hourly_today_others") or {}).get(key))
        hourly_points.append({
            "date": f"{_date_today().isoformat()} {key}",
            "label": key,
            "tokens": assigned + tracked_other,
            "assigned_tokens": assigned,
            "unregistered_tokens": tracked_other,
            "by_resident": by_resident,
        })

    starts = usage_read_model.period_starts(_date_today())
    raw = {
        "today": hourly_points,
        "week": daily_points(starts["week"]),
        "month": daily_points(starts["month"]),
        "year": monthly_points(starts["year"]),
    }
    out: dict[str, list[dict[str, Any]]] = {}
    if group_by in USAGE_GROUPINGS:
        titles = resident.get(USAGE_GROUPINGS[group_by][1]) or {}
    for key, points in raw.items():
        if group_by in USAGE_GROUPINGS:
            points = [{
                **point,
                "by_resident": _group_token_map(point["by_resident"], resident, group_by),
            } for point in points]
        top_keys = _top_resident_keys(points, limit=CHART_TOP_RESIDENT_LIMIT)
        out[key] = [_segment_point(point, top_keys, titles) for point in points]
    return out


def _complete_today_bucket_groups(resident: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Reuse the authorized hourly projection before chart top-N truncation."""
    start_value = resident.get("today_start")
    if not start_value:
        # No observed scope means no evidence, rather than a fabricated zero day.
        return {"resident": [], **{group_by: [] for group_by in USAGE_GROUPINGS}}
    start = datetime.fromisoformat(start_value)
    buckets = {"resident": {}, **{group_by: {} for group_by in USAGE_GROUPINGS}}
    for hour in range(24):
        key = f"{hour:02d}:00"
        bucket = (start + timedelta(hours=hour)).isoformat(timespec="seconds")
        visible = (resident.get("hourly_today_by_resident") or {}).get(key) or {}
        total = (_nonnegative_int((resident.get("hourly_today_assigned") or {}).get(key))
                 + _nonnegative_int((resident.get("hourly_today_others") or {}).get(key)))
        named_by_kind = {"resident": visible, **{group_by: _group_token_map(visible, resident, group_by)
                                                 for group_by in USAGE_GROUPINGS}}
        for kind, named in named_by_kind.items():
            groups = {rid: _nonnegative_int(value) for rid, value in named.items()}
            anonymous = total - sum(groups.values())
            if anonymous:
                groups[""] = anonymous
            buckets[kind][bucket] = groups
    return {kind: usage_read_model.bucket_groups(values) for kind, values in buckets.items()}


def _token_usage_payload(
    resident: dict[str, Any],
    *,
    include_cached_input: bool,
) -> dict[str, Any]:
    windows = _make_token_windows(resident)
    residents = _resident_usage_ranking(resident)
    return {
        "windows": windows,
        "chart_series": _build_chart_series(resident),
        "lineage_chart_series": _build_chart_series(resident, group_by="lineage"),
        "project_chart_series": _build_chart_series(resident, group_by="project"),
        "bucket_groups": _complete_today_bucket_groups(resident),
        "residents": residents,
        "lineages": _group_usage_ranking(residents, resident, "lineage"),
        "projects": _group_usage_ranking(residents, resident, "project"),
        "status": str(resident.get("status") or "ok"),
        "error": str(resident.get("error") or ""),
        "refreshing": bool(resident.get("refreshing")),
        "updated_at": str(resident.get("updated_at") or ""),
        "include_cached_input": include_cached_input,
        "source": "execution_ledger_and_account_daily",
        "providers": resident.get("providers") or [],
    }


def _agent_status_summary_cache_key(actor: str) -> str:
    return f"agent_status_summary:v10:{PORTAL_INSTANCE}"


def _token_usage_cache_key(actor: str) -> str:
    return f"token_usage:v13:{PORTAL_INSTANCE}:{actor or 'anonymous'}"


def _build_token_usage_summary(actor: str) -> dict[str, Any]:
    collection_started_at = usage_read_model.collection_time()
    resident_usage = get_society_service().projection.collect_resident_usage(actor=actor)
    if resident_usage.get("status") == "error":
        raise RuntimeError(resident_usage.get("error") or "Resident usage unavailable")
    resident_usage_without_cache = resident_usage.get("excluding_cached_input")
    if not isinstance(resident_usage_without_cache, dict):
        raise ValueError("Execution usage is missing the non-cache variant")
    account_usage = ExecutionService().account_usage()
    accounts = account_usage.get("accounts", [])
    def payload(source, supplement, complete=True):
        shared = {
            "status": str(resident_usage.get("status") or "ok"),
            "error": str(resident_usage.get("error") or ""),
            "refreshing": bool(resident_usage.get("refreshing")),
            "updated_at": str(resident_usage.get("updated_at") or ""),
        }
        variants = {
            "including_cached_input": _token_usage_payload(
                {**source, **shared, "status": shared["status"] if complete else "partial",
                 "daily_account_supplement": supplement}, include_cached_input=True,
            ),
            # A native account's daily total includes cached input and cannot
            # be split, so the view without cache shows only the ledger.
            "excluding_cached_input": {
                **_token_usage_payload(
                    {**source.get("excluding_cached_input", {}), **shared, "daily_account_supplement": {}},
                    include_cached_input=False,
                ),
                "account_totals_omitted": bool(supplement),
            },
        }
        return {**variants["including_cached_input"], "variants": variants}

    supplement, complete = _account_daily_supplement(resident_usage, accounts)
    result = payload(resident_usage, supplement, complete)
    scopes = {(item["provider"], item["account_id"]): item for item in resident_usage.get("scopes", [])}
    catalog = {(str(a.get("provider") or ""), str(a.get("account_id") or "")) for a in account_usage.get("catalog", accounts)}
    catalog.update((provider, account) for provider, account in scopes if account)
    providers = sorted({provider for provider, _ in scopes} | {provider for provider, _ in catalog if provider})
    empty = {"daily_by_provider_utc": {}, "today_start": resident_usage.get("today_start"),
             "excluding_cached_input": {"today_start": resident_usage.get("today_start")}}
    projected = []
    for provider in providers:
        source = scopes.get((provider, None), empty)
        native_accounts = [a for a in accounts if a.get("provider") == provider]
        gap, provider_complete = _account_daily_supplement(source, native_accounts)
        projected.append({"provider": provider, "account_id": None, **payload(source, gap, provider_complete)})
        unknown = scopes.get((provider, ""), empty)
        has_unknown = bool(unknown.get("daily_tracked"))
        if has_unknown:
            projected.append({"provider": provider, "account_id": "", **payload(unknown, {})})
        for owner, account in sorted(catalog):
            if owner == provider and account:
                account_source = scopes.get((provider, account), empty)
                account_gap, account_complete = _account_daily_supplement(
                    account_source, [item for item in native_accounts if item.get("account_id") == account],
                )
                projected.append({"provider": provider, "account_id": account,
                                  "account_history_incomplete": has_unknown,
                                  **payload(account_source, account_gap, account_complete)})
    result["filters"] = {
        "backends": providers,
        "accounts": [{"provider": provider, "account_id": account} for provider, account in sorted(catalog) if provider and account],
    }
    result["scopes"] = projected
    result["incomplete_accounts"] = _incomplete_usage_accounts(accounts)
    result["collection_started_at"] = collection_started_at
    result["collection_finished_at"] = usage_read_model.collection_time()
    result["collection_windows"] = resident_usage.get("collection_windows") or []
    return result


def _incomplete_usage_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Native account usage that is not current, with the type of its last read failure."""
    items = []
    for account in accounts:
        usage = account.get("usage") if isinstance(account.get("usage"), dict) else {}
        status = str(usage.get("status") or "unknown")
        if status == "ok":
            continue
        error = usage.get("error") if isinstance(usage.get("error"), dict) else {}
        items.append({
            "provider": str(account.get("provider") or ""),
            "account_id": str(account.get("account_id") or ""),
            "status": status,
            "error_code": str(error.get("code") or ""),
            "observed_at": usage.get("observed_at") or 0,
        })
    return items


def _incomplete_usage_message(accounts: list[dict[str, Any]]) -> str:
    named = [
        f"{item['account_id'] or item['provider']}（{item['status']}{'，' + item['error_code'] if item['error_code'] else ''}）"
        for item in accounts
    ]
    return ("部分账号的原生用量未更新：" + "、".join(named) if named else "部分账号的原生用量未更新") + "；其余数据已刷新"


def _refresh_token_usage_summary(actor: str, *, force_refresh: bool) -> dict[str, Any]:
    cache_key = _token_usage_cache_key(actor)
    entry = token_usage_snapshot_cache.read(STATUS_CACHE_PATH, cache_key)
    previous = entry.get("payload") or {}
    error = ""
    try:
        fresh = dict(_build_token_usage_summary(actor))
        # One account whose native usage cannot be read (an expired login,
        # say) must not freeze everyone's usage: the ledger and the other
        # accounts are current, so the new summary is shown with a warning that
        # names the account.  Only a summary that cannot be built at all keeps
        # the previous snapshot.
        if fresh.get("status") != "ok":
            fresh["warning"] = _incomplete_usage_message(fresh.get("incomplete_accounts") or [])
    except Exception as exc:
        fresh = {}
        error = _safe_error(exc)
    if error:
        fresh = {
            **previous,
            "snapshot_version": int(previous.get("snapshot_version") or 0),
            "status": str(previous.get("status") or "error"),
            "refresh_error": error,
        }
    else:
        fresh["snapshot_version"] = max(
            int(_now_ts() * 1000),
            int(previous.get("snapshot_version") or 0) + 1,
        )
    now = _now_ts()
    fresh["updated_at"] = fresh.get("updated_at") or _now_iso()
    token_usage_snapshot_cache.write(STATUS_CACHE_PATH, cache_key, {
        "payload": fresh,
        "next_refresh_at": now + (60 if error or fresh.get("status") == "partial" else max(60, RESIDENT_USAGE_REFRESH_SECONDS)),
        "last_forced_at": now if force_refresh else float(entry.get("last_forced_at") or 0),
    })
    return token_usage_snapshot_cache.read(STATUS_CACHE_PATH, cache_key).get("payload") or fresh


def get_token_usage_summary(actor: str = "", force_refresh: bool = False) -> dict[str, Any]:
    actor = str(actor or "")
    cache_key = _token_usage_cache_key(actor)
    entry = token_usage_snapshot_cache.read(STATUS_CACHE_PATH, cache_key)
    payload = entry.get("payload")
    stale = _now_ts() >= float(entry.get("next_refresh_at") or 0)
    retry_after = max(0, MANUAL_REFRESH_MIN_SECONDS - (_now_ts() - float(entry.get("last_forced_at") or 0)))
    force_allowed = force_refresh and not retry_after
    if payload is None or stale or force_allowed:
        _schedule_background_refresh(
            cache_key,
            lambda: _refresh_token_usage_summary(actor, force_refresh=force_allowed),
        )
    # Read after checking the worker, so a completed refresh never returns the old snapshot as settled.
    refreshing = _refresh_in_progress(cache_key)
    payload = token_usage_snapshot_cache.read(STATUS_CACHE_PATH, cache_key).get("payload")
    return {
        **(payload or {"status": "loading", "snapshot_version": 0}),
        "actor": actor,
        "refreshing": refreshing,
        "cache_ttl_seconds": RESIDENT_USAGE_REFRESH_SECONDS,
        "manual_refresh_skipped": bool(force_refresh and retry_after),
        "manual_refresh_retry_after_seconds": int(retry_after) if force_refresh else 0,
    }


def _build_agent_status_summary(actor: str = "", force_refresh: bool = False) -> dict[str, Any]:
    try:
        account_snapshot = ExecutionService().accounts(
            force_refresh=force_refresh,
        )
    except Exception:
        return {"generated_at": _now_iso(), "refreshing": False, "accounts_error": "账号状态暂不可读取"}
    return {
        "generated_at": _now_iso(),
        "accounts": with_account_resources(account_snapshot.get("accounts", []), account_snapshot.get("exchange_rates") or {}),
        "refreshing": bool(account_snapshot.get("refreshing")),
        "refresh_retry_after_seconds": int(account_snapshot.get("refresh_retry_after_seconds") or 0),
    }


def _agent_status_loading_summary(actor: str) -> dict[str, Any]:
    return {
        "generated_at": "",
        "status": "loading",
        "refreshing": True,
        "accounts": [],
    }


def _stale_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The readings a failed read leaves in place, each marked stale."""
    result = []
    for account in accounts:
        item = {**account, "rate_limits": [{**window, "stale": True} for window in account.get("rate_limits") or []]}
        for field in ("balance", "credits"):
            if isinstance(account.get(field), dict):
                item[field] = {**account[field], "stale": True}
        result.append(item)
    return result


def _refresh_agent_status_summary(actor: str, *, force_refresh: bool) -> dict[str, Any]:
    payload = _build_agent_status_summary(actor, force_refresh=force_refresh)
    cache_key = _agent_status_summary_cache_key(actor)
    with _CACHE_LOCK:
        cache = _read_status_cache()
    if "accounts" not in payload:
        entry = cache.get(cache_key) if isinstance(cache.get(cache_key), dict) else {}
        previous = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        payload["accounts"] = _stale_accounts(previous.get("accounts") or [])
    return _store_payload(cache_key, payload, cache, force=force_refresh, refresh_after_seconds=0)


def get_agent_status_summary(actor: str = "", force_refresh: bool = False) -> dict[str, Any]:
    """Read the execution accounts on every request.

    A reading never expires with time; only a failed read marks the readings
    it leaves in place as stale. The read is one local ExecutionService call
    (milliseconds): the reader waits briefly for it, and otherwise gets the
    last result marked ``refreshing``.
    """
    normalized_actor = str(actor or "")
    cache_key = _agent_status_summary_cache_key(normalized_actor)
    _schedule_background_refresh(
        cache_key,
        lambda: _refresh_agent_status_summary(
            normalized_actor,
            force_refresh=force_refresh,
        ),
    ).wait(AGENT_STATUS_SUMMARY_WAIT_SECONDS)
    payload, _due = _cached_payload_state(cache_key)
    result = dict(payload) if payload is not None else _agent_status_loading_summary(normalized_actor)
    result["status"] = str(result.get("status") or "ok")
    result["refreshing"] = bool(result.get("refreshing") or _refresh_in_progress(cache_key))
    return result
