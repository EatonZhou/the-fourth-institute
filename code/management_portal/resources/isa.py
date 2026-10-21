"""ResourceService instruction handlers and its kernel.

ResourceService owns the resource instructions (decision_ad3e21094f14), the
Project instructions of the organization category and the credential and
permission queries of the permission category (decision_22227d0ad075).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from management_common import secure_paths

from management_portal.hatg.contracts import InstructionError, invalid, not_found
from management_portal.hatg.declarations import cursor_offset, offset_cursor
from management_portal.hatg.kernel import InstructionKernel, Invocation, OwnerBinding
from management_portal.hatg.maintenance import recover_projections


UPLOAD_TTL_SECONDS = 24 * 60 * 60
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_WORKSPACE_TEXT_BYTES = 8 * 1024 * 1024
POST_IMAGE_MAX_BYTES = 12 * 1024 * 1024
POST_IMAGE_MAX_PIXELS = 40_000_000


def _service():
    from .runtime import get_resource_service

    return get_resource_service()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


# -- resources ---------------------------------------------------------------------


def search_resources(inv: Invocation) -> dict[str, Any]:
    offset = cursor_offset(inv.argument("cursor"))
    query: dict[str, Any] = {"actor": inv.caller, "limit": inv.argument("limit") or 50,
                             "offset": offset, "include_attributes": True}
    if inv.argument("q"):
        query["q"] = inv.argument("q")
    for name, field in (("resource_type", "resource_types"), ("project_id", "projects"), ("status", "statuses")):
        if inv.argument(name):
            query[field] = [inv.argument(name)]
    result = _service().query(**query)
    items = []
    for item in result.get("resources") or []:
        summary = {
            key: item.get(key)
            for key in ("resource_id", "resource_type", "title", "summary", "project", "url", "status", "updated_at")
            if key in item
        }
        attributes = item.get("attributes") or {}
        projects = attributes.get("project_ids") or []
        summary.setdefault("project", projects[0] if len(projects) == 1 else projects)
        if "status" in attributes:
            summary.setdefault("status", attributes["status"])
        items.append(summary)
    resources = result.get("resources") or []
    total = int(result.get("total") or len(items))
    page: dict[str, Any] = {"items": items, "total": total}
    if resources and offset + len(resources) < total:
        page["next_cursor"] = offset_cursor(offset + len(resources))
    return page


def resolve_resource(inv: Invocation) -> dict[str, Any]:
    try:
        resolved = _service().resolve(inv.argument("address"), actor=inv.caller)
    except PermissionError as exc:
        raise not_found(f"资源地址不存在或不可读：{inv.argument('address')}") from exc
    if resolved.get("exists") is False:
        raise not_found(f"资源地址不存在：{inv.argument('address')}")
    record = resolved.get("record") if isinstance(resolved.get("record"), dict) else {}
    operations = ["read", "update", "manage"]
    target = {key: value for key, value in resolved.items() if key not in {"record"}}
    return {"resource": record, "target": target, "operations": operations}


def _resource_version(record: dict[str, Any]) -> str:
    """Use the provider's version number, never a serialized audit record."""
    value = record.get("version")
    if isinstance(value, dict):
        value = value.get("number")
    return str(value if value is not None else record.get("updated_at") or "")


def read_resource(inv: Invocation) -> dict[str, Any]:
    import base64

    address = inv.argument("address")
    limit = int(inv.argument("max_bytes") or 4 * 1024 * 1024)
    file_address = address.partition("/")[2].partition("/")[0] == "file"
    if not file_address and (inv.argument("meta_only") or inv.argument("preview")):
        raise invalid("meta_only 与 preview 只用于 <资源ID>/file/<相对路径> 地址")
    if file_address and (inv.argument("meta_only") or inv.argument("preview")):
        return _read_file_summary(address, inv.caller, limit, meta_only=bool(inv.argument("meta_only")))
    try:
        data, content_type, resolved = _service().read_content(address, actor=inv.caller, max_bytes=limit)
    except ValueError as exc:
        if "limit" not in str(exc):
            raise
        return _oversized_read(address, inv.caller)
    if resolved.get("target_kind") == "directory":
        return _directory_result(data, resolved)
    version = _resource_version(resolved.get("record") or {}) if "/" not in address else ""
    result: dict[str, Any] = {
        "content_type": str(content_type or "application/octet-stream"),
        "version": version or hashlib.sha256(data).hexdigest(),
        "size": len(data),
        **({**_file_metadata(resolved), **_save_allows(resolved, inv.caller)}
           if resolved.get("target_kind") == "file" and file_address else {}),
    }
    try:
        result["text"] = data.decode("utf-8")
    except UnicodeDecodeError:
        result["base64"] = base64.b64encode(data).decode("ascii")
    return result


# -- files inside folder resources (decision_ad3e21094f14) -----------------------------

_PREVIEW_BINARY_KINDS = frozenset({"binary", "image", "pdf"})


