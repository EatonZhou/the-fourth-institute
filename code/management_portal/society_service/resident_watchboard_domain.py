"""Resident domain responsibilities: watchboard."""
from __future__ import annotations

from . import resident_lineage, watchboard_lineage
from .resident_access import ensure_resident_visible_to_actor
from management_common.actor_identity import canonical_actor_id
from pathlib import Path
from typing import Any
import hashlib
import copy
import json
import shutil
import threading
import yaml

from .resident_domain_common import (
    RESIDENT_MEDIA_KINDS,
)

_WATCHBOARD_YAML_CACHE_LOCK = threading.RLock()
_WATCHBOARD_YAML_CACHE: dict[str, dict[str, Any]] = {}
_WATCHBOARD_YAML_CACHE_MAX_ENTRIES = 64


WATCHBOARD_TEXT_LIMIT = 120_000


WATCHBOARD_VIEW_SCHEMA = "tfi.resident_watchboard.v1"


def _has_watchboard(record: dict[str, Any]) -> bool:
    return watchboard_lineage.installed(record)


def _watchboard_root(record: dict[str, Any]) -> Path:
    return watchboard_lineage.root(record).resolve(strict=False)


def _watchboard_agent_manifest_rel(record: dict[str, Any]) -> str:
    return "agent.yaml"


def inherit_watchboard(source: dict[str, Any], child: dict[str, Any]) -> None:
    """Give the child its own script tree, independently of source relocation."""
    if not _has_watchboard(source):
        return
    source_root = _watchboard_root(source)
    target_root = watchboard_lineage.root(child)
    if not target_root.exists() and source_root.is_dir():
        shutil.copytree(source_root, target_root, symlinks=True)
    # Independent files; the child installs its own graph through ISA.


def _watchboard_agent_manifest_path(record: dict[str, Any]) -> Path:
    return (_watchboard_root(record) / _watchboard_agent_manifest_rel(record)).resolve(strict=False)


def _watchboard_view_manifest_rel(record: dict[str, Any]) -> str:
    return "watchboard.yaml"


def _watchboard_view_manifest_path(record: dict[str, Any]) -> Path:
    return (_watchboard_root(record) / _watchboard_view_manifest_rel(record)).resolve(strict=False)


def _default_watchboard_agent_manifest(record: dict[str, Any]) -> dict[str, Any]:
    resident_id = str(record.get("resident_id") or "")
    title = str(record.get("title") or resident_id or "Resident Watchboard")
    script_root = str(_watchboard_root(record))
    return {
        "schema": "tfi.resident_watchboard_script.v1",
        "id": resident_id,
        "title": title,
        "kind": "resident_watchboard_script",
        "root": script_root,
        "description": "",
        "entrypoint": "src/main.py",
        "config": "config/config.yaml",
        "status_path": "state/status.json",
        "default_timeout_seconds": 120,
        "execution": {"kind": "process"},
        "commands": {
            "status": {
                "description": "读取脚本侧状态。",
                "risk": "read_only",
                "timeout_seconds": 60,
            },
            "run-once": {
                "description": "执行一次脚本侧工作循环。",
                "risk": "agent_state_write",
                "timeout_seconds": 1800,
            },
        },
    }


def _default_watchboard_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": WATCHBOARD_VIEW_SCHEMA,
        "title": str(record.get("title") or record.get("resident_id") or "Watchboard"),
        "description": "",
        "layout": "generic-three-panel",
    }


def _watchboard_readme_text(record: dict[str, Any]) -> str:
    resident_id = str(record.get("resident_id") or "resident")
    return "\n".join([
        f"# {resident_id} Watchboard",
        "",
        "本目录保存该 Resident 的 Watchboard 展示定义、配置、状态和可运行脚本。",
        "修改前读取 `/share/Skills/create-resident-watchboard/SKILL.md`。",
        "",
        "- `watchboard.yaml` 定义通用展示信息。",
        "- `agent.yaml` 声明配置、状态、命令和运行方式。",
        "- `src/` 保存脚本代码。",
        "- `state/` 保存脚本侧轻量状态。",
        "",
    ])


