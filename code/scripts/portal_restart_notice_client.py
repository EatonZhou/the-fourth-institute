from __future__ import annotations

import json
import urllib.request
from typing import Any


def publish_restart_notice(
    url: str,
    *,
    seconds: int,
    message: str,
    services: list[str],
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps({
            "seconds": int(seconds),
            "message": str(message)[:240],
            "services": [str(item) for item in services if str(item)],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    notice = payload.get("notice") if isinstance(payload, dict) else None
    if not (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("projection_published") is True
        and isinstance(notice, dict)
        and notice.get("active") is True
    ):
        raise RuntimeError("Portal restart notice was not projected")
    return dict(notice)


def drain_realtime(
    url: str,
    *,
    timeout_seconds: float = 4.0,
) -> dict[str, Any]:
    """Request realtime drain and return only after the server confirms closure."""

    request = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    confirmed = (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("drained") is True
        and int(payload.get("remaining_realtime_connections") or 0) == 0
    )
    if not confirmed:
        raise RuntimeError("Portal realtime connections were not drained")
    return dict(payload)