def _file_metadata(resolved: dict[str, Any]) -> dict[str, Any]:
    """Name, preview kind and modification time of the file a resolution names, and its Portal content address."""
    from management_portal.society_service.lineage_workspace_files import workspace_file_kind

    mtime_ns = int(resolved.get("content_mtime_ns") or 0)
    name = str(resolved.get("filename") or "")
    return {
        "name": name, "kind": workspace_file_kind(name),
        "modified_at": _iso(datetime.fromtimestamp(mtime_ns / 1e9, timezone.utc)),
        # Nanosecond timestamps exceed JavaScript's exact integer range.
        "mtime_ns": str(mtime_ns),
        "content_url": str(resolved.get("portal_url") or ""),
    }


def _save_allows(resolved: dict[str, Any], caller: str) -> dict[str, Any]:
    """``allows.UpdateWorkspaceFile`` of a Workspace file, for the save button that runs it on this address."""
    if resolved.get("resource_type") != "workspace":
        return {}
    from management_portal.society_service.lineage_access_policy import workspace_reaches

    root = str(((resolved.get("record") or {}).get("attributes") or {}).get("path") or "")
    native_root = Path(root)
    relative = str(resolved.get("relative_path") or "")
    path = str(native_root.joinpath(*relative.split("/"))) if native_root.is_absolute() else ""
    return {"allows": {"UpdateWorkspaceFile": workspace_reaches(caller, str(resolved.get("root_id") or ""), write=True, path=path)}}


def _directory_result(data: bytes, resolved: dict[str, Any]) -> dict[str, Any]:
    from .providers.folder_files import DIRECTORY_MEDIA_TYPE

    listing = json.loads(data)
    mtime_ns = int(resolved.get("content_mtime_ns") or 0)
    return {
        "content_type": DIRECTORY_MEDIA_TYPE, "version": hashlib.sha256(data).hexdigest(), "size": len(data),
        "name": str(resolved.get("filename") or ""), "kind": "directory",
        "modified_at": _iso(datetime.fromtimestamp(mtime_ns / 1e9, timezone.utc)),
        "entries": listing["entries"], "truncated": listing["truncated"], "hidden_count": listing["hidden_count"],
    }


def _read_file_summary(address: str, caller: str, limit: int, *, meta_only: bool) -> dict[str, Any]:
    """A file's metadata, or a bounded text preview of it; a directory reads as its children."""
    from management_portal.resources.content_payload import resolved_media_type

    service = _service()
    resolved = service.resolve(address, actor=caller)
    if not resolved.get("exists"):
        raise not_found(f"资源地址不存在：{address}")
    if resolved.get("target_kind") == "directory":
        data, _content_type, current = service.read_content(address, actor=caller)
        return _directory_result(data, {**resolved, **current})
    size = int(resolved.get("content_size") or 0)
    result: dict[str, Any] = {
        "content_type": resolved_media_type(resolved, fallback="application/octet-stream"), "size": size,
        **_file_metadata(resolved), **_save_allows(resolved, caller),
    }
    if meta_only:
        return result
    result["truncated"] = size > limit
    result["binary"] = result["kind"] in _PREVIEW_BINARY_KINDS
    data = b""
    if size and not result["binary"]:
        data, _content_type, current = service.read_content_range(address, start=0, end=min(size, limit) - 1, actor=caller)
        if current.get("content_mtime_ns") != resolved.get("content_mtime_ns"):
            result.update(_file_metadata(current), size=int(current.get("content_size") or 0))
            result["truncated"] = result["size"] > limit
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A cut through the last character is still text.
        text = data[:exc.start].decode("utf-8") if result["truncated"] and exc.start >= len(data) - 3 else None
    if text is None or "\x00" in text[:2048]:
        result["binary"], text = True, ""
    result["text"] = text
    result["version"] = hashlib.sha256(data).hexdigest() if not result["truncated"] and not result["binary"] else ""
    if not result["version"]:
        result.pop("version")
    return result


def get_resource_type(inv: Invocation) -> dict[str, Any]:
    return _resource_type_details(inv.argument("resource_type"))


def _resource_type_details(resource_type: str, *, row=None, counts=None) -> dict[str, Any]:
    from management_portal.hatg.resource_actions import actions_for_type

    service = _service()
    row = row if row is not None else service.resource_types.get(resource_type)
    capabilities = service.capabilities(resource_type) if row['installed'] else {}
    if counts is None:
        counts = service._index().type_counts()
    declared = {action.action: action for action in actions_for_type(resource_type)}
    actions = [
        declared[name].schema()
        for name in capabilities.get("actions") or []
        if name in declared
    ]
    return {
        **row,
        "declaration": service.resource_types.identity.declaration(resource_type),
        "instance_count": counts.get(resource_type, 0),
        "registerable": bool(capabilities.get("registerable")),
        "updatable": bool(capabilities.get("updatable")),
        "unregisterable": bool(capabilities.get("unregisterable")),
        "actions": actions,
    }


