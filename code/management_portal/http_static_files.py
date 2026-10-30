"""Serving Portal static files: one cached, optionally gzipped payload per file version,
revalidating cache headers, build-versioned asset URLs and the authenticated
``/static/`` fallback.
"""
from __future__ import annotations

import gzip
import re
import threading
from collections import OrderedDict
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from management_portal import http_auth, http_instance, http_responses
from management_portal.http_routes import RouteDefinition


ROOT = Path(__file__).resolve().parent


_STATIC_CACHE_CONTROL = "public, max-age=0, must-revalidate"
_STATIC_IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
_STATIC_PAYLOAD_CACHE_LIMIT = 512
_STATIC_PAYLOAD_CACHE_LOCK = threading.RLock()
_STATIC_PAYLOAD_CACHE: OrderedDict[
    tuple[str, int, int, bool], tuple[bytes, str]
] = OrderedDict()
_STATIC_PAYLOAD_BUILD_LOCKS: dict[
    tuple[str, int, int, bool], threading.Lock
] = {}

_STATIC_HTML_REFERENCE_RE = re.compile(
    rb'((?:src|href)=["\'])(/static/[^"\']+)(["\'])'
)


def _version_static_asset_urls(data: bytes) -> bytes:
    version = quote(str(http_instance._PORTAL_BUILD_ID or "").strip(), safe="").encode("ascii")
    if not version:
        return data

    def replace(match: re.Match[bytes]) -> bytes:
        url = match.group(2)
        separator = b"&" if b"?" in url else b"?"
        return match.group(1) + url + separator + b"portal_v=" + version + match.group(3)

    return _STATIC_HTML_REFERENCE_RE.sub(replace, data)


def _static_cache_control(handler: SimpleHTTPRequestHandler) -> str:
    requested = str(
        (parse_qs(urlparse(handler.path).query).get("portal_v") or [""])[0]
    ).strip()
    # Dev serves a mutable working tree even while its Web build ID is unchanged.
    if (
        http_instance._PORTAL_INSTANCE == "release"
        and requested
        and requested == str(http_instance._PORTAL_BUILD_ID or "").strip()
    ):
        return _STATIC_IMMUTABLE_CACHE_CONTROL
    return _STATIC_CACHE_CONTROL


def _cached_static_payload(
    path: Path,
    *,
    mtime_ns: int,
    size: int,
    gzip_variant: bool,
) -> tuple[bytes, str]:
    """Read and encode one immutable file version once across HTTP workers."""

    key = (str(path), int(mtime_ns), int(size), bool(gzip_variant))
    with _STATIC_PAYLOAD_CACHE_LOCK:
        cached = _STATIC_PAYLOAD_CACHE.get(key)
        if cached is not None:
            _STATIC_PAYLOAD_CACHE.move_to_end(key)
            return cached
        build_lock = _STATIC_PAYLOAD_BUILD_LOCKS.setdefault(key, threading.Lock())

    with build_lock:
        try:
            with _STATIC_PAYLOAD_CACHE_LOCK:
                cached = _STATIC_PAYLOAD_CACHE.get(key)
                if cached is not None:
                    _STATIC_PAYLOAD_CACHE.move_to_end(key)
                    return cached
            data = path.read_bytes()
            payload = (
                gzip.compress(data, compresslevel=4, mtime=0),
                "gzip",
            ) if gzip_variant else (data, "")
            with _STATIC_PAYLOAD_CACHE_LOCK:
                _STATIC_PAYLOAD_CACHE[key] = payload
                _STATIC_PAYLOAD_CACHE.move_to_end(key)
                while len(_STATIC_PAYLOAD_CACHE) > _STATIC_PAYLOAD_CACHE_LIMIT:
                    _STATIC_PAYLOAD_CACHE.popitem(last=False)
            return payload
        finally:
            with _STATIC_PAYLOAD_CACHE_LOCK:
                if _STATIC_PAYLOAD_BUILD_LOCKS.get(key) is build_lock:
                    _STATIC_PAYLOAD_BUILD_LOCKS.pop(key, None)


def _static_fallback_file(path: str) -> Path | None:
    """A static file outside the public route list, served with the same caching.

    The plain file handler sends only Last-Modified.  A browser then caches the
    file by heuristics (a tenth of its age, ~17 hours for a week-old file) and
    keeps running a stale copy after it changes, e.g. an old
    portal_user_avatar.js without the functions newer pages call.
    """
    if not path.startswith("/static/"):
        return None
    static_root = ROOT / "static"
    candidate = (static_root / unquote(path.removeprefix("/static/"))).resolve()
    if static_root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def _serve_static_file(
    handler: SimpleHTTPRequestHandler,
    path: Path,
    content_type: str,
) -> None:
    try:
        stat = path.stat()
    except OSError:
        return http_responses._send_empty(handler, 404)
    gzip_variant = (
        stat.st_size >= http_responses._RESPONSE_COMPRESSION_MIN_BYTES
        and http_responses._compressible_content_type(content_type)
        and http_responses._accepts_gzip(handler)
    )
    variant = "-gzip" if gzip_variant else ""
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}{variant}"'
    cache_control = _static_cache_control(handler)
    if etag in {
        token.strip()
        for token in str(handler.headers.get("If-None-Match") or "").split(",")
    }:
        handler.send_response(304)
        handler.send_header("Cache-Control", cache_control)
        handler.send_header("ETag", etag)
        if http_responses._compressible_content_type(content_type):
            handler.send_header("Vary", "Accept-Encoding")
        handler.end_headers()
        return
    try:
        data, content_encoding = _cached_static_payload(
            path,
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            gzip_variant=gzip_variant,
        )
    except OSError:
        return http_responses._send_empty(handler, 404)
    return http_responses._serve_encoded_payload(
        handler,
        200,
        data,
        content_type,
        cache_control=cache_control,
        content_encoding=content_encoding,
        etag=etag,
    )


def _route_private_static(request, parsed, _actor):
    static_file = _static_fallback_file(parsed.path)
    if static_file is None:
        return http_responses._send_empty(request, 404)
    return _serve_static_file(request, static_file, request.guess_type(str(static_file)))


def routes() -> tuple[RouteDefinition, ...]:
    """The static files outside the public list, for signed-in Users and Residents."""
    return (
        http_auth._principal_route("GET", "/static/", _route_private_static, "portal-static", match="prefix_fallback"),
    )
