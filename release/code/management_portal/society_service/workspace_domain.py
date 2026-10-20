from __future__ import annotations
from management_common.portal_paths import path as portal_path, resolve_directory
from management_common.site_config import read_site_config

import base64
import binascii
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import resident_store
from .domain_ports import resident_domain_port
from .workspace_attachments import authorize_attachment_read
from .workspace_contracts import (
    CHAT_ATTACHMENT_CACHE_ROOT,
    CHAT_ATTACHMENT_MAX_BYTES,
    CHAT_IMAGE_CACHE_ROOT,
    CHAT_IMAGE_MAX_BYTES,
    STAGING_ROOT,
    image_type,
    validate_image_upload,
)


def _link_preview_roots() -> tuple[Path, ...]:
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict):
        raise ValueError("deployment must be an object")
    workspace = deployment.get("workspace", {})
    if not isinstance(workspace, dict):
        raise ValueError("deployment.workspace must be an object")
    configured = workspace.get("additional_preview_roots", [])
    if not isinstance(configured, list):
        raise ValueError("additional_preview_roots must be a list of paths")
    roots = [portal_path("share_root"), portal_path("staging_root")]
    for value in configured:
        if not isinstance(value, str) or ".." in Path(value).parts or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("additional_preview_roots contains an invalid path")
        resolved = resolve_directory(value)
        if resolved == Path(resolved.anchor):
            raise ValueError("additional_preview_roots cannot include a filesystem root")
        roots.append(resolved)
    return tuple(dict.fromkeys(roots))

SHARED_COLLABORATION_ROOTS = (
    (portal_path("share_root") / "Skills"),
    (portal_path("share_root") / "Agents"),
)
# Personal AMA folders, earlier personal agents and Personal Chat workspaces:
# reached only through a Resident's Workspace, never as shared files.
PERSONAL_ROOTS = ((portal_path("share_root") / "Agents" / "PersonalAgents"),)
FILE_PREVIEW_MAX_BYTES = 120_000
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}
TEXT_SUFFIXES = {
    ".txt", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".css", ".scss", ".html", ".htm", ".xml", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".sh", ".bash", ".zsh",
    ".sql", ".csv", ".log", ".tex", ".bib", ".rst",
}
MEDIA_KINDS = {"image", "video", "pdf"}


@dataclass(frozen=True)
class _LinkFileAuthorization:
    root: Path
    can_write: bool
    source: str


def _actor_id(value: object) -> str:
    actor = str(value or "").strip()
    while actor.startswith(("@", "＠")):
        actor = actor[1:].strip()
    return actor


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _slugify(value: str) -> str:
    clean = re.sub(r"[^0-9A-Za-z._-]+", "-", str(value or "").strip())
    return clean.strip("-._") or "file"


def _actor_upload_dir(actor: str) -> str:
    return _slugify(_actor_id(actor) or "user")


def _visible_resident_id(resident_id: object, actor: str) -> str:
    exact_id = str(resident_id or "").strip()
    if not exact_id:
        return ""
    identity = resident_domain_port.resolve_resident_identity(exact_id, actor)
    return str(identity.get("resident_id") or "")


def _decode_upload(payload: dict[str, Any], max_bytes: int) -> bytes:
    raw_bytes = payload.get("data_bytes")
    if isinstance(raw_bytes, bytes):
        data = raw_bytes
    else:
        raw = str(payload.get("data_base64") or "").strip()
        if "," in raw and raw.split(",", 1)[0].casefold().startswith("data:"):
            raw = raw.split(",", 1)[1]
        if not raw:
            raise ValueError("file data required")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("file data must be base64") from exc
    if not data:
        raise ValueError("file data required")
    if len(data) > max_bytes:
        raise ValueError(f"文件超过大小限制：{max_bytes // (1024 * 1024)} MB")
    return data


def _upload_path(root: Path, actor: str, filename: str, suffix: str) -> tuple[Path, Path]:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    stem = _slugify(Path(filename).stem or "attachment")[:36]
    name = f"{int(time.time())}_{secrets.token_hex(6)}_{stem}{suffix}"
    relative = Path(_actor_upload_dir(actor)) / day / name
    absolute = (root / relative).resolve(strict=False)
    absolute.parent.mkdir(parents=True, exist_ok=True)
    return relative, absolute