def list_resource_types(inv: Invocation) -> dict[str, Any]:
    service = _service()
    counts = service._index().type_counts()
    return {"types": [_resource_type_details(kind, row=row, counts=counts)
                      for kind, row in service.resource_types.all().items()],
            "registry": service.resource_types.identity.metadata}


def update_resource_type(inv: Invocation) -> dict[str, Any]:
    service = _service()
    kind = inv.argument('resource_type')
    expected = inv.expected_version_for(kind)
    with _registry_errors():
        result = service.resource_types.update(kind, inv.argument('enabled'),
                                               expected_version=expected, actor=inv.caller)
    if result['enabled']:
        index = service._index()
        index.adapter_layer.request_refresh(index._target_adapters((kind,)), force=True)
    return result


# Updates and unregisters still wait for their queued index refresh.
INDEX_WAIT = {"wait_for_index": True, "index_timeout": 60}


@contextmanager
def _registry_errors() -> Iterator[None]:
    """Keep the reason a registry change was refused in the instruction error."""

    from .errors import GitCheckoutRequired, ResourceVersionConflict
    from .resource_types import ResourceTypeError

    try:
        yield
    except ResourceTypeError as exc:
        raise InstructionError(exc.code, str(exc), details=exc.details) from exc
    except GitCheckoutRequired as exc:
        raise InstructionError("instruction.conflict", str(exc), details={
            "reason": "git_checkout_required", "path": exc.path, "can_initialize_git": True,
            "hint": "目录还不是 Git 仓库；在 fields 中加入 initialize_git: true 后重试即可初始化",
        }) from exc
    except ResourceVersionConflict as exc:
        raise InstructionError("instruction.version_conflict", str(exc), details={
            "expected_version": exc.expected_version, "current_version": exc.current_version,
        }) from exc


def _register_resource(resource_type: str, fields: dict[str, Any], actor: str) -> dict[str, Any]:
    with _registry_errors():
        record = _service().register(resource_type, fields, actor=actor)
    if resource_type == "project":
        from management_common.projection_receipts import project_committed
        from management_portal import project_registry
        from management_portal.society_service import get_society_service

        project_id = str(record["resource_id"])
        project_committed({
            "projection": "project.principal", "project_id": project_id,
            "committed_revision": project_registry.revision(),
        }, lambda: get_society_service().lineage.provision_project_principal(project_id, actor))
    return record


def register_resource(inv: Invocation) -> dict[str, Any]:
    record = _register_resource(
        inv.argument("resource_type"), dict(inv.argument("fields") or {}), inv.caller,
    )
    return {"resource_id": str(record.get("resource_id") or ""), "resource": record}


def update_resource(inv: Invocation) -> dict[str, Any]:
    from .providers.knowledge import MUTABLE_RESOURCE_TYPES

    changes = dict(inv.argument("changes") or {})
    token = inv.expected_version_for(inv.argument("resource_id"))
    if token is None:
        token = changes.get("expected_version")
    if token is not None:
        resource_type = inv.argument("resource_id").partition("_")[0]
        if resource_type not in MUTABLE_RESOURCE_TYPES and resource_type != "workspace":
            raise invalid(f"{resource_type} 的 UpdateResource 不支持 expected_version")
        changes["expected_version"] = token
    with _registry_errors():
        record = _service().update(inv.argument("resource_id"), changes, actor=inv.caller, **INDEX_WAIT)
    return {"resource": record, "version": _resource_version(record)}


def unregister_resource(inv: Invocation) -> dict[str, Any]:
    with _registry_errors():
        record = _service().unregister(inv.argument("resource_id"), actor=inv.caller, **INDEX_WAIT)
    return {**record, "resource_id": inv.argument("resource_id")}


def _action_target(resource_id: str, actor: str) -> dict[str, Any]:
    """The resource an action runs on.

    A resource registered moments ago, such as a workbench created from its
    page, may not be in the search index yet; its own provider already
    resolves it, the same way ReadResource reads it.
    """

    try:
        return _service().get(resource_id, actor=actor)
    except KeyError:
        try:
            resolved = _service().resolve(resource_id, actor=actor)
        except Exception:  # noqa: BLE001 - unknown to its provider as well
            raise KeyError(resource_id) from None
        return {**dict(resolved.get("record") or {}), "resource_type": str(resolved.get("resource_type") or "")}


def run_resource_action(inv: Invocation) -> dict[str, Any]:
    from management_portal.hatg.resource_actions import resource_action

    resource_id = inv.argument("resource_id")
    record = _action_target(resource_id, inv.caller)
    declaration = resource_action(str(record.get("resource_type") or ""), inv.argument("action"))
    arguments = dict(inv.argument("arguments") or {})
    declaration.validate_arguments(arguments)
    result = _service().action(resource_id, inv.argument("action"), arguments, actor=inv.caller)
    return {"result": result}


