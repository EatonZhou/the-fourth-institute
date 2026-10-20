from __future__ import annotations

import hashlib
from typing import Any


def text_metrics(value: str) -> dict[str, Any]:
    encoded = value.encode("utf-8")
    return {"characters": len(value), "utf8_bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}
