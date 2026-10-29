from __future__ import annotations
from management_common.product_environment import read_product_env, mirror_product_env

from management_common.format_tags import schema_matches
from management_common.portal_paths import path as portal_path
from management_common.site_config import read_site_config
from management_common.clock import utc_now_iso

import copy
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable
from management_portal.resources.portal import portal_url_for_address


from management_portal.resources import get_resource_service
from management_portal.resources.registry_store import announce_registry_changes, get_registry_store
from management_portal.shared_state import atomic_replace_json


REGISTRY_SCHEMA = "tfi.device.v1"
STATUS_SCHEMA = "tfi.device_status.v1"
DEVICE_COLLECTION = "device"
DEFAULT_STATUS_PATH = Path(
    read_product_env(
        "PORTAL_DEVICE_STATUS_PATH",
        str(portal_path("staging_root") / "shared" / "portal_cache" / "devices" / "status.json"),
    )
).expanduser()
DEFAULT_DEVICE_SECRET_ROOT = Path(
    read_product_env(
        "PORTAL_DEVICE_SECRET_ROOT",
        str(portal_path("secrets_dir") / "devices"),
    )
).expanduser()

_RESOURCE_SERVICE = get_resource_service()
_STATUS_LOCK = threading.RLock()
_KIND_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_SSH_HOST_RE = re.compile(r"^(?!-)[A-Za-z0-9_.:@%+\[\]-]+$")
_RECORD_FIELDS = frozenset(
    {
        "schema",
        "device_id",
        "name",
        "description",
        "device_kind",
        "lifecycle",
        "hostname",
        "location",
        "network",
        "access_methods",
        "monitor",
        "parent_device_id",
        "gpu_index",
        "gpu_uuid",
        "model",
        "created_at",
        "updated_at",
    }
)
def _text(value: object) -> str:
    return str(value or "").strip()


def _string_list(value: object, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"device field {field} must be an array")
    output: list[str] = []
    seen: set[str] = set()
    for raw in value:
        clean = _text(raw)
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"device field {field} must be an object")
    return dict(value)


def _device_password_path(
    device_id: str,
    secret_root: str | Path | None = None,
) -> Path:
    clean_id = _text(device_id)
    _RESOURCE_SERVICE.validate_id(clean_id, "device")
    return Path(secret_root or DEFAULT_DEVICE_SECRET_ROOT).expanduser() / f"{clean_id}.ssh-password"


