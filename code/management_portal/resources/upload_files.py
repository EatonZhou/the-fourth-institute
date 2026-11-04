"""Uploads as authenticated files, without introducing another resource identity.

The upload owner owns address validation, readiness and no-follow byte access.
ResourceService and HTTP use this boundary; upload creation keeps its existing
mutable metadata protocol in isa.read_upload.
"""
from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlsplit

from management_common import secure_paths

_HANDLE = re.compile(r"upload_[0-9a-f]{24}\Z")
_URL_PATH = re.compile(r"/api/uploads/(upload_[0-9a-f]{24})\Z")


def _origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("Upload URL requires an official Portal origin")
    return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


def _origins():
    from management_common.instance_manifest import current_instance
    from management_portal.portal_session_auth import authentication_setting

    instance = current_instance()
    origins = {_origin(instance.internal_web_origin())}
    public = authentication_setting("public_origin")
    if public:
        origins.add(_origin(public))
    return origins


def file_reference(address):
    """Return the canonical upload reference, or None for another address domain.

    No network request is made. Invalid upload-looking addresses fail closed;
    URLs from other sites never become references to this instance's files.
    """
    if isinstance(address, Mapping):
        if "upload_id" not in address:
            return None
        if set(address) != {"upload_id"}:
            raise ValueError("Upload file reference accepts only upload_id")
        handle = address["upload_id"]
    elif isinstance(address, str):
        if not (address.startswith(("http:", "https:", "//", "/api/uploads/"))):
            return None
        if any(ord(char) < 32 for char in address) or "\\" in address:
            raise ValueError("Invalid upload URL")
        parsed = urlsplit(address)
        match = _URL_PATH.fullmatch(parsed.path)
        if not match or parsed.fragment or "#" in address:
            raise ValueError("Invalid upload URL path or fragment")
        if parsed.netloc or parsed.scheme:
            if _origin(address) not in _origins():
                raise ValueError("Upload URL belongs to another Portal origin")
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if query and query != [("download", "1")]:
            raise ValueError("Unsupported upload URL query")
        handle = match.group(1)
    else:
        return None
    if not isinstance(handle, str) or not _HANDLE.fullmatch(handle):
        raise ValueError("Invalid upload handle")
    return {"upload_id": handle}


def authorize(address, *, actor):
    """Domain read rule: every active authenticated User or Resident may read."""
    reference = file_reference(address)
    if reference is None:
        raise ValueError("Expected an upload file reference")
    from .access import require_identity

    require_identity(actor)
    return reference


def _regular(info):
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Upload content and metadata must be regular files without symlinks")


@contextmanager
def _target(address, *, actor, content=False):
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    from .isa import upload_root

    reference = authorize(address, actor=actor)
    handle = reference["upload_id"]
    root = upload_root()
    if get_directory_cleanup_store().pending_at(root / handle):
        raise FileNotFoundError(handle)
    with secure_paths.open_directory(root, (handle,)) as directory:
        _regular(secure_paths.stat_child(directory, "meta.json"))
        with os.fdopen(secure_paths.open_child(directory, "meta.json", os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)), "rb") as stream:
            _regular(os.fstat(stream.fileno()))
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("Upload metadata exceeds its size limit")
        meta = json.loads(raw)
        if not isinstance(meta, dict) or meta.get("ready") is not True:
            raise FileNotFoundError(handle)
        name = meta.get("file_name")
        if (not isinstance(name, str) or name in {"", ".", "..", "meta.json"}
                or any(char in name for char in ("/", "\\", "\0"))):
            raise ValueError("Invalid upload file name")
        info = secure_paths.stat_child(directory, name)
        _regular(info)
        if not content:
            yield None, _metadata(reference, meta, info)
            return
        with os.fdopen(secure_paths.open_child(directory, name, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)), "rb") as stream:
            info = os.fstat(stream.fileno())
            _regular(info)
            yield stream, _metadata(reference, meta, info)


def _metadata(reference, meta, info):
    from .providers.folder_files import metadata

    name = meta["file_name"]
    url = "/api/uploads/" + reference["upload_id"]
    return {
        **metadata(info, name), "domain": "upload", "address": url,
        "file_reference": reference, "exists": True, "target_kind": "file",
        "title": name, "filename": name, "content_size": info.st_size,
        "content_mtime_ns": info.st_mtime_ns,
        "media_type": str(meta.get("media_type") or "application/octet-stream"),
        "portal_url": url, "content_url": url,
    }


def resolve(address, *, actor):
    """Metadata only: never opens or reads the payload."""
    with _target(address, actor=actor) as (_, resolved):
        return resolved


def reference(address, *, actor):
    resolved = resolve(address, actor=actor)
    return {**resolved, "resource_id": "", "resource_type": "", "status": "resolved",
            "content_kind": resolved["kind"], "url": resolved["content_url"],
            "capabilities": {"download": True, "edit": False, "save": False}}


def read_content(address, *, actor, max_bytes=4 * 1024 * 1024):
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    with _target(address, actor=actor, content=True) as (stream, resolved):
        if resolved["content_size"] > max_bytes:
            raise ValueError("Upload exceeds the byte content limit")
        data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Upload exceeds the byte content limit")
        return data, resolved["media_type"], resolved


def read_content_range(address, *, actor, start, end):
    if type(start) is not int or type(end) is not int or start < 0 or end < start:
        raise ValueError("content range requires non-negative inclusive start and end")
    with _target(address, actor=actor, content=True) as (stream, resolved):
        stream.seek(start)
        return stream.read(end - start + 1), resolved["media_type"], resolved


def remove_upload(upload_id: str) -> dict:
    from management_common.upload_retention import deleted
    from management_portal.hatg.contracts import InstructionError
    from .isa import _upload_dir, read_upload

    directory = _upload_dir(upload_id)
    try:
        read_upload(upload_id)
    except InstructionError as error:
        if error.code != "instruction.not_found":
            raise
        # The browser may retry a removal after its response was lost.
        return {"upload_id": upload_id, "deleted": False}
    deleted(directory)
    return {"upload_id": upload_id, "deleted": True}
