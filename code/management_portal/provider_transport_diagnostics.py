from __future__ import annotations

from management_common.format_tags import schema_matches

import json
import math
import ssl
import time
from datetime import datetime, timezone
import urllib.error
from collections.abc import Callable
from typing import Any



SCHEMA = "tfi.provider-transport.v1"
PHASES = {"opening_response", "reading_response_body", "decoding_response_json"}
ERROR_KINDS = {"none", "timeout", "read_timeout", "http_error", "tls_error", "connection_error", "url_error", "invalid_json", "unknown"}


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> int | float | None:
    return value if type(value) in {int, float} and 0 <= value <= 10**18 and math.isfinite(value) else None


def _instant(value: Any) -> str | None:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.astimezone(timezone.utc).isoformat() if moment.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def sanitize_transport_observation(value: Any) -> dict | None:
    """The persisted/public transport contract contains no arbitrary strings or headers."""
    value = _mapping(value)
    phase = value.get("phase")
    outcome = value.get("outcome")
    if (not schema_matches(value.get("schema"), "tfi.provider-transport.v1") or not isinstance(phase, str) or phase not in PHASES
            or not isinstance(outcome, str) or outcome not in {"completed", "failed"}):
        return None
    request, response = _mapping(value.get("request")), _mapping(value.get("response"))
    status = response.get("http_status")
    kind = value.get("error_kind")
    return {
        "schema": SCHEMA, "source": "recorded_http_transport", "phase": phase, "outcome": outcome,
        "attempt_number": _number(value.get("attempt_number")),
        "started_at": _instant(value.get("started_at")),
        "finished_at": _instant(value.get("finished_at")),
        "error_kind": kind if isinstance(kind, str) and kind in ERROR_KINDS else "unknown",
        "request": {"characters": _number(request.get("characters")), "utf8_bytes": _number(request.get("utf8_bytes")),
                    "timeout_seconds": _number(request.get("timeout_seconds"))},
        "response": {"headers_received": response.get("headers_received") is True,
                     "http_status": status if type(status) is int and 100 <= status <= 599 else None,
                     "body_complete": response.get("body_complete") is True,
                     "body_bytes": _number(response.get("body_bytes"))},
        "durations_ms": {key: _number(_mapping(value.get("durations_ms")).get(key))
                         for key in (*sorted(PHASES), "total")},
    }


class ProviderTransportObservation:
    """Observe the existing single non-streaming HTTP attempt without changing its IO."""

    def __init__(self, serialized_payload: bytes, timeout_seconds: float):
        self.started = self.phase_started = time.monotonic()
        self.phase = "opening_response"
        self.value = {
            "schema": SCHEMA,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "request": {"characters": len(serialized_payload.decode("utf-8")), "utf8_bytes": len(serialized_payload),
                        "timeout_seconds": timeout_seconds},
            "response": {"headers_received": False, "http_status": None, "body_complete": False, "body_bytes": None},
            "durations_ms": {},
        }

    def _end_phase(self) -> float:
        now = time.monotonic()
        self.value["durations_ms"][self.phase] = max(0, (now - self.phase_started) * 1000)
        return now

    def enter(self, phase: str) -> None:
        self.phase_started = self._end_phase()
        self.phase = phase

    def headers_received(self, status: Any) -> None:
        self.value["response"].update(headers_received=True, http_status=status)

    def body_received(self, body: bytes) -> None:
        self.value["response"].update(body_complete=True, body_bytes=len(body))
        self.value["finished_at"] = datetime.now(timezone.utc).isoformat()

    def finish(self, failure: Exception | None, observer: Callable[[dict], None] | None) -> None:
        if self.value["finished_at"] is None:
            self.value["finished_at"] = datetime.now(timezone.utc).isoformat()
        now = self._end_phase()
        self.value["durations_ms"]["total"] = max(0, (now - self.started) * 1000)
        kind = "none" if failure is None else "unknown"
        reason = failure.reason if isinstance(failure, urllib.error.URLError) else failure
        if isinstance(failure, urllib.error.HTTPError):
            kind = "http_error"
        elif isinstance(reason, TimeoutError):
            kind = "read_timeout" if self.phase == "reading_response_body" else "timeout"
        elif isinstance(reason, ssl.SSLError):
            kind = "tls_error"
        elif isinstance(reason, ConnectionError):
            kind = "connection_error"
        elif isinstance(failure, urllib.error.URLError):
            kind = "url_error"
        elif isinstance(failure, json.JSONDecodeError):
            kind = "invalid_json"
        self.value.update(phase=self.phase, outcome="completed" if failure is None else "failed", error_kind=kind)
        if observer is not None:
            try:
                observer(sanitize_transport_observation(self.value))
            except Exception:
                # Telemetry cannot replace the request outcome, or trigger a retry.
                pass