def _default_watchboard_main_py() -> str:
    return '''#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "state" / "status.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_status() -> dict:
    try:
        payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def write_status(payload: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    partial = STATUS_PATH.with_name(STATUS_PATH.name + ".partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    partial.replace(STATUS_PATH)


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    status = read_status()
    if command == "status":
        status.setdefault("schema", "tfi.resident_watchboard_status.v1")
        status.setdefault("state", "idle")
        status["updated_at"] = now_iso()
        write_status(status)
        print(json.dumps({"schema": "tfi.resident_watchboard_run.v1", "command": command, "status": "success", "summary": status}, ensure_ascii=False))
        return 0
    if command == "run-once":
        status.update({"schema": "tfi.resident_watchboard_status.v1", "state": "idle", "last_run_at": now_iso(), "updated_at": now_iso()})
        write_status(status)
        print(json.dumps({"schema": "tfi.resident_watchboard_run.v1", "command": command, "status": "no_op", "summary": status}, ensure_ascii=False))
        return 0
    print(json.dumps({"schema": "tfi.resident_watchboard_run.v1", "command": command, "status": "failed", "errors": ["unknown command"]}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''


def _yaml_file_signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return int(stat.st_dev), int(stat.st_ino), int(stat.st_mtime_ns), int(stat.st_size)


def _read_watchboard_yaml_cached(path: Path) -> tuple[Any, str]:
    cache_key = str(path)
    for attempt in range(2):
        signature = _yaml_file_signature(path)
        if signature is None:
            with _WATCHBOARD_YAML_CACHE_LOCK:
                _WATCHBOARD_YAML_CACHE.pop(cache_key, None)
            return {}, ""

        with _WATCHBOARD_YAML_CACHE_LOCK:
            cached = _WATCHBOARD_YAML_CACHE.get(cache_key)
            if cached and cached.get("signature") == signature:
                return copy.deepcopy(cached.get("payload")), str(cached.get("error") or "")

        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            error = ""
        except Exception as exc:
            payload = {}
            error = str(exc)

        refreshed_signature = _yaml_file_signature(path)
        if refreshed_signature is None:
            with _WATCHBOARD_YAML_CACHE_LOCK:
                _WATCHBOARD_YAML_CACHE.pop(cache_key, None)
            return {}, ""
        if refreshed_signature != signature and attempt == 0:
            continue
        if refreshed_signature != signature:
            return payload, error

        with _WATCHBOARD_YAML_CACHE_LOCK:
            _WATCHBOARD_YAML_CACHE[cache_key] = {
                "signature": signature,
                "payload": copy.deepcopy(payload),
                "error": error,
            }
            if len(_WATCHBOARD_YAML_CACHE) > _WATCHBOARD_YAML_CACHE_MAX_ENTRIES:
                _WATCHBOARD_YAML_CACHE.pop(next(iter(_WATCHBOARD_YAML_CACHE)))
        return payload, error
    return {}, ""


def _read_watchboard_agent_manifest(record: dict[str, Any]) -> tuple[dict[str, Any], str]:
    path = _watchboard_agent_manifest_path(record)
    payload, error = _read_watchboard_yaml_cached(path)
    if error:
        return {}, error
    if not isinstance(payload, dict):
        return {}, "agent.yaml must be a YAML object"
    commands = payload.get("commands")
    if not isinstance(commands, dict):
        return {}, "agent.yaml commands must be a YAML mapping"
    if commands and not str(payload.get("entrypoint") or "").strip():
        return {}, "agent.yaml requires entrypoint"
    if any(not isinstance(spec, dict) for spec in commands.values()):
        return {}, "agent.yaml command definitions must be YAML objects"
    return payload, ""


def _read_watchboard_view_manifest(record: dict[str, Any]) -> tuple[dict[str, Any], str]:
    path = _watchboard_view_manifest_path(record)
    payload, error = _read_watchboard_yaml_cached(path)
    if error:
        return {}, error
    if not isinstance(payload, dict):
        return {}, "watchboard.yaml must be a YAML object"
    return payload, ""


def _watchboard_config_paths(record: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    from .resident_projection import _safe_relative_path
    paths: list[str] = []
    config = manifest.get("config")
    if isinstance(config, str) and config.strip():
        paths.append(config)
    configs = manifest.get("configs")
    if isinstance(configs, list):
        paths.extend(str(item or "") for item in configs)
    if not paths and "configs" not in manifest:
        paths = ["config/config.yaml"]
    clean: list[str] = []
    for item in paths:
        try:
            rel = _safe_relative_path(item, "")
        except ValueError:
            continue
        if rel and rel not in clean:
            clean.append(rel)
    return clean[:8]


def _watchboard_status_path(record: dict[str, Any], manifest: dict[str, Any]) -> str:
    from .resident_projection import _safe_relative_path
    for value in (
        manifest.get("status_path"),
        "state/status.json",
    ):
        try:
            rel = _safe_relative_path(value, "")
        except ValueError:
            continue
        if rel:
            return rel
    return "state/status.json"


def _watchboard_resolve_rel(record: dict[str, Any], rel_path: str) -> Path:
    from .resident_projection import _safe_relative_path
    root = _watchboard_root(record)
    rel = _safe_relative_path(rel_path, "")
    path = (root / rel).resolve(strict=False)
    try:
        path.relative_to(root)
    except Exception:
        raise ValueError("path outside watchboard workspace")
    return path


def _watchboard_text_file_payload(record: dict[str, Any], rel_path: str, editable: bool) -> dict[str, Any]:
    from .lineage_access_policy import workspace_file_address
    from .resident_workspace_domain import _resident_folder_kind
    path = _watchboard_resolve_rel(record, rel_path)
    exists = path.exists()
    kind = _resident_folder_kind(path) if exists else "missing"
    binary = False
    truncated = False
    content = ""
    mtime_ns = 0
    size = 0
    error = ""
    version = ""
    if exists and path.is_file():
        try:
            stat = path.stat()
            mtime_ns = int(stat.st_mtime_ns)
            size = int(stat.st_size)
            data = path.read_bytes()[: WATCHBOARD_TEXT_LIMIT + 1]
            truncated = len(data) > WATCHBOARD_TEXT_LIMIT
            if not truncated:
                version = hashlib.sha256(data).hexdigest()
            if truncated:
                data = data[:WATCHBOARD_TEXT_LIMIT]
            binary = kind == "binary" or kind in RESIDENT_MEDIA_KINDS or b"\0" in data[:2048]
            if not binary:
                content = data.decode("utf-8", errors="replace")
        except Exception as exc:
            error = str(exc)
    return {
        "path": str(path),
        "rel_path": rel_path,
        # The Workspace address UpdateWorkspaceFile writes; "" when the file is in no registered Workspace.
        "address": workspace_file_address(str(path)),
        "version": version,
        "exists": exists,
        "kind": kind,
        "binary": binary,
        "truncated": truncated,
        "content": content,
        "size": size,
        # Nanosecond timestamps exceed JavaScript's exact integer range.
        "mtime_ns": str(mtime_ns),
        "error": error,
        "editable": bool(editable and exists and path.is_file() and not binary and not truncated and kind in {"markdown", "json", "jsonl", "text"}),
    }


def _watchboard_commands_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    from .resident_lifecycle_domain import _nonnegative_int
    commands = manifest.get("commands") or {}
    manifest_execution = (
        manifest.get("execution")
        if isinstance(manifest.get("execution"), dict)
        else {}
    )
    out: list[dict[str, Any]] = []
    items = commands.items()
    for command_id, spec in items:
        command_id = str(command_id or "").strip()
        if not command_id:
            continue
        args = spec.get("args") if isinstance(spec.get("args"), dict) else spec.get("parameters")
        command_execution = (
            spec.get("execution")
            if isinstance(spec.get("execution"), dict)
            else manifest_execution
        )
        out.append({
            "id": command_id,
            "description": str(spec.get("description") or ""),
            "risk": str(spec.get("risk") or ""),
            "command_kind": str(command_execution.get("kind") or ""),
            "timeout_seconds": _nonnegative_int(spec.get("timeout_seconds"), 0),
            "args_schema": args if isinstance(args, dict) else {},
            "requires_args": bool(args),
        })
    return out[:40]


def _editable_configs(actor: str, configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A config file is editable when UpdateWorkspaceFile would accept its Workspace address from ``actor``."""
    from management_portal import permission_system

    return [{**config, "editable": bool(config["editable"] and config["address"] and permission_system.allows(
        actor, "UpdateWorkspaceFile", {"address": config["address"]}))} for config in configs]


def _watchboard_for_record(
    record: dict[str, Any],
    actor: str = "",
) -> dict[str, Any]:
    from .resident_directory_domain import _resident_status_signature
    from .resident_record_domain import _resident_id
    if not _has_watchboard(record):
        return {"enabled": False}
    from management_portal import permission_system

    actor_id = canonical_actor_id(actor)
    script_root = _watchboard_root(record)
    manifest, manifest_error = _read_watchboard_agent_manifest(record)
    manifest_path = _watchboard_agent_manifest_path(record)
    view_manifest_path = _watchboard_view_manifest_path(record)
    view_manifest, view_manifest_error = _read_watchboard_view_manifest(record)
    config_paths = _watchboard_config_paths(record, manifest)
    configs = _editable_configs(actor_id, [_watchboard_text_file_payload(record, rel, True) for rel in config_paths])
    # Each button asks the instruction it calls: commands run with TriggerEvent on the Resident's Lineage.
    permissions = {
        "can_run": permission_system.allows(actor_id, "TriggerEvent", {"lineage_id": resident_lineage.lineage_id(record)}),
        "can_edit_config": any(config["editable"] for config in configs),
    }
    status_rel = _watchboard_status_path(record, manifest)
    status_file = _watchboard_text_file_payload(record, status_rel, False)
    status_payload: Any = {}
    status_error = status_file.get("error") or ""
    if status_file.get("content") and str(status_file.get("kind") or "") == "json":
        try:
            status_payload = json.loads(str(status_file.get("content") or ""))
        except Exception as exc:
            status_error = str(exc)
    commands = _watchboard_commands_from_manifest(manifest)
    recent_runs = []
    actual_ids = {item["id"] for item in watchboard_lineage.components(record)}
    commands = [{**item, **watchboard_lineage.command_ids(record, item["id"])} for item in commands]
    commands = [item for item in commands if item["event_source_id"] in actual_ids and item["effect_id"] in actual_ids]
    resident_id = _resident_id(record)
    signature = _resident_status_signature({
        "resident_id": resident_id,
        "permissions": permissions,
        "script_root": str(script_root),
        "manifest_mtime": manifest_path.stat().st_mtime_ns if manifest_path.exists() else 0,
        "manifest_error": manifest_error,
        "view_manifest_mtime": view_manifest_path.stat().st_mtime_ns if view_manifest_path.exists() else 0,
        "view_manifest_error": view_manifest_error,
        "configs": [(item.get("rel_path"), item.get("mtime_ns"), item.get("size")) for item in configs],
        "status": (status_file.get("rel_path"), status_file.get("mtime_ns"), status_file.get("size"), status_error),
        "commands": commands,
        "runs": [(item.get("trigger_path_id"), item.get("status"), item.get("updated_at"), item.get("finished_at")) for item in recent_runs],
    })
    return {
        "enabled": True,
        "signature": signature,
        "resident_id": resident_id,
        "script_root": str(script_root),
        "lineage_id": resident_lineage.lineage_id(record),
        "execution_model": "lineage",
        "script_root_exists": script_root.exists(),
        "manifest": {
            "path": str(manifest_path),
            "rel_path": _watchboard_agent_manifest_rel(record),
            "exists": manifest_path.exists(),
            "error": manifest_error,
            "payload": manifest,
        },
        "view_manifest": {
            "path": str(view_manifest_path),
            "rel_path": _watchboard_view_manifest_rel(record),
            "exists": view_manifest_path.exists(),
            "error": view_manifest_error,
            "payload": view_manifest,
        },
        "configs": configs,
        "status": {
            "file": status_file,
            "payload": status_payload if isinstance(status_payload, dict) else {},
            "error": status_error,
        },
        "commands": commands,
        "recent_runs": recent_runs,
        "permissions": permissions,
    }


def _watchboard_capabilities(
    record: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    from management_portal import permission_system

    actor_id = canonical_actor_id(actor)
    installed = _has_watchboard(record)
    # Installing and removing apply the plan with ApplyLineageChanges on the Resident's Lineage.
    can_manage = permission_system.allows(actor_id, "ApplyLineageChanges", {"lineage_id": resident_lineage.lineage_id(record)})
    return {
        "installed": installed,
        "can_install": bool(can_manage and not installed),
        "can_remove": bool(can_manage and installed),
        "can_view": installed,
    }


def get_resident_watchboard(
    resident_id: str,
    actor: str = "",
) -> dict[str, Any]:
    from .resident_directory_domain import _has_resident_workspace_authority
    from .resident_workspace_domain import resolve_resident_runtime_target
    with resident_lineage.access_snapshot_scope():
        target = resolve_resident_runtime_target(resident_id, actor, require_visible=True)
        record = target.record
        actor_id = canonical_actor_id(actor)
        if not _has_resident_workspace_authority(
            record,
            actor_id,
        ):
            raise PermissionError("无权读取该 Resident 的 Watchboard")
        watchboard = _watchboard_for_record(record, actor)
        watchboard["execution_model"] = "lineage"
        watchboard["lineage_id"] = resident_lineage.lineage_id(record)
        watchboard["installation"] = watchboard_lineage.installation_plan(record)
        return {
            "resident_id": target.resident_id,
            "watchboard": watchboard,
            "watchboard_capabilities": _watchboard_capabilities(
                record,
                actor_id,
            ),
        }
