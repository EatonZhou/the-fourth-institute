"""Read canonical runtime identity headers without substituting an identity."""
from __future__ import annotations


def runtime_identity_header(headers, canonical_name: str) -> str:
    # HTTPMessage and ASGI Headers both retain repeated header fields. Reject
    # ambiguity before a caller associates a request with a runtime identity.
    if callable(get_all := getattr(headers, "get_all", None)):
        raw = get_all(canonical_name) or []
    elif callable(getlist := getattr(headers, "getlist", None)):
        raw = getlist(canonical_name)
    else:
        value = headers.get(canonical_name)
        raw = [] if value is None else [value]
    values = [str(value).strip() for value in raw]
    if len(set(values)) > 1:
        raise ValueError("Conflicting runtime identity headers")
    return values[0] if values else ""
