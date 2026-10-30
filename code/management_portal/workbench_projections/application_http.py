"""Authenticated, revision-bound application assets; never execute raw HTML."""
from __future__ import annotations

import re
from urllib.parse import unquote

from .store import ProjectionError


PREFIX = "/api/workbenches/assets/"
LIBRARY_PREFIX = "/api/workbenches/libraries/"
_ASSET = re.compile(r"(workbench_[0-9a-f]{12})/([1-9][0-9]*)/([0-9a-f]{64})/(.+)")
_LIBRARY = re.compile(r"(workbench_[0-9a-f]{12})/([1-9][0-9]*)(?:/(.+))?")


def _relative(encoded):
    try:
        relative = unquote(encoded, errors="strict")
    except UnicodeError as error:
        raise ProjectionError("projection.asset", "应用包产物路径无效", 404) from error
    if any(part in {"", ".", ".."} for part in relative.split("/")) or any(char in relative for char in "\\\x00?#%"):
        raise ProjectionError("projection.asset", "应用包产物路径无效", 404)
    return relative


def _headers(digest):
    return {
        "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-origin",
        "ETag": '"' + digest + '"',
    }


def read_asset(path: str, actor: str, store):
    """The browser supplies identity/path only, never a build or storage path."""
    match = _ASSET.fullmatch(path.removeprefix(PREFIX)) if path.startswith(PREFIX) else None
    if not match:
        raise ProjectionError("projection.asset", "应用包产物地址无效", 404)
    app_id, revision, digest, encoded = match.groups()
    relative = _relative(encoded)
    app = store.get_revision(app_id, actor, int(revision))
    build = app.get("application_build") or {}
    if build.get("status") != "ready" or build.get("artifact_hash") != digest or relative not in build.get("files", {}):
        raise ProjectionError("projection.asset", "此修订没有该应用包产物", 404)
    from .application_build import read_artifact

    content, content_type = read_artifact(build, relative)
    return content, content_type, _headers(build["files"][relative]["sha256"])


def serve_asset(handler, path: str, actor: str, store):
    # _serve_bytes injects the Portal shell into HTML. Package bytes must remain
    # exact and inert when opened directly; only the sandbox host bootstraps them.
    from management_portal.http_responses import _serve_payload

    content, content_type, headers = read_asset(path, actor, store)
    return _serve_payload(handler, 200, content, content_type,
                          cache_control="private, no-store", extra_headers=headers)


def serve_library(handler, path: str, actor: str, store):
    """Expose the reviewed catalog and its exact files, never an arbitrary path."""
    from management_portal.http_responses import _serve_json, _serve_payload

    match = _LIBRARY.fullmatch(path.removeprefix(LIBRARY_PREFIX)) if path.startswith(LIBRARY_PREFIX) else None
    if not match:
        raise ProjectionError("projection.library", "登记库地址无效", 404)
    app_id, revision, encoded = match.groups()
    relative = _relative(encoded) if encoded else None
    app = store.get_revision(app_id, actor, int(revision))
    if (app.get("application_build") or {}).get("status") != "ready":
        raise ProjectionError("projection.library", "登记库需要可运行的应用包修订", 404)
    from .libraries import read_library, registry

    catalog = registry()
    if relative is None:
        return _serve_json(handler, 200, catalog)
    metadata = next((item for library in catalog.values() for item in library["files"] if item["path"] == relative), None)
    if metadata is None:
        raise ProjectionError("projection.library", "登记库文件不存在", 404)
    content, content_type = read_library(relative)
    return _serve_payload(handler, 200, content, content_type,
                          cache_control="private, no-store", extra_headers=_headers(metadata["sha256"]))