def save_workspace_pasted_image(
    payload: dict[str, Any], actor: str = ""
) -> dict[str, Any]:
    actor_id = _actor_id(actor)
    if not actor_id:
        raise PermissionError("需要登录后操作")
    resident_id = _visible_resident_id(payload.get("resident_id"), actor_id)
    original_name = str(payload.get("filename") or "pasted-image")
    data = _decode_upload(payload, CHAT_IMAGE_MAX_BYTES)
    content_type, suffix, width, height = validate_image_upload(data)
    relative, absolute = _upload_path(
        CHAT_IMAGE_CACHE_ROOT, actor_id, original_name, suffix
    )
    absolute.write_bytes(data)
    metadata = {
        "created_at": _now_iso(),
        "actor": actor_id,
        "resident_id": resident_id,
        "filename": original_name or absolute.name,
        "content_type": content_type,
        "size": len(data),
        "width": width,
        "height": height,
        "path": str(absolute),
        "file_id": relative.as_posix(),
    }
    resident_store.write_json(
        absolute.with_suffix(absolute.suffix + ".json"), metadata
    )
    return {
        "file_id": relative.as_posix(),
        "path": str(absolute),
        "filename": metadata["filename"],
        "content_type": content_type,
        "size": len(data),
        "width": width,
        "height": height,
        "preview_url": (
            "/api/agent-workbench/chat-attachments/image?file_id="
            f"{relative.as_posix()}"
        ),
    }


def save_workspace_attachment(
    payload: dict[str, Any], actor: str = ""
) -> dict[str, Any]:
    actor_id = _actor_id(actor)
    if not actor_id:
        raise PermissionError("需要登录后操作")
    resident_id = _visible_resident_id(payload.get("resident_id"), actor_id)
    data = _decode_upload(payload, CHAT_ATTACHMENT_MAX_BYTES)
    original_name = str(payload.get("filename") or "attachment").strip() or "attachment"
    content_type = (
        str(payload.get("content_type") or "application/octet-stream")
        .split(";", 1)[0]
        .strip()
        .lower()
        or "application/octet-stream"
    )
    suffix = Path(original_name).suffix.lower()
    if len(suffix) > 16 or not re.fullmatch(r"\.[A-Za-z0-9._-]+", suffix or ""):
        suffix = ""
    relative, absolute = _upload_path(
        CHAT_ATTACHMENT_CACHE_ROOT, actor_id, original_name, suffix
    )
    absolute.write_bytes(data)
    metadata = {
        "created_at": _now_iso(),
        "actor": actor_id,
        "resident_id": resident_id,
        "filename": original_name,
        "content_type": content_type,
        "size": len(data),
        "path": str(absolute),
        "file_id": relative.as_posix(),
    }
    resident_store.write_json(
        absolute.with_suffix(absolute.suffix + ".json"), metadata
    )
    return {
        "file_id": relative.as_posix(),
        "path": str(absolute),
        "filename": original_name,
        "content_type": content_type,
        "size": len(data),
    }


def resolve_workspace_pasted_image(
    file_id: str, actor: str = ""
) -> tuple[Path, str]:
    actor_id = _actor_id(actor)
    if not actor_id:
        raise PermissionError("需要登录后操作")
    relative = Path(str(file_id or "").strip().lstrip("/"))
    if not relative.parts:
        raise FileNotFoundError("image not found")
    if relative.is_absolute() or ".." in relative.parts:
        raise PermissionError("invalid image path")
    root = CHAT_IMAGE_CACHE_ROOT.resolve(strict=False)
    path = (root / relative).resolve(strict=False)
    path.relative_to(root)
    if not path.is_file():
        raise FileNotFoundError("image not found")
    authorize_attachment_read(path, actor_id, root=root)
    content_type, _suffix = image_type(path.name, "")
    return path, content_type


