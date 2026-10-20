"""Conditional and byte-range response policy for Portal media."""
from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse


def parse_byte_range(value: str, size: int) -> tuple[int, int] | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    unit, separator, raw_range = raw_value.partition("=")
    if unit.strip().lower() != "bytes" or not separator or "," in raw_range:
        raise ValueError("unsupported byte range")
    raw_start, dash, raw_end = raw_range.strip().partition("-")
    raw_start = raw_start.strip()
    raw_end = raw_end.strip()
    if not dash or (not raw_start and not raw_end) or size <= 0:
        raise ValueError("invalid byte range")
    if raw_start:
        if not raw_start.isdigit() or (raw_end and not raw_end.isdigit()):
            raise ValueError("invalid byte range")
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
        if start >= size or end < start:
            raise ValueError("unsatisfiable byte range")
        return start, min(end, size - 1)
    if not raw_end.isdigit():
        raise ValueError("invalid byte range")
    suffix_length = int(raw_end)
    if suffix_length <= 0:
        raise ValueError("invalid byte range")
    length = min(suffix_length, size)
    return size - length, size - 1


@dataclass(frozen=True)
class MediaResponse:
    status: int
    size: int
    start: int
    end: int
    etag: str
    cache_control: str

    @property
    def length(self) -> int:
        return max(0, self.end - self.start + 1)

    def send_headers(
        self,
        handler: BaseHTTPRequestHandler,
        content_type: str,
        *,
        content_disposition: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        handler.send_response(self.status)
        for name, value in (extra_headers or {}).items():
            handler.send_header(name, value)
        if self.status != 304:
            handler.send_header("Accept-Ranges", "bytes")
        if self.status in {200, 206}:
            handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", self.cache_control)
        handler.send_header("ETag", self.etag)
        if self.status == 416:
            handler.send_header("Content-Range", f"bytes */{self.size}")
            handler.send_header("Content-Length", "0")
        elif self.status in {200, 206}:
            handler.send_header("Content-Length", str(self.length))
            if self.status == 206:
                handler.send_header("Content-Range", f"bytes {self.start}-{self.end}/{self.size}")
            if content_disposition:
                handler.send_header("Content-Disposition", content_disposition)
        handler.end_headers()


def plan_media_response(
    handler: BaseHTTPRequestHandler,
    *,
    size: int,
    mtime_ns: int,
    download: bool = False,
) -> MediaResponse:
    etag = f'"{mtime_ns:x}-{size:x}"'
    query = parse_qs(urlparse(str(getattr(handler, "path", "") or "")).query)
    requested_version = str((query.get("_preview_mtime") or [""])[0]).strip()
    cache_control = (
        "no-store" if download else
        "private, max-age=31536000, immutable"
        if requested_version == str(mtime_ns) else "private, no-cache"
    )
    range_header = str(handler.headers.get("Range", "") or "").strip()
    if_range = str(handler.headers.get("If-Range", "") or "").strip()
    if range_header and if_range and if_range != etag:
        range_header = ""
    if not download and not range_header and etag in {
        token.strip()
        for token in str(handler.headers.get("If-None-Match") or "").split(",")
    }:
        return MediaResponse(304, size, 0, size - 1, etag, cache_control)
    try:
        byte_range = parse_byte_range(range_header, size)
    except ValueError:
        return MediaResponse(416, size, 0, size - 1, etag, cache_control)
    start, end = byte_range if byte_range is not None else (0, size - 1)
    return MediaResponse(206 if byte_range is not None else 200, size, start, end, etag, cache_control)