def reads_only(inv: Invocation) -> bool:
    """RunResourceAction records an execution only when the declared action changes state."""

    from management_portal.hatg.resource_actions import resource_action

    try:
        record = _action_target(inv.argument("resource_id"), inv.caller)
        return not resource_action(str(record.get("resource_type") or ""), inv.argument("action")).mutates
    except Exception:  # noqa: BLE001 - unknown actions are recorded and then rejected
        return False


# -- uploads -----------------------------------------------------------------------


def upload_root() -> Path:
    """One upload store for every deployment.

    Dev and Release share their Conversations, and the router may send one
    user's requests to either (a restart sends them to the other), so an
    upload written through one must be readable through the other.  10-12 an
    image uploaded through Release while Dev restarted was 404 on Dev.
    """

    configured = str(os.getenv("PORTAL_UPLOAD_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser()
    from management_common.instance_paths import instance_root

    return instance_root() / "uploads"


def _upload_dir(upload_id: str) -> Path:
    if not upload_id.startswith("upload_") or not upload_id[7:].isalnum():
        raise not_found(f"上传不存在：{upload_id}")
    return upload_root() / upload_id


def read_upload(upload_id: str) -> dict[str, Any]:
    directory = _upload_dir(upload_id)
    try:
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise not_found(f"上传不存在：{upload_id}") from None
    # The upload belongs to this instance. Legacy absolute paths may point at
    # a retired installation even when its complete upload store was copied.
    name = meta.get("file_name")
    if (not isinstance(name, str) or name in {"", ".", ".."}
            or any(character in name for character in ("/", "\\", "\x00"))):
        raise invalid("上传文件名无效")
    path = directory / name
    if path.resolve().parent != directory.resolve():
        raise invalid("上传文件不能位于上传目录之外")
    return {**meta, "path": str(path)}


# -- downloads of content larger than one ReadResource result ---------------------------

DOWNLOAD_TTL_SECONDS = 15 * 60
DOWNLOAD_CHUNK_BYTES = 4 * 1024 * 1024
_DOWNLOAD_TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _download_path(token: str) -> Path:
    if len(token) != 32 or not set(token) <= _DOWNLOAD_TOKEN_CHARS:
        raise not_found("下载地址不存在或已经失效")
    return upload_root() / "downloads" / f"{token}.json"


def _content_size(address: str, caller: str, resolved: dict[str, Any]) -> int:
    size = resolved.get("content_size")
    if type(size) is int and size >= 0:
        return size
    path = Path(str(resolved.get("resolved_path") or ""))
    if str(path) not in {"", "."} and path.is_file():
        return path.stat().st_size
    raise invalid(f"{address} 的内容超过 max_bytes，且不支持分段读取")


def iter_content(address: str, caller: str, size: int, start: int = 0, end: int | None = None) -> Iterator[bytes]:
    """Read ``[start, end]`` in chunks; every chunk passes the resource's permission check again."""

    last = size - 1 if end is None else min(end, size - 1)
    position = start
    while position <= last:
        chunk_end = min(position + DOWNLOAD_CHUNK_BYTES - 1, last)
        data, _content_type, _resolved = _service().read_content_range(address, start=position, end=chunk_end, actor=caller)
        if not data:
            return
        yield bytes(data)
        position += len(data)


def _oversized_read(address: str, caller: str) -> dict[str, Any]:
    """Content above ``max_bytes``: its size, digest and a download address for this caller only."""

    resolved = _service().resolve(address, actor=caller)
    size = _content_size(address, caller, resolved)
    digest = hashlib.sha256()
    for chunk in iter_content(address, caller, size):
        digest.update(chunk)
    token = secrets.token_urlsafe(24)
    expires = _now() + timedelta(seconds=DOWNLOAD_TTL_SECONDS)
    content_type = str(resolved.get("media_type") or resolved.get("content_type") or "application/octet-stream")
    record = {"address": address, "caller": caller, "size": size, "version": digest.hexdigest(),
              "content_type": content_type, "expires_at": _iso(expires)}
    path = _download_path(token)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{token}.{secrets.token_hex(4)}")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)
    return {"content_type": content_type, "version": record["version"], "size": size,
            "download_url": f"/api/resources/download/{token}", "expires_at": record["expires_at"]}


def read_download(token: str, caller: str) -> dict[str, Any]:
    """The download a token grants; only its caller may use it, and only until it expires."""

    path = _download_path(token)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise not_found("下载地址不存在或已经失效") from None
    if record.get("expires_at", "") < _iso(_now()):
        path.unlink(missing_ok=True)
        raise not_found("下载地址不存在或已经失效")
    if record.get("caller") != caller:
        raise not_found("下载地址不存在或已经失效")
    return record


def _write_meta(meta: dict[str, Any]) -> None:
    directory = _upload_dir(meta["upload_id"])
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".meta.{secrets.token_hex(4)}"
    # Keep identity and filename portable; read_upload supplies the local path.
    stored = {key: value for key, value in meta.items() if key != "path"}
    temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, directory / "meta.json")


def _safe_name(value: str) -> str:
    name = PurePosixPath(str(value or "file")).name.replace("\x00", "")
    return name[:180] or "file"


IMAGE_PURPOSES = frozenset({"post_image", "avatar"})


def _purpose_limit(purpose: str) -> int:
    if purpose == "avatar":
        from management_portal.avatar_store import MAX_IMAGE_BYTES

        return MAX_IMAGE_BYTES
    return POST_IMAGE_MAX_BYTES if purpose in IMAGE_PURPOSES else MAX_UPLOAD_BYTES


def _checked_media_type(purpose: str, data: bytes, media_type: str) -> str:
    """Size and type rules of an upload's purpose (decision_61e50306cc79), applied to its bytes."""

    if len(data) > _purpose_limit(purpose):
        raise invalid(f"文件不能超过 {_purpose_limit(purpose)} 字节")
    if purpose in IMAGE_PURPOSES:
        actual_type = _post_image_type(data)
        if purpose == "avatar" and actual_type == "image/gif":
            raise invalid("头像格式必须是 PNG、JPEG 或 WebP")
        return actual_type
    if purpose == "paper_pdf":
        if not data.startswith(b"%PDF-"):
            raise invalid("Paper PDF 必须是 PDF 文件")
        return "application/pdf"
    return media_type or "application/octet-stream"


def _check_target(target: str, caller: str) -> None:
    """Where an upload is sent must exist and be open to the caller.

    A conversation attachment names the receiving Resident (who can see that
    Resident may read the attachment); any other target is a workspace or
    resource address the caller can write.
    """

    if target.startswith("resident_"):
        from management_portal.society_service import get_society_service

        try:
            get_society_service().resident.get_resident(target, caller)
        except Exception as exc:  # noqa: BLE001 - invisible and missing Residents look the same
            raise InstructionError("instruction.not_found", f"上传目标不存在：{target}") from exc
        return
    try:
        resolved = _service().resolve(target, actor=caller)
    except Exception as exc:  # noqa: BLE001 - unreadable and missing targets look the same
        raise InstructionError("instruction.not_found", f"上传目标不存在：{target}") from exc
    record = resolved.get("record") if isinstance(resolved.get("record"), dict) else {}
    resource_id = str(record.get("resource_id") or resolved.get("root_id") or "")
    if not resource_id or not resolved.get("exists", True):
        raise InstructionError("instruction.not_found", f"上传目标不存在：{target}")


def create_upload(inv: Invocation) -> dict[str, Any]:
    upload_id = f"upload_{secrets.token_hex(12)}"
    purpose = inv.argument("purpose")
    target = inv.argument("target") or ""
    if target:
        _check_target(target, inv.caller)
    meta: dict[str, Any] = {
        "upload_id": upload_id,
        "purpose": purpose,
        "target": target,
        "created_by": inv.caller,
        "created_at": _iso(_now()),
    }
    source = inv.argument("source_address")
    if source:
        data, content_type, resolved = _service().read_content(source, actor=inv.caller, max_bytes=MAX_UPLOAD_BYTES)
        media_type = _checked_media_type(purpose, bytes(data), str(content_type or ""))
        name = _safe_name(str(resolved.get("filename") or source.rsplit("/", 1)[-1]))
        directory = _upload_dir(upload_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(data)
        meta.update({
            "file_name": name, "media_type": media_type,
            "size": len(data), "path": str(directory / name), "ready": True, "source_address": source,
        })
        _write_meta(meta)
        return {"upload_id": upload_id, "ready": True}
    size = int(inv.argument("size") or 0)
    if size > _purpose_limit(purpose):
        raise invalid(f"文件不能超过 {_purpose_limit(purpose)} 字节")
    name = _safe_name(inv.argument("file_name"))
    expires = _now() + timedelta(seconds=UPLOAD_TTL_SECONDS)
    meta.update({
        "file_name": name, "media_type": inv.argument("media_type"), "size": size,
        "path": str(_upload_dir(upload_id) / name), "ready": False, "expires_at": _iso(expires),
    })
    _write_meta(meta)
    return {"upload_id": upload_id, "ready": False, "upload_url": f"/api/uploads/{upload_id}", "expires_at": meta["expires_at"]}


def _post_image_type(data: bytes) -> str:
    """A notice board image must be a readable PNG, JPEG, GIF or WebP picture."""

    import io

    from PIL import Image

    if len(data) > POST_IMAGE_MAX_BYTES:
        raise invalid(f"图片不能超过 {POST_IMAGE_MAX_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            image.verify()
    except Exception as exc:  # noqa: BLE001 - any decoder failure means "not an image"
        raise invalid("上传的文件不是可读取的图片") from exc
    if width <= 0 or height <= 0 or width * height > POST_IMAGE_MAX_PIXELS:
        raise invalid("图片尺寸超出限制")
    media_type = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}.get(image_format)
    if media_type is None:
        raise invalid("图片格式必须是 PNG、JPEG、GIF 或 WebP")
    return media_type


def complete_upload(upload_id: str, data: bytes, *, actor: str) -> dict[str, Any]:
    """Write the bytes of a pending upload (the data channel behind ``upload_url``)."""

    meta = read_upload(upload_id)
    if meta.get("ready"):
        raise InstructionError("instruction.conflict", "这个上传已经完成")
    if meta.get("expires_at") and meta["expires_at"] < _iso(_now()):
        raise InstructionError("instruction.conflict", "上传的数据通道已经过期")
    if len(data) != int(meta.get("size") or 0):
        raise invalid(f"字节数与声明的 size 不一致：{len(data)} ≠ {meta.get('size')}")
    meta["media_type"] = _checked_media_type(str(meta.get("purpose") or ""), data, str(meta.get("media_type") or ""))
    path = Path(meta["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    meta["ready"] = True
    meta.pop("expires_at", None)
    _write_meta(meta)
    return {"upload_id": upload_id, "ready": True, "size": len(data)}


def delete_upload(inv: Invocation) -> dict[str, Any]:
    import shutil

    upload_id = inv.argument("upload_id")
    meta = read_upload(upload_id)  # an unknown upload is not_found
    from management_common.actor_identity import same_actor
    from management_portal import permission_system

    if permission_system.owners_only() and not same_actor(meta.get("created_by"), inv.caller):
        raise InstructionError("instruction.forbidden", "只有上传者本人可以删除上传")
    shutil.rmtree(_upload_dir(upload_id), ignore_errors=True)
    return {"upload_id": upload_id}


# -- workspace files ---------------------------------------------------------------


@contextmanager
def _workspace_directory(root: Path, parts: tuple[str, ...]) -> Iterator[int]:
    """Open the parent directory of a workspace file without following symlinks."""

    with secure_paths.open_directory(root, parts[:-1], create=True) as directory:
        yield directory


def _expected_file_version(inv: Invocation, address: str) -> str:
    """The content digest the caller read, when it asked for a version check."""

    expected = inv.expected_version
    if isinstance(expected, dict):
        expected = expected.get(address)
    return str(expected) if expected not in (None, "") else ""


def update_workspace_file(inv: Invocation) -> dict[str, Any]:
    from .providers.workspace import _root

    address = inv.argument("address")
    if address.startswith("/"):
        raise invalid("UpdateWorkspaceFile 只接受 <workspace-id>/file/<相对路径>，不接受绝对路径")
    root_id, _, suffix = address.partition("/")
    namespace, _, relative = suffix.partition("/")
    try:
        parts = secure_paths.relative_parts(relative)
    except ValueError as exc:
        raise invalid("地址应为 <workspace-id>/file/<相对路径>") from exc
    if namespace != "file":
        raise invalid("地址应为 <workspace-id>/file/<相对路径>")
    content = str(inv.argument("content") or "")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_WORKSPACE_TEXT_BYTES:
        raise invalid(f"文件内容不能超过 {MAX_WORKSPACE_TEXT_BYTES} 字节")
    record = _service().get(root_id, actor=inv.caller)
    from management_portal.society_service.lineage_access_policy import workspace_reaches

    root = _root(record)
    if not workspace_reaches(inv.caller, root_id, write=True, path=str(root / relative)):
        raise InstructionError("instruction.forbidden", f"无权写入 {root_id}")
    expected = _expected_file_version(inv, address)
    with _workspace_directory(root, parts) as directory, secure_paths.lock_directory(directory):
        name = parts[-1]
        # Compare and replace under one lock on the parent directory.
        current = ""
        try:
            existing = secure_paths.stat_child(directory, name)
            if not stat.S_ISREG(existing.st_mode):
                raise invalid("目标不是普通文件")
            if expected:
                handle = secure_paths.open_child(directory, name, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(handle, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise invalid("目标不是普通文件")
                    current = hashlib.sha256(stream.read()).hexdigest()
        except FileNotFoundError:
            pass
        if expected and current != expected:
            raise InstructionError(
                "instruction.version_conflict", "文件内容已经变化，未写入",
                details={"address": address, "current_version": current},
            )
        temporary = f".{name}.{secrets.token_hex(4)}.tmp"
        fd = secure_paths.open_child(directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664)
        try:
            with os.fdopen(fd, "wb") as stream:
                secure_paths.prepare_child_permissions(directory, temporary, name, 0o664)
                stream.write(encoded)
            secure_paths.replace_child(directory, temporary, name)
        except BaseException:
            try:
                secure_paths.unlink_child(directory, temporary)
            except FileNotFoundError:
                pass
            raise
        written = secure_paths.stat_child(directory, name)
    _service().notify_source_changed(resource_id=root_id, resource_type="workspace", kind="updated")
    return {
        "version": hashlib.sha256(encoded).hexdigest(),
        "modified_at": _iso(datetime.fromtimestamp(written.st_mtime, timezone.utc)),
    }


# -- projects ----------------------------------------------------------------------


def _project_resource_ids(actor: str, projects: list[str], *, exclude: set[str]) -> list[str]:
    """Every resource a project owns, read page by page.

    Lineages and Residents go with their Lineage.  Papers and the other objects
    of a feature's shared library are only related to the project; they lose the
    relation instead (``project.relations``).
    """
    from management_portal import portal_features

    kept = {"lineage", "resident", *portal_features.related_types()}
    ids: list[str] = []
    offset = 0
    while True:
        page = _service().query(actor=actor, projects=projects, limit=5000, offset=offset)
        resources = [item for item in page.get("resources") or [] if isinstance(item, dict)]
        for item in resources:
            item_id = str(item.get("resource_id") or "")
            if item_id and item_id not in exclude and str(item.get("resource_type") or "") not in kept:
                ids.append(item_id)
        offset += len(resources)
        if not resources or offset >= int(page.get("total") or 0):
            return list(dict.fromkeys(ids))


def _project_record(project_id: str, actor: str) -> dict[str, Any]:
    result = _service().query(actor=actor, resource_types=["project"], q=project_id, limit=20)
    for item in result.get("resources") or []:
        if project_id in {str(item.get("resource_id") or ""), str(item.get("title") or "")}:
            return item
    raise not_found(f"Project 不存在：{project_id}")


def create_project(inv: Invocation) -> dict[str, Any]:
    fields = {
        "id": inv.argument("project_id"),
        "project_type": inv.argument("project_type"),
    }
    for name in ("description", "status", "ddl", "tags", "language"):
        if inv.argument(name) is not None:
            fields[name] = inv.argument(name)
    record = _register_resource("project", fields, inv.caller)
    return {"project": record}


def get_project(inv: Invocation) -> dict[str, Any]:
    from .providers.project import project_state, project_summary

    project_id = inv.argument("project_id")
    record = _project_record(project_id, inv.caller)
    resource_id = str(record.get("resource_id") or project_id)
    try:
        summary = project_summary(resource_id)
    except (FileNotFoundError, KeyError):
        summary = {"exists": False, "text": ""}
    return {"project": record, "summary": summary, "state": project_state(resource_id)}


def update_project(inv: Invocation) -> dict[str, Any]:
    from .providers.project import save_project_summary

    project_id = inv.argument("project_id")
    record = _project_record(project_id, inv.caller)
    resource_id = str(record.get("resource_id") or project_id)
    changes = dict(inv.argument("changes") or {})
    unknown = sorted(set(changes) - {"status", "ddl", "project_type", "summary", "avatar"})
    if unknown:
        raise invalid("changes 只能包含 status、ddl、project_type、summary 与 avatar")
    summary = changes.pop("summary", None)
    has_avatar = "avatar" in changes
    avatar = changes.pop("avatar", None)
    # The Project provider changes one registry field per update (decision_4648f7fa3295).
    for field, value in changes.items():
        record = _service().update(
            resource_id, {"field": field, "value": value}, actor=inv.caller, resource_type="project",
        )
    if summary is not None:
        save_project_summary(resource_id, str(summary))
    if has_avatar:
        from management_portal.avatar_store import change_avatar, scope_avatar_store, scope_subject

        try:
            record = {**record, "avatar": change_avatar(scope_avatar_store(), scope_subject("project", resource_id), avatar, inv.caller)}
        except ValueError as exc:
            raise invalid(str(exc)) from exc
    return {"project": record}


def delete_project(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import get_society_service

    project_id = inv.argument("project_id")
    try:
        record = _project_record(project_id, inv.caller)
    except InstructionError as exc:
        # A deletion this caller already ran leaves the index first; calling
        # again finishes what is left, as the deletion audit allows.
        if exc.code != "instruction.not_found":
            raise
        record = {}
    resource_id = str(record.get("resource_id") or project_id)
    try:
        get_society_service().project.validate_project_deletion(project_id, inv.caller)
    except KeyError as exc:
        raise not_found(f"Project 不存在：{project_id}") from exc
    # The project's resources go first, while the project and its member roles still
    # exist; Lineage deletion handles its own Lineage and Resident records.
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    for item_id in _project_resource_ids(inv.caller, [project_id, resource_id], exclude={resource_id}):
        try:
            _service().unregister(item_id, actor=inv.caller)
            removed.append(item_id)
        except Exception as exc:  # noqa: BLE001 - reported below; files stay until every record is gone
            failed.append({"resource_id": item_id, "error": str(exc) or type(exc).__name__})
    if failed:
        raise InstructionError(
            "instruction.failed", "Project 资源注销未完成；再次调用会继续注销剩余资源，之后才删除项目内容",
            details={"project_id": project_id, "unregistered_resource_ids": removed, "failed": failed},
            commit_state="unknown" if removed else "not_committed",
        )
    from management_portal import portal_features

    # Objects in other features' libraries (papers) stay and only leave the Project.
    relations = {feature: relate(resource_id)
                 for feature, relate in portal_features.contributions("project.relations")}
    contents = get_society_service().project.delete_project(project_id, inv.caller)
    # The Project leaves the permission trees with every entry naming it, so a
    # later Project with the same ID does not inherit them.
    _service().unregister(resource_id, actor=inv.caller, resource_type="project")
    # The Workspaces retired with the project directory leave the trees too.
    from management_portal import permission_system
    from .workspace_store import get_workspace_store

    permission_system.retire_resources(get_workspace_store().deleted_under([str(contents.get("project_path") or "")]))
    # Nor does a later Project with the same ID wear this one's avatar.
    from management_portal.avatar_store import scope_avatar_store, scope_subject

    scope_avatar_store().delete(scope_subject("project", resource_id))
    return {
        "project_id": project_id,
        "resource_ids": [*removed, resource_id],
        "lineage_ids": list(contents.get("lineage_ids") or []),
        "resident_ids": list(contents.get("resident_ids") or []),
        "unlinked": relations,
    }


def rename_project(inv: Invocation) -> dict[str, Any]:
    from management_portal import project_rename

    record = _project_record(inv.argument("project_id"), inv.caller)
    try:
        return project_rename.rename_project(
            str(record.get("resource_id") or inv.argument("project_id")), inv.argument("new_project_id"),
            inv.caller, dry_run=bool(inv.argument("dry_run")), entry=inv.context.entry,
        )
    except project_rename.RenameBlocked as exc:
        raise InstructionError("instruction.conflict", str(exc), details=exc.details) from exc
    except KeyError as exc:
        raise not_found(str(exc.args[0] if exc.args else exc)) from exc


# -- permissions -------------------------------------------------------------------


def permission_instruction(inv: Invocation) -> dict[str, Any]:
    """A permission instruction, answered by the permission system that is on (decision_22227d0ad075).

    Creating a scheme, changing its rules and activating it generate or bring
    up to date a scheme's instance from the objects this service knows.
    """
    from management_portal import permission_system
    from management_portal.hatg.declarations import target_value

    # The envelope's expected_version for the object the call targets (a scheme or an entry), compared by its handler.
    expected = inv.expected_version_for(target_value(inv.declaration, inv.call.arguments))
    return permission_system.manage(inv.name, dict(inv.arguments), actor=inv.caller,
                                    collect_facts=_service().permission_facts, expected_version=expected)


PERMISSION_INSTRUCTIONS = (
    "GetPermissionSystem", "ListPermissionSchemes", "GetPermissionScheme", "CreatePermissionScheme",
    "UpdatePermissionScheme", "DeletePermissionScheme", "ActivatePermissionScheme", "GetPermissionNode",
    "SearchPermissionNodes", "ListPermissionEntries", "CreatePermissionEntry", "UpdatePermissionEntry",
    "DeletePermissionEntry", "GetEffectivePermissions", "CheckPermission",
    "IssueTemporaryCredential", "RenewTemporaryCredential", "RevokeTemporaryCredential", "ListTemporaryCredentials",
)


HANDLERS = {
    **dict.fromkeys(PERMISSION_INSTRUCTIONS, permission_instruction),
    "SearchResources": search_resources,
    "ResolveResource": resolve_resource,
    "ReadResource": read_resource,
    "GetResourceType": get_resource_type,
    "ListResourceTypes": list_resource_types,
    "UpdateResourceType": update_resource_type,
    "RegisterResource": register_resource,
    "UpdateResource": update_resource,
    "UnregisterResource": unregister_resource,
    "RunResourceAction": run_resource_action,
    "CreateUpload": create_upload,
    "DeleteUpload": delete_upload,
    "UpdateWorkspaceFile": update_workspace_file,
    "CreateProject": create_project,
    "GetProject": get_project,
    "UpdateProject": update_project,
    "DeleteProject": delete_project,
    "RenameProject": rename_project,
}


def binding() -> OwnerBinding:
    return OwnerBinding(owner="resource", handlers=HANDLERS, streams={}, reads_only=_reads_only, version_of=version_of, recover_projections=recover_projections)


def _reads_only(inv: Invocation) -> bool:
    return inv.name == "RunResourceAction" and reads_only(inv)


def version_of(inv: Invocation, object_id: str) -> Any:
    """Version token of the target resource; other envelope keys are not looked up."""

    # Only the instruction's own target is compared, so an envelope cannot probe
    # objects the call does not touch. UpdateWorkspaceFile compares under the
    # file's directory lock, UpdateResource passes the token on to the resource's
    # provider, and an upload is immutable once written and has no token.
    if inv.name not in {"UnregisterResource", "RunResourceAction"} or object_id != inv.argument("resource_id"):
        return None
    # The same token GetResource reports.
    return _resource_version(_service().get(object_id, actor=inv.caller))


def build_kernel() -> InstructionKernel:
    from management_portal.hatg.store import ExecutionStore

    return InstructionKernel(binding(), store=ExecutionStore(_execution_database()))


def _execution_database():
    from management_common.portal_database import portal_database

    return portal_database(schema="resource_instructions")