def _normalize_link_path(raw_path: str) -> Path:
    value = str(raw_path or "").strip()
    if value.startswith("file://"):
        value = value.removeprefix("file://")
    if value.startswith("/nas_ro/"):
        value = str(portal_path("share_root")) + "/" + value.removeprefix("/nas_ro/")
    value = re.sub(r":\d+(?::\d+)?$", "", value)
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("link path must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError("symlink preview is not supported")
    return path.resolve(strict=False)


def _authorize_link_path(path: Path, actor: str) -> _LinkFileAuthorization:
    """Read access to a linked file; ``can_write`` only tells the page whether to offer its Workspace address."""
    actor_id = _actor_id(actor)
    if not actor_id:
        raise PermissionError("workspace link requires an authenticated actor")

    for configured_root in (CHAT_IMAGE_CACHE_ROOT, CHAT_ATTACHMENT_CACHE_ROOT):
        root = configured_root.resolve(strict=False)
        if not path.is_relative_to(root):
            continue
        authorize_attachment_read(path, actor_id, root=root)
        return _LinkFileAuthorization(root=root, can_write=False, source="attachment")

    authorization = resident_domain_port.authorize_workspace_path(path, actor_id)
    return _LinkFileAuthorization(
        root=authorization["root"],
        can_write=bool(authorization["can_write"]),
        source=str(authorization["source"]),
    )


def _resolve_authorized_link_file_path(
    raw_path: str,
    actor: str,
) -> tuple[Path, _LinkFileAuthorization]:
    path = _normalize_link_path(raw_path)
    authorization = _authorize_link_path(path, actor)
    if not path.exists():
        raise FileNotFoundError("file not found")
    if path.is_symlink():
        raise ValueError("symlink preview is not supported")
    return path, authorization


def resolve_workspace_link_file_path(
    raw_path: str,
    allow_outside_roots: bool = False,
    actor: str = "",
) -> Path:
    del allow_outside_roots
    path, _authorization = _resolve_authorized_link_file_path(raw_path, actor)
    return path


def _file_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    suffix = path.suffix.lower()
    if suffix in {".md", ".mdx", ".markdown"}:
        return "markdown"
    if suffix == ".json":
        return "json"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix == ".pdf":
        return "pdf"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "binary"


def _entry(root: Path, path: Path, depth: int = 0) -> dict[str, Any]:
    try:
        stat = path.lstat() if path.is_symlink() else path.stat()
        size = int(stat.st_size)
        mtime = float(stat.st_mtime)
        mtime_ns = int(stat.st_mtime_ns)
    except OSError:
        size = 0
        mtime = 0.0
        mtime_ns = 0
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.name
    kind = _file_kind(path)
    return {
        "directory_id": "link",
        "path": f"link/{relative}" if relative else "link",
        "rel_path": relative,
        "name": path.name,
        "label": path.name,
        "type": kind,
        "size": 0 if kind == "directory" else size,
        "mtime": mtime,
        "mtime_ns": mtime_ns,
        "mtime_ns_text": str(mtime_ns),
        "depth": depth,
    }


def _preview_root(path: Path) -> Path | None:
    for root in _link_preview_roots():
        resolved = root.resolve(strict=False)
        try:
            path.relative_to(resolved)
            return resolved
        except ValueError:
            continue
    return None


def get_workspace_link_file(
    raw_path: str,
    actor: str = "",
    meta_only: bool = False,
    allow_outside_roots: bool = False,
) -> dict[str, Any]:
    del allow_outside_roots
    path, authorization = _resolve_authorized_link_file_path(raw_path, actor)
    return _workspace_link_file_payload(
        path,
        authorization,
        meta_only=meta_only,
    )


def _workspace_link_file_payload(
    path: Path,
    authorization: _LinkFileAuthorization,
    *,
    meta_only: bool = False,
) -> dict[str, Any]:
    preview_root = _preview_root(path)
    root = preview_root or path.parent
    entry = _entry(root, path)
    common = {
        "source": "workspace_link",
        "access_source": authorization.source,
        "directory_id": "link",
        "root_path": str(root),
        "path": str(path),
        "rel_path": path.relative_to(root).as_posix() if path != root else "",
        "entry": entry,
    }
    if meta_only:
        return {**common, "kind": _file_kind(path), "meta_only": True}
    if path.is_dir():
        children = []
        for child in sorted(
            path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
        ):
            if not child.is_symlink():
                children.append(_entry(root, child.resolve(strict=False)))
        return {
            **common,
            "kind": "directory",
            "children": children,
        }
    kind = _file_kind(path)
    data = path.read_bytes()[: FILE_PREVIEW_MAX_BYTES + 1]
    truncated = len(data) > FILE_PREVIEW_MAX_BYTES
    if truncated:
        data = data[:FILE_PREVIEW_MAX_BYTES]
    binary = kind == "binary" or kind in MEDIA_KINDS or b"\x00" in data[:2048]
    editable = kind in {"markdown", "json", "jsonl", "text"} and not binary and not truncated
    from .lineage_access_policy import workspace_file_address

    # UpdateWorkspaceFile takes only a Workspace address; "" when the file is in no registered Workspace.
    file_address = workspace_file_address(str(path)) if editable and authorization.can_write else ""
    return {
        **common,
        "file_address": file_address,
        # ``can_write`` asked UpdateWorkspaceFile on the file's Workspace, the instruction the save button runs.
        "allows": {"UpdateWorkspaceFile": bool(file_address)},
        "kind": kind,
        "binary": binary,
        "truncated": truncated,
        "content": "" if binary else data.decode("utf-8", errors="replace"),
        "raw_url": f"/api/agent-workbench/link-file-raw?path={quote(str(path), safe='')}",
    }
