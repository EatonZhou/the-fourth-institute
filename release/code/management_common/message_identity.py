from __future__ import annotations

import hashlib


def derived_message_identity(*coordinates: object) -> str:
    values = [str(value or "").strip() for value in coordinates]
    if not values or any(not value for value in values):
        raise ValueError("MessageIdentity coordinates are required")
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()
    return f"msg_{digest[:32]}"
