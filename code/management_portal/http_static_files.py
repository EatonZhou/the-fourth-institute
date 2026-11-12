"""Serving Portal static files: one cached, optionally gzipped payload per file version,
revalidating cache headers, content-versioned asset URLs and the authenticated
``/static/`` fallback.
"""
from __future__ import annotations

import gzip
import hashlib
from html import escape, unescape
import re
import threading
from collections import OrderedDict
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, unquote, urlparse, urlunparse

from management_portal import http_auth, http_instance, http_responses, http_static_versions
from management_portal.http_routes import RouteDefinition
from management_portal.http_validators import if_none_match_matches


ROOT = Path(__file__).resolve().parent


_STATIC_CACHE_CONTROL = "public, max-age=0, must-revalidate"
_STATIC_IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
_STATIC_PAYLOAD_CACHE_LIMIT = 512
_STATIC_PAYLOAD_CACHE_LOCK = threading.RLock()
_STATIC_PAYLOAD_CACHE: OrderedDict[
    tuple, tuple[bytes, str, str]
] = OrderedDict()
_STATIC_PAYLOAD_BUILD_LOCKS: dict[
    tuple, threading.Lock
] = {}

_STATIC_HTML_REFERENCE_RE = re.compile(
    rb'((?:src|href)=["\'])(/static/[^"\']+)(["\'])'
)


def _version_static_asset_urls(data: bytes) -> bytes:
    def replace(match: re.Match[bytes]) -> bytes:
        url = urlparse(unescape(match.group(2).decode("utf-8")))
        path = _static_fallback_file(url.path)
        if path is None:
            return match.group(0)
        try:
            payload, _, version = _cached_static_payload(path, gzip_variant=False)
        except OSError:
            return match.group(0)
        http_static_versions.remember(path, version, payload)
        query = [(key, value) for key, value in parse_qsl(url.query, keep_blank_values=True) if key != "portal_v"]
        query.append(("portal_v", version))
        versioned = urlunparse(url._replace(query=urlencode(query)))
        return match.group(1) + escape(versioned, quote=True).encode("utf-8") + match.group(3)

    return _STATIC_HTML_REFERENCE_RE.sub(replace, data)


def _static_cache_control(handler: SimpleHTTPRequestHandler, version: str) -> str:
    requested = str(
        (parse_qs(urlparse(handler.path).query).get("portal_v") or [""])[0]
    ).strip()
    # Only a digest of the bytes in this response is an immutable identity.
    if (
        http_instance._PORTAL_DEPLOYMENT == "release"
        and requested
        and requested == version
    ):
        return _STATIC_IMMUTABLE_CACHE_CONTROL
    return _STATIC_CACHE_CONTROL


def _cached_static_payload(
    path: Path,
    *,
    gzip_variant: bool,
) -> tuple[bytes, str, str]:
    """Read and encode one immutable file version once across HTTP workers."""

    stat = path.stat()
    key = (str(path), stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns, stat.st_size, bool(gzip_variant))
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
            version = "sha256-" + hashlib.sha256(data).hexdigest()
            payload = (
                gzip.compress(data, compresslevel=4, mtime=0),
                "gzip",
                version,
            ) if gzip_variant else (data, "", version)
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
    try:
        data, content_encoding, version = _cached_static_payload(path, gzip_variant=gzip_variant)
    except OSError:
        return http_responses._send_empty(handler, 404)
    requested = (parse_qs(urlparse(handler.path).query).get("portal_v") or [""])[0]
    if requested.startswith("sha256-") and requested != version:
        # Finish documents already in flight across deployment with the exact
        # bytes promised by their URLs, including after Web process replacement.
        previous = http_static_versions.recall(path, requested)
        if previous is None:
            return http_responses._serve_payload(handler, 409, b"Static asset version changed; reload the document", "text/plain", cache_control="no-store")
        data = gzip.compress(previous, compresslevel=4, mtime=0) if gzip_variant else previous
        content_encoding = "gzip" if gzip_variant else ""
        version = requested
    variant = "-gzip" if gzip_variant else ""
    etag = f'"{version}{variant}"'
    cache_control = _static_cache_control(handler, version)
    if if_none_match_matches(handler.headers.get("If-None-Match", ""), etag):
        handler.send_response(304)
        handler.send_header("Cache-Control", cache_control)
        handler.send_header("ETag", etag)
        if http_responses._compressible_content_type(content_type):
            handler.send_header("Vary", "Accept-Encoding")
        handler.end_headers()
        return
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
