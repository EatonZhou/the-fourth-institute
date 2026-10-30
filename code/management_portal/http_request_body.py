"""Reading Portal HTTP request bodies within their size limits: JSON objects, URL-encoded
forms and multipart uploads.
"""
from __future__ import annotations

import json
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import SimpleHTTPRequestHandler
from urllib.parse import parse_qsl


_MULTIPART_FORM_OVERHEAD_BYTES = 65_536
_MULTIPART_HARD_LIMIT_BYTES = 256 * 1024 * 1024
_JSON_REQUEST_MAX_BYTES = 4 * 1024 * 1024
_FILE_SAVE_JSON_MAX_BYTES = 2 * 1024 * 1024


class InvalidRequestBody(ValueError):
    pass


class RequestBodyTooLarge(InvalidRequestBody):
    pass


def _multipart_body_limit(payload_max_bytes: int) -> int:
    return min(
        _MULTIPART_HARD_LIMIT_BYTES,
        max(0, int(payload_max_bytes)) + _MULTIPART_FORM_OVERHEAD_BYTES,
    )


def _read_bounded_request_body(
    handler: SimpleHTTPRequestHandler,
    *,
    max_bytes: int,
    media_type: str,
) -> bytes:
    transfer_encoding = str(handler.headers.get("Transfer-Encoding") or "").strip()
    if transfer_encoding and transfer_encoding.casefold() != "identity":
        raise InvalidRequestBody("chunked request bodies are not supported")
    content_type = str(handler.headers.get("Content-Type") or "")
    actual_media_type = content_type.partition(";")[0].strip().casefold()
    if actual_media_type != media_type:
        raise InvalidRequestBody(f"Content-Type must be {media_type}")
    raw_length = str(handler.headers.get("Content-Length") or "").strip()
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestBody("invalid Content-Length") from exc
    if length < 0:
        raise InvalidRequestBody("invalid Content-Length")
    if length > max_bytes:
        raise RequestBodyTooLarge("request body is too large")
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise InvalidRequestBody("incomplete request body")
    if not raw:
        raise InvalidRequestBody("request body must not be empty")
    return raw


def _read_request_json(handler: SimpleHTTPRequestHandler) -> dict:
    return _read_bounded_json_object(handler)


def _read_bounded_form_object(
    handler: SimpleHTTPRequestHandler,
    *,
    max_bytes: int = 16_384,
) -> dict[str, str]:
    raw = _read_bounded_request_body(
        handler,
        max_bytes=max_bytes,
        media_type="application/x-www-form-urlencoded",
    )
    try:
        text = raw.decode("utf-8")
        return dict(
            parse_qsl(
                text,
                keep_blank_values=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=16,
            )
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidRequestBody("request body must be valid form data") from exc


def _read_bounded_json_object(
    handler: SimpleHTTPRequestHandler,
    *,
    max_bytes: int = _JSON_REQUEST_MAX_BYTES,
) -> dict:
    raw = _read_bounded_request_body(
        handler,
        max_bytes=max_bytes,
        media_type="application/json",
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRequestBody("request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidRequestBody("request JSON must be an object")
    return payload


def _read_request_multipart(
    handler: SimpleHTTPRequestHandler,
    *,
    max_bytes: int,
) -> dict:
    content_type = str(handler.headers.get("Content-Type") or "")
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid Content-Length") from exc
    if not content_type.casefold().startswith("multipart/form-data"):
        return {}
    if length < 0:
        raise ValueError("invalid Content-Length")
    effective_max_bytes = min(int(max_bytes), _MULTIPART_HARD_LIMIT_BYTES)
    if effective_max_bytes < 0:
        raise ValueError("invalid multipart request size limit")
    if length > effective_max_bytes:
        raise RequestBodyTooLarge("multipart request body is too large")
    body = handler.rfile.read(length)
    if len(body) != length:
        raise ValueError("incomplete multipart request body")
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: "
        + content_type.encode("latin-1")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    if not message.is_multipart():
        raise ValueError("invalid multipart request body")
    payload: dict[str, object] = {}
    for field in message.iter_parts():
        if field.get_content_disposition() != "form-data":
            continue
        key = str(field.get_param("name", header="content-disposition") or "")
        if not key:
            continue
        filename = str(field.get_filename() or "")
        data = field.get_payload(decode=True) or b""
        if filename or key == "file":
            payload["filename"] = filename or str(payload.get("filename") or "attachment")
            payload["content_type"] = str(
                field.get_content_type()
                or payload.get("content_type")
                or "application/octet-stream"
            )
            payload["data_bytes"] = data
            continue
        charset = field.get_content_charset() or "utf-8"
        payload[key] = data.decode(charset, errors="replace")
    return payload
