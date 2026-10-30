"""Writing Portal HTTP responses: JSON and byte payloads (gzip when the client accepts it),
empty and OPTIONS replies, and the shared error answers.
"""
from __future__ import annotations

import gzip
import json
from http.server import SimpleHTTPRequestHandler
from urllib.parse import unquote

from management_common.operation_errors import OperationError
from management_portal.http_errors import (
    exception_http_error,
    instruction_error_payload as _hatg_instruction_error_payload,
    normalize_json_error,
)
from management_portal.resident_discovery_client import ResidentDiscoveryUnavailable
from management_portal.resources.errors import resource_error_http_response
from management_portal.society_service import SocietyServiceUnavailable


def _serve_society_service_unavailable(
    handler: SimpleHTTPRequestHandler,
    error: SocietyServiceUnavailable | None = None,
) -> None:
    failure = error if error is not None else SocietyServiceUnavailable("SocietyService 暂时不可用")
    return _serve_json(
        handler,
        failure.http_status,
        _hatg_instruction_error_payload(failure),
    )


def _serve_resource_error(handler: SimpleHTTPRequestHandler, error: Exception) -> None:
    status, payload = resource_error_http_response(error)
    return _serve_json(handler, status, payload)


_RESPONSE_COMPRESSION_MIN_BYTES = 1024

_SENSITIVE_RESPONSE_CACHE_CONTROL = "no-store"
_AUTHENTICATED_DOCUMENT_CACHE_CONTROL = "private, no-cache"


def _response_body_allowed(handler: SimpleHTTPRequestHandler) -> bool:
    return str(getattr(handler, "command", "") or "").upper() != "HEAD"


def _compressible_content_type(content_type: str) -> bool:
    mime = str(content_type or "").partition(";")[0].strip().casefold()
    return (
        mime.startswith("text/")
        or mime in {
            "application/javascript",
            "application/json",
            "application/manifest+json",
            "application/wasm",
            "application/xhtml+xml",
            "application/xml",
            "image/svg+xml",
        }
        or mime.endswith("+json")
        or mime.endswith("+xml")
    )


def _accepts_gzip(handler: SimpleHTTPRequestHandler) -> bool:
    accepted = str(handler.headers.get("Accept-Encoding") or "").casefold()
    for token in accepted.split(","):
        encoding, *parameters = token.split(";")
        if encoding.strip() != "gzip":
            continue
        quality = 1.0
        for parameter in parameters:
            key, separator, value = parameter.strip().partition("=")
            if separator and key == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        return quality > 0
    return False


def _encode_response_payload(
    handler: SimpleHTTPRequestHandler,
    data: bytes,
    content_type: str,
) -> tuple[bytes, str]:
    if (
        len(data) >= _RESPONSE_COMPRESSION_MIN_BYTES
        and _compressible_content_type(content_type)
        and _accepts_gzip(handler)
    ):
        return gzip.compress(data, compresslevel=4, mtime=0), "gzip"
    return data, ""


def _serve_payload(
    handler: SimpleHTTPRequestHandler,
    status: int,
    data: bytes,
    content_type: str,
    *,
    cache_control: str = _SENSITIVE_RESPONSE_CACHE_CONTROL,
    etag: str = "",
    extra_headers: dict[str, str] | None = None,
) -> None:
    if any(str(name).casefold() == "cache-control" for name in (extra_headers or {})):
        raise ValueError("Cache-Control must be selected through the response policy")
    encoded, content_encoding = _encode_response_payload(handler, data, content_type)
    return _serve_encoded_payload(
        handler,
        status,
        encoded,
        content_type,
        cache_control=cache_control,
        content_encoding=content_encoding,
        etag=etag,
        extra_headers=extra_headers,
    )


def _serve_encoded_payload(
    handler: SimpleHTTPRequestHandler,
    status: int,
    data: bytes,
    content_type: str,
    *,
    cache_control: str,
    content_encoding: str = "",
    etag: str = "",
    extra_headers: dict[str, str] | None = None,
) -> None:
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", cache_control)
        if _compressible_content_type(content_type):
            handler.send_header("Vary", "Accept-Encoding")
        if content_encoding:
            handler.send_header("Content-Encoding", content_encoding)
        if etag:
            handler.send_header("ETag", etag)
        for name, value in (extra_headers or {}).items():
            handler.send_header(str(name), str(value))
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        if _response_body_allowed(handler):
            handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        return


def _serve_json(
    handler: SimpleHTTPRequestHandler,
    status: int,
    payload: dict | list,
    *,
    extra_headers: dict[str, str] | None = None,
):
    data = json.dumps(normalize_json_error(status, payload), ensure_ascii=False).encode("utf-8")
    return _serve_payload(
        handler,
        status,
        data,
        "application/json; charset=utf-8",
        extra_headers=extra_headers,
    )


def _serve_exception_json(handler: SimpleHTTPRequestHandler, error: Exception):
    status, payload = exception_http_error(error)
    return _serve_json(handler, status, payload)


def _is_json_api_path(path: str) -> bool:
    return path.startswith("/api/") or path.endswith("/api") or "/api/" in path


def _serve_http_error(handler: SimpleHTTPRequestHandler, status: int, message: str):
    return _serve_json(handler, status, {"error": message})


def _serve_unavailable_json(handler: SimpleHTTPRequestHandler):
    return _serve_exception_json(
        handler,
        OperationError(
            "Portal service temporarily unavailable",
            code="operation.unavailable",
            http_status=503,
        ),
    )


def _serve_link_file_permission_denied(handler: SimpleHTTPRequestHandler):
    return _serve_json(handler, 403, {"error": "file access denied"})


def _serve_agent_workbench_error(
    handler: SimpleHTTPRequestHandler,
    error: Exception,
    *,
    public_error: str | None = None,
):
    if isinstance(error, ResidentDiscoveryUnavailable):
        error = OperationError(str(error), code="operation.unavailable", http_status=503)
    status, payload = exception_http_error(error)
    if public_error is not None:
        payload.update(error=public_error, message=public_error, details={})
    return _serve_json(handler, status, payload)


def _send_empty(handler: SimpleHTTPRequestHandler, status: int):
    try:
        handler.send_response(status)
        handler.end_headers()
    except (BrokenPipeError, ConnectionResetError):
        return


def _serve_resource_portal_not_found(
    handler: SimpleHTTPRequestHandler,
    address: object,
):
    clean_address = unquote(str(address or "")).strip().strip("/")
    return _serve_json(
        handler,
        404,
        {
            "error": (
                f"resource has no Portal detail surface: {clean_address}"
                if clean_address
                else "resource has no Portal detail surface"
            )
        },
    )


def _send_options(handler: SimpleHTTPRequestHandler):
    try:
        handler.send_response(204)
        handler.send_header("Allow", "GET, POST, HEAD, OPTIONS")
        handler.send_header("Access-Control-Allow-Origin", handler.headers.get("Origin") or "*")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
        requested_headers = handler.headers.get("Access-Control-Request-Headers")
        if requested_headers:
            handler.send_header("Access-Control-Allow-Headers", requested_headers)
        handler.send_header("Access-Control-Max-Age", "600")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
    except (BrokenPipeError, ConnectionResetError):
        return
