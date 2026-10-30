"""File responses: media with byte ranges, downloads and resource content streamed from
its provider.
"""
from __future__ import annotations

import mimetypes
import os
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote

from management_portal import http_responses
from management_portal.http_media_range import plan_media_response


def _society_service_resolved_path(value: object, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError(f"SocietyService returned an invalid {label}")
    raw_value = os.fspath(value)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(f"SocietyService returned an empty {label}")
    return Path(raw_value).expanduser().resolve(strict=False)


def _serve_media_file(handler: SimpleHTTPRequestHandler, path: Path, content_type: str) -> None:
    try:
        stat = path.stat()
    except OSError:
        return http_responses._send_empty(handler, 404)
    response = plan_media_response(handler, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    response.send_headers(handler, content_type)
    if response.status not in {200, 206} or not http_responses._response_body_allowed(handler):
        return
    try:
        with path.open("rb") as f:
            f.seek(response.start)
            remaining = response.length
            while remaining > 0:
                chunk = f.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
    except (BrokenPipeError, ConnectionResetError):
        return


def _serve_authorized_resource_file(
    handler: SimpleHTTPRequestHandler,
    *,
    resource_service: object,
    address: str,
    actor: str,
    resolved: dict[str, object],
    content_type: str,
    download: bool,
) -> None:
    size = resolved.get("content_size")
    mtime_ns = resolved.get("content_mtime_ns")
    if type(size) is not int or size < 0 or type(mtime_ns) is not int or mtime_ns < 0:
        raise ValueError("resource provider did not return stable content metadata")
    response = plan_media_response(handler, size=size, mtime_ns=mtime_ns, download=download)
    # Files written by Residents never run in the Portal origin: only
    # browser-safe media display inline, everything else downloads.
    safe_headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}
    inline = content_type == "application/pdf" or content_type.startswith(("image/", "audio/", "video/"))
    if response.status in {304, 416}:
        response.send_headers(handler, content_type, extra_headers=safe_headers)
        return
    chunk_size = 1024 * 1024

    def read_chunk(chunk_start: int, chunk_end: int) -> bytes:
        data, _provider_type, current = resource_service.read_content_range(
            address,
            actor=actor,
            start=chunk_start,
            end=chunk_end,
        )
        current = current if isinstance(current, dict) else {}
        if (
            current.get("content_size") != size
            or current.get("content_mtime_ns") != mtime_ns
        ):
            raise RuntimeError("resource changed while its content was being read")
        expected = chunk_end - chunk_start + 1
        if len(data) != expected:
            raise RuntimeError("resource provider returned an incomplete content range")
        return data

    first_end = min(response.end, response.start + chunk_size - 1)
    first_chunk = read_chunk(response.start, first_end) if response.length else b""
    response.send_headers(
        handler,
        content_type,
        content_disposition=(
            _download_content_disposition(resolved.get("filename")) if download or not inline else ""
        ),
        extra_headers=safe_headers,
    )
    if not http_responses._response_body_allowed(handler):
        return
    try:
        if first_chunk:
            handler.wfile.write(first_chunk)
        offset = first_end + 1
        while offset <= response.end:
            chunk_end = min(response.end, offset + chunk_size - 1)
            handler.wfile.write(read_chunk(offset, chunk_end))
            offset = chunk_end + 1
    except (BrokenPipeError, ConnectionResetError):
        return


def _download_content_disposition(filename: object) -> str:
    filename = str(filename or "download").replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(
        "_" if ord(char) < 32 or ord(char) == 127 else char
        for char in filename
    ) or "download"
    ascii_filename = "".join(
        char
        if char.isascii() and (char.isalnum() or char in {".", "_", "-"})
        else "_"
        for char in filename
    ) or "download"
    return (
        f"attachment; filename=\"{ascii_filename}\"; "
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def _serve_download_file(handler: SimpleHTTPRequestHandler, path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return http_responses._send_empty(handler, 404)
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(size))
        handler.send_header(
            "Content-Disposition",
            _download_content_disposition(path.name),
        )
        handler.end_headers()
        if not http_responses._response_body_allowed(handler):
            return
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 256)
                if not chunk:
                    break
                handler.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError):
        return


def _media_content_type(path: Path) -> str:
    content_type = mimetypes.guess_type(str(path))[0] or ""
    if content_type == "application/pdf" or content_type.startswith(("audio/", "image/", "video/")):
        return content_type
    return ""
