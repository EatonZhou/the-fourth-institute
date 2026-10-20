from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED_VALUE = "[REDACTED]"

_SENSITIVE_FIELD_PARTS = frozenset({
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "key",
    "passwd",
    "password",
    "pwd",
    "secret",
    "token",
})
_SENSITIVE_COMPOUND_FIELDS = frozenset({
    "accesstoken",
    "apikey",
    "authorization",
    "authtoken",
    "clientsecret",
    "cookie",
    "credential",
    "credentials",
    "privatekey",
    "refreshtoken",
    "setcookie",
})
_SENSITIVE_HINTS = (
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "key",
    "passwd",
    "password",
    "pwd",
    "secret",
    "token",
)
_SENSITIVE_LABEL = (
    r"api[_-]?key|api[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"auth[_-]?token|"
    r"client[_-]?secret|private[_-]?key|authorization|credentials?|cookie|"
    r"password|passwd|pwd|secret|token"
)
_URL_PATTERN = re.compile(
    r"\b[a-z][a-z0-9+.-]*://[^\s<>\"']+",
    re.IGNORECASE,
)
_ASSIGNMENT_PATTERN = re.compile(
    rf"(?P<label>\b(?:{_SENSITIVE_LABEL})\b)"
    r"(?P<separator>\s*(?:=|:)\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;&]+)",
    re.IGNORECASE,
)
_JSON_ASSIGNMENT_PATTERN = re.compile(
    rf"(?P<prefix>[\"'](?:{_SENSITIVE_LABEL})[\"']\s*:\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^,}\s]+)",
    re.IGNORECASE,
)
_SESSION_COOKIE_PATTERN = re.compile(
    r"(?P<label>\b[A-Za-z0-9_.-]*(?:auth|cookie|sess(?:ion)?|token)[A-Za-z0-9_.-]*\b)"
    r"(?P<separator>\s*=\s*)"
    r"(?P<value>[^\s,;&\"']+)",
    re.IGNORECASE,
)
_CLI_ARGUMENT_PATTERN = re.compile(
    rf"(?P<prefix>--(?:{_SENSITIVE_LABEL})\s+)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s]+)",
    re.IGNORECASE,
)
_API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}")
_BEARER_PATTERN = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)


def _field_parts(value: object) -> tuple[str, ...]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or ""))
    return tuple(
        part
        for part in re.split(r"[^a-z0-9]+", text.casefold())
        if part
    )


def is_sensitive_field(value: object) -> bool:
    text = str(value or "")
    if not any(hint in text.casefold() for hint in _SENSITIVE_HINTS):
        return False
    parts = _field_parts(value)
    return bool(
        _SENSITIVE_FIELD_PARTS.intersection(parts)
        or "".join(parts) in _SENSITIVE_COMPOUND_FIELDS
    )


def _redact_url(match: re.Match[str]) -> str:
    value = match.group(0)
    try:
        parsed = urlsplit(value)
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = f"{REDACTED_VALUE}@{netloc.rsplit('@', 1)[1]}"
        query = urlencode([
            (
                key,
                REDACTED_VALUE if is_sensitive_field(key) else redact_sensitive_text(item),
            )
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
    except (TypeError, ValueError):
        return REDACTED_VALUE


def redact_sensitive_text(value: object) -> str:
    text = str(value or "")
    text = _API_KEY_PATTERN.sub("[REDACTED_API_KEY]", text)
    folded = text.casefold()
    if "://" not in text and not any(
        hint in folded for hint in _SENSITIVE_HINTS
    ):
        return text
    text = _URL_PATTERN.sub(_redact_url, text)
    text = _BEARER_PATTERN.sub(f"Bearer {REDACTED_VALUE}", text)
    text = _JSON_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group('prefix')}\"{REDACTED_VALUE}\"",
        text,
    )
    text = _CLI_ARGUMENT_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{REDACTED_VALUE}",
        text,
    )
    text = _ASSIGNMENT_PATTERN.sub(
        lambda match: (
            f"{match.group('label')}{match.group('separator')}{REDACTED_VALUE}"
        ),
        text,
    )
    text = _SESSION_COOKIE_PATTERN.sub(
        lambda match: (
            f"{match.group('label')}{match.group('separator')}{REDACTED_VALUE}"
        ),
        text,
    )
    return text


def redact_sensitive_data(value: object, *, _depth: int = 0) -> Any:
    if _depth >= 20:
        return REDACTED_VALUE
    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED_VALUE
                if is_sensitive_field(key)
                and not (key == "isSecret" and isinstance(item, bool))
                else redact_sensitive_data(item, _depth=_depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            redact_sensitive_data(item, _depth=_depth + 1)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            redact_sensitive_data(item, _depth=_depth + 1)
            for item in value
        )
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def redact_sensitive_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    redacted = redact_sensitive_data(value)
    return redacted if isinstance(redacted, dict) else {}