def save_device_ssh_password(
    device_id: str,
    password: str,
    *,
    secret_root: str | Path | None = None,
) -> dict[str, Any]:
    clean_password = str(password or "")
    if not clean_password:
        raise ValueError("SSH password required")
    if len(clean_password) > 1024 or any(char in clean_password for char in "\r\n\0"):
        raise ValueError("SSH password contains unsupported characters")
    path = _device_password_path(device_id, secret_root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(clean_password)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return {"resource_id": _text(device_id), "credential_saved": True}


def delete_device_ssh_password(
    device_id: str,
    *,
    secret_root: str | Path | None = None,
) -> dict[str, Any]:
    path = _device_password_path(device_id, secret_root)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return {
        "resource_id": _text(device_id),
        "credential_saved": False,
        "deleted": existed,
    }


def has_device_ssh_password(
    device_id: str,
    *,
    secret_root: str | Path | None = None,
) -> bool:
    path = _device_password_path(device_id, secret_root)
    return path.is_file() and (path.stat().st_mode & 0o777) == 0o600


def read_device_ssh_password(
    device_id: str,
    *,
    secret_root: str | Path | None = None,
) -> str:
    path = _device_password_path(device_id, secret_root)
    try:
        password = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    if (path.stat().st_mode & 0o077) != 0:
        raise PermissionError("device SSH password file permissions are too broad")
    return password


def _normalize_record(raw: dict[str, Any], document_id: str) -> dict[str, Any]:
    unknown = sorted(set(raw) - _RECORD_FIELDS)
    if unknown:
        raise ValueError(
            f"device registry {document_id} contains unsupported fields: "
            + ", ".join(unknown)
        )
    if not schema_matches(raw.get("schema"), "tfi.device.v1"):
        raise ValueError(f"device registry {document_id} has an invalid schema")

    device_id = _text(raw.get("device_id"))
    _RESOURCE_SERVICE.validate_id(device_id, "device")
    if document_id != device_id:
        raise ValueError(f"device registry document does not match device_id: {document_id}")

    name = _text(raw.get("name"))
    device_kind = _text(raw.get("device_kind"))
    if not name:
        raise ValueError(f"device registry {document_id} has no name")
    if not _KIND_RE.fullmatch(device_kind):
        raise ValueError(f"device registry {document_id} has an invalid device_kind")

    network = _mapping(raw.get("network"), field="network")
    unknown_network = sorted(set(network) - {"primary_ip", "mac_addresses"})
    if unknown_network:
        raise ValueError(
            f"device registry {document_id} contains unsupported network fields: "
            + ", ".join(unknown_network)
        )
    network = {
        "primary_ip": _text(network.get("primary_ip")),
        "mac_addresses": _string_list(
            network.get("mac_addresses"),
            field="network.mac_addresses",
        ),
    }

    access_methods = _mapping(raw.get("access_methods"), field="access_methods")
    unknown_access_methods = sorted(set(access_methods) - {"ssh"})
    if unknown_access_methods:
        raise ValueError(
            f"device registry {document_id} contains unsupported access methods: "
            + ", ".join(unknown_access_methods)
        )
    ssh = _mapping(access_methods.get("ssh"), field="access_methods.ssh")
    unknown_ssh = sorted(
        set(ssh) - {"host", "config_path", "user", "port", "identity_file"}
    )
    if unknown_ssh:
        raise ValueError(
            f"device registry {document_id} contains unsupported SSH fields: "
            + ", ".join(unknown_ssh)
        )
    ssh_port: int | None = None
    if ssh.get("port") not in (None, ""):
        try:
            ssh_port = int(ssh.get("port"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"device registry {document_id} has an invalid SSH port"
            ) from exc
        if not 1 <= ssh_port <= 65535:
            raise ValueError(f"device registry {document_id} has an invalid SSH port")
    ssh_host = _text(ssh.get("host"))
    if ssh_host and not _SSH_HOST_RE.fullmatch(ssh_host):
        raise ValueError(f"device registry {document_id} has an invalid SSH host")
    normalized_ssh = {
        "host": ssh_host,
        "config_path": _text(ssh.get("config_path")),
        "user": _text(ssh.get("user")),
        "port": ssh_port,
        "identity_file": _text(ssh.get("identity_file")),
    }
    if any(value not in ("", None) for value in normalized_ssh.values()) and not ssh_host:
        raise ValueError(
            f"device registry {document_id} configures SSH without a host"
        )
    access_methods = {"ssh": normalized_ssh} if ssh_host else {}

    monitor = _mapping(raw.get("monitor"), field="monitor")
    unknown_monitor = sorted(set(monitor) - {"kind", "expected_gpu_count"})
    if unknown_monitor:
        raise ValueError(
            f"device registry {document_id} contains unsupported monitor fields: "
            + ", ".join(unknown_monitor)
        )
    try:
        expected_gpu_count = int(monitor.get("expected_gpu_count") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"device registry {document_id} has an invalid expected_gpu_count"
        ) from exc
    monitor = {
        "kind": _text(monitor.get("kind")),
        "expected_gpu_count": expected_gpu_count,
    }

    parent_device_id = _text(raw.get("parent_device_id"))
    if parent_device_id:
        _RESOURCE_SERVICE.validate_id(parent_device_id, "device")

    gpu_index: int | None = None
    if raw.get("gpu_index") is not None:
        try:
            gpu_index = int(raw.get("gpu_index"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"device registry {document_id} has an invalid gpu_index") from exc
        if gpu_index < 0:
            raise ValueError(f"device registry {document_id} has a negative gpu_index")

    if device_kind == "gpu_server":
        if parent_device_id or gpu_index is not None or _text(raw.get("gpu_uuid")):
            raise ValueError(f"GPU server {device_id} cannot be a child GPU")
        if monitor["kind"] != "nvidia_smi" or expected_gpu_count < 1:
            raise ValueError(f"GPU server {device_id} has no valid monitor configuration")
        if not access_methods.get("ssh", {}).get("host"):
            raise ValueError(f"GPU server {device_id} has no SSH host")
    elif device_kind == "gpu":
        if not parent_device_id:
            raise ValueError(f"GPU {device_id} has no parent_device_id")
        if gpu_index is None or not _text(raw.get("gpu_uuid")):
            raise ValueError(f"GPU {device_id} has no stable index and UUID")
        if not _text(raw.get("model")):
            raise ValueError(f"GPU {device_id} has no model")
        if monitor["kind"] or expected_gpu_count:
            raise ValueError(f"GPU {device_id} cannot define a server monitor")

    return {
        "schema": REGISTRY_SCHEMA,
        "device_id": device_id,
        "name": name,
        "description": _text(raw.get("description")),
        "device_kind": device_kind,
        "lifecycle": _text(raw.get("lifecycle")) or "active",
        "hostname": _text(raw.get("hostname")),
        "location": _text(raw.get("location")),
        "network": network,
        "access_methods": access_methods,
        "monitor": monitor,
        "parent_device_id": parent_device_id,
        "gpu_index": gpu_index,
        "gpu_uuid": _text(raw.get("gpu_uuid")),
        "model": _text(raw.get("model")),
        "created_at": _text(raw.get("created_at")),
        "updated_at": _text(raw.get("updated_at")),
    }


def _validate_registry(
    documents: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for document_id, raw in sorted(documents.items()):
        try:
            if not isinstance(raw, dict):
                raise ValueError(f"device registry {document_id} must contain an object")
            records.append(_normalize_record(raw, document_id))
        except Exception as exc:
            errors.append({"resource_id": document_id, "error": str(exc)[:1000]})

    by_id: dict[str, dict[str, Any]] = {}
    gpu_slots: set[tuple[str, int]] = set()
    gpu_uuids: set[str] = set()
    for record in records:
        device_id = record["device_id"]
        if device_id in by_id:
            errors.append(
                {
                    "resource_id": device_id,
                    "error": f"duplicate device_id: {device_id}",
                }
            )
            continue
        by_id[device_id] = record

    accepted: list[dict[str, Any]] = []
    for record in by_id.values():
        if record["device_kind"] != "gpu":
            accepted.append(record)
            continue
        parent_id = record["parent_device_id"]
        parent = by_id.get(parent_id)
        if not parent or parent["device_kind"] != "gpu_server":
            errors.append(
                {
                    "resource_id": record["device_id"],
                    "error": (
                        f"GPU {record['device_id']} has an invalid "
                        f"parent_device_id: {parent_id}"
                    ),
                }
            )
            continue
        slot = (parent_id, int(record["gpu_index"]))
        if slot in gpu_slots:
            errors.append(
                {
                    "resource_id": record["device_id"],
                    "error": f"duplicate GPU slot: {parent_id}/gpu/{slot[1]}",
                }
            )
            continue
        gpu_slots.add(slot)
        uuid_key = record["gpu_uuid"].casefold()
        if uuid_key in gpu_uuids:
            errors.append(
                {
                    "resource_id": record["device_id"],
                    "error": f"duplicate GPU UUID: {record['gpu_uuid']}",
                }
            )
            continue
        gpu_uuids.add(uuid_key)
        accepted.append(record)

    for server in (item for item in accepted if item["device_kind"] == "gpu_server"):
        child_count = sum(
            1
            for item in accepted
            if item["parent_device_id"] == server["device_id"]
        )
        expected = int(server["monitor"]["expected_gpu_count"])
        if child_count != expected:
            errors.append(
                {
                    "resource_id": server["device_id"],
                    "error": (
                        f"GPU server {server['device_id']} registers {child_count} "
                        f"GPUs; expected {expected}"
                    ),
                }
            )
    return accepted, errors


def scan_device_records() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    return _validate_registry(get_registry_store().documents(DEVICE_COLLECTION))


def load_device_records() -> list[dict[str, Any]]:
    records, errors = scan_device_records()
    if errors:
        raise ValueError(errors[0]["error"])
    return records


def register_device(
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("device registration payload must be an object")
    name = _text(payload.get("name"))
    device_kind = _text(payload.get("device_kind")) or "device"
    if not name:
        raise ValueError("device name required")
    if not _KIND_RE.fullmatch(device_kind):
        raise ValueError("invalid device kind")
    if device_kind in {"gpu", "gpu_server"}:
        raise ValueError(
            "GPU devices must be registered through the GPU inventory workflow"
        )
    allowed_fields = {
        "name", "description", "device_kind", "lifecycle", "hostname",
        "location", "primary_ip", "access_methods", "model",
    }
    if set(payload) - allowed_fields:
        raise ValueError("device registration payload does not match current contract")
    with get_registry_store().writer(DEVICE_COLLECTION) as writer:
        documents = writer.documents(DEVICE_COLLECTION)
        records, errors = _validate_registry(documents)
        if errors:
            raise ValueError(errors[0]["error"])
        existing_ids = [str(item.get("device_id") or "") for item in records]
        device_id = _RESOURCE_SERVICE.new_id("device", existing_ids)
        now = utc_now_iso()
        record = {
            "schema": REGISTRY_SCHEMA,
            "device_id": device_id,
            "name": name,
            "description": _text(payload.get("description")),
            "device_kind": device_kind,
            "lifecycle": _text(payload.get("lifecycle")) or "active",
            "hostname": _text(payload.get("hostname")),
            "location": _text(payload.get("location")),
            "network": {
                "primary_ip": _text(payload.get("primary_ip")),
                "mac_addresses": [],
            },
            "access_methods": _mapping(
                payload.get("access_methods"),
                field="access_methods",
            ),
            "monitor": {"kind": "", "expected_gpu_count": 0},
            "parent_device_id": "",
            "gpu_index": None,
            "gpu_uuid": "",
            "model": _text(payload.get("model")),
            "created_at": now,
            "updated_at": now,
        }
        normalized = _normalize_record(record, device_id)
        _records, errors = _validate_registry({**documents, device_id: record})
        if errors:
            raise ValueError(errors[0]["error"])
        writer.put(DEVICE_COLLECTION, device_id, record)
    announce_registry_changes(DEVICE_COLLECTION, {device_id: "created"}, resource_type="device")
    return {"id": device_id, **normalized}


def unregister_device(
    device_id: str,
) -> dict[str, Any]:
    clean_id = _text(device_id)
    _RESOURCE_SERVICE.validate_id(clean_id, "device")
    with get_registry_store().writer(DEVICE_COLLECTION) as writer:
        records, errors = _validate_registry(writer.documents(DEVICE_COLLECTION))
        if errors:
            raise ValueError(errors[0]["error"])
        record = next(
            (item for item in records if item["device_id"] == clean_id),
            None,
        )
        if record is None:
            raise KeyError(clean_id)
        if any(item.get("parent_device_id") == clean_id for item in records):
            raise ValueError("cannot unregister a device that still has child devices")
        writer.delete(DEVICE_COLLECTION, clean_id)
        delete_device_ssh_password(clean_id)
    announce_registry_changes(DEVICE_COLLECTION, {clean_id: "deleted"}, resource_type="device")
    return {"id": clean_id, "unregistered": True, "name": record["name"]}


def ssh_access_for_record(record: dict[str, Any]) -> dict[str, Any]:
    """Apply installation-local SSH paths to a device's connection metadata."""
    access_methods = record.get("access_methods") or {}
    ssh = copy.deepcopy(access_methods.get("ssh") or {})
    paths_by_device = read_site_config().get("deployment", {}).get("device_ssh_paths", {})
    if not isinstance(paths_by_device, dict):
        raise ValueError("deployment.device_ssh_paths must be an object")
    device_id = record["device_id"]
    if device_id in paths_by_device:
        paths = paths_by_device[device_id]
        fields = {"config_path", "identity_file"}
        if not isinstance(paths, dict) or set(paths) - fields:
            raise ValueError(f"deployment.device_ssh_paths.{device_id} must contain only SSH paths")
        for field in fields:
            value = paths.get(field, "")
            if not isinstance(value, str) or (value and not Path(value).is_absolute()):
                raise ValueError(f"deployment.device_ssh_paths.{device_id}.{field} must be an absolute path or empty")
            ssh[field] = value
    return ssh


def effective_ssh_access(
    device_id: str,
    *,
    secret_root: str | Path | None = None,
) -> dict[str, Any]:
    records = load_device_records()
    by_id = {item["device_id"]: item for item in records}
    current = by_id.get(_text(device_id))
    if current is None:
        raise KeyError(device_id)
    visited: set[str] = set()
    while current and current["device_id"] not in visited:
        visited.add(current["device_id"])
        ssh = ssh_access_for_record(current)
        if _text(ssh.get("host")):
            via_resource_id = current["device_id"]
            return {
                **copy.deepcopy(ssh),
                "via_resource_id": via_resource_id,
                "credential_saved": has_device_ssh_password(
                    via_resource_id,
                    secret_root=secret_root,
                ),
            }
        current = by_id.get(_text(current.get("parent_device_id")))
    raise ValueError("device has no SSH access method")


def test_ssh_connection(
    device_id: str,
    *,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 45:
        raise ValueError("SSH timeout_seconds must be an integer from 1 to 45")
    ssh = effective_ssh_access(
        device_id,
    )
    timeout = timeout_seconds
    via_resource_id = str(ssh["via_resource_id"])
    password_path = _device_password_path(via_resource_id)
    use_password = bool(ssh["credential_saved"])
    command = ["ssh"]
    config_path = _text(ssh.get("config_path"))
    if config_path:
        command.extend(["-F", str(Path(config_path).expanduser())])
    command.extend(
        [
            "-o",
            f"BatchMode={'no' if use_password else 'yes'}",
            "-o",
            f"ConnectTimeout={timeout}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "NumberOfPasswordPrompts=1",
        ]
    )
    ssh_port = ssh.get("port")
    if ssh_port:
        command.extend(["-p", str(ssh_port)])
    identity_file = _text(ssh.get("identity_file"))
    if identity_file:
        command.extend(["-i", str(Path(identity_file).expanduser())])
    ssh_host = _text(ssh.get("host"))
    ssh_user = _text(ssh.get("user"))
    target = f"{ssh_user}@{ssh_host}" if ssh_user else ssh_host
    started = time.monotonic()
    run_options: dict[str, Any] = {}
    if use_password:
        environment = os.environ.copy()
        environment.update(
            {
                "DISPLAY": environment.get("DISPLAY") or "tfi-portal",
                "SSH_ASKPASS_REQUIRE": "force",
                "SSH_ASKPASS": str(Path(__file__).with_name("ssh_askpass_secret_file.sh")),
            }
        )
        mirror_product_env(environment, "PORTAL_SSH_PASSWORD_FILE", str(password_path))
        mirror_product_env(environment, "PORTAL_PYTHON", sys.executable)
        run_options = {"env": environment, "stdin": subprocess.DEVNULL}
    try:
        completed = subprocess.run(
            [*command, "--", target, "true"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout + 6,
            **run_options,
        )
        reachable = completed.returncode == 0
        message = (
            "SSH 连接成功"
            if reachable
            else _text(completed.stderr).splitlines()[-1][:300]
            if _text(completed.stderr)
            else f"SSH 退出码 {completed.returncode}"
        )
    except subprocess.TimeoutExpired:
        reachable = False
        message = f"SSH 连接在 {timeout} 秒后超时"
    except OSError as exc:
        reachable = False
        message = str(exc)
    return {
        "resource_id": _text(device_id),
        "via_resource_id": via_resource_id,
        "reachable": reachable,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "message": message,
        "tested_at": utc_now_iso(),
    }


def gpu_children(
    parent_device_id: str,
    *,
    records: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    source = list(records) if records is not None else load_device_records()
    return sorted(
        [
            record
            for record in source
            if record["device_kind"] == "gpu"
            and record["parent_device_id"] == parent_device_id
        ],
        key=lambda record: int(record["gpu_index"]),
    )


def _empty_status_store() -> dict[str, Any]:
    return {"schema": STATUS_SCHEMA, "updated_at": "", "servers": {}}


def _read_status_store(status_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(status_path or DEFAULT_STATUS_PATH).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_status_store()
    if not isinstance(payload, dict) or not schema_matches(payload.get("schema"), "tfi.device_status.v1"):
        return _empty_status_store()
    servers = payload.get("servers")
    if not isinstance(servers, dict):
        return _empty_status_store()
    return {
        "schema": STATUS_SCHEMA,
        "updated_at": _text(payload.get("updated_at")),
        "servers": copy.deepcopy(servers),
    }


def cached_gpu_server_status(
    device_id: str,
    *,
    status_path: str | Path | None = None,
) -> dict[str, Any] | None:
    clean_id = _text(device_id)
    _RESOURCE_SERVICE.validate_id(clean_id, "device")
    with _STATUS_LOCK:
        store = _read_status_store(status_path)
    entry = store["servers"].get(clean_id)
    if not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict):
        return None
    return copy.deepcopy(entry["payload"])


def store_gpu_server_status(
    device_id: str,
    payload: dict[str, Any],
    *,
    status_path: str | Path | None = None,
) -> dict[str, Any]:
    clean_id = _text(device_id)
    _RESOURCE_SERVICE.validate_id(clean_id, "device")
    if not isinstance(payload, dict):
        raise ValueError("GPU server status must be an object")
    stored = copy.deepcopy(payload)
    if _text(stored.get("device_id")) != clean_id:
        raise ValueError("GPU server status device_id does not match its cache key")
    path = Path(status_path or DEFAULT_STATUS_PATH).expanduser()
    now = time.time()
    with _STATUS_LOCK:
        store = _read_status_store(path)
        store["updated_at"] = utc_now_iso()
        store["servers"][clean_id] = {
            "stored_at": now,
            "stored_at_iso": utc_now_iso(),
            "payload": stored,
        }
        atomic_replace_json(path, store, sort_keys=False)
    return stored


def load_device_observations(
    status_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    with _STATUS_LOCK:
        store = _read_status_store(status_path)
    observations: dict[str, dict[str, Any]] = {}
    for server_id, entry in store["servers"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict):
            continue
        payload = copy.deepcopy(entry["payload"])
        if _text(payload.get("device_id")) != server_id:
            continue
        observations[server_id] = payload
        for gpu in payload.get("gpus") if isinstance(payload.get("gpus"), list) else []:
            if not isinstance(gpu, dict):
                continue
            gpu_id = _text(gpu.get("device_id"))
            if not _RESOURCE_SERVICE.is_valid_id(gpu_id, "device"):
                continue
            observations[gpu_id] = copy.deepcopy(gpu)
    return observations


def resolve_device_address(
    address: str,
    *,
    status_path: str | Path | None = None,
) -> dict[str, Any]:
    parsed = _RESOURCE_SERVICE.parse_address(address)
    if parsed.resource_type != "device":
        raise ValueError(f"not a device address: {address}")
    records = load_device_records()
    by_id = {record["device_id"]: record for record in records}
    root = by_id.get(parsed.root_id)
    if root is None:
        raise KeyError(parsed.root_id)

    target = root
    if parsed.suffix:
        if (
            len(parsed.suffix) != 2
            or parsed.suffix[0] != "gpu"
            or not parsed.suffix[1].isdigit()
            or str(int(parsed.suffix[1])) != parsed.suffix[1]
            or root["device_kind"] != "gpu_server"
        ):
            raise ValueError(f"unsupported nested device address: {parsed.value}")
        gpu_index = int(parsed.suffix[1])
        target = next(
            (
                record
                for record in gpu_children(root["device_id"], records=records)
                if int(record["gpu_index"]) == gpu_index
            ),
            None,
        )
        if target is None:
            raise KeyError(parsed.value)

    observations = load_device_observations(status_path)
    target_id = target["device_id"]
    try:
        ssh = effective_ssh_access(
            target_id,
        )
        access_methods = {"ssh": copy.deepcopy(ssh)}
    except ValueError:
        access_methods = {}
    resolved_device = copy.deepcopy(target)
    resolved_device.pop("access_methods", None)
    return {
        "schema": "tfi.resource_resolution.v2",
        "address": parsed.value,
        "root_id": parsed.root_id,
        "resource_type": "device",
        "target_kind": "device",
        "record": resolved_device,
        "device_id": target_id,
        "observation": observations.get(target_id, {}),
        "access_methods": access_methods,
        "portal_url": portal_url_for_address(target_id, "device"),
        "resolved_path": "",
        "exists": True,
    }
