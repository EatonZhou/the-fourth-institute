from __future__ import annotations

import re


_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _token(value: object, name: str) -> str:
    clean = str(value or "").strip()
    if not clean or not _TOKEN_RE.fullmatch(clean):
        raise ValueError(f"invalid notification {name}: {clean!r}")
    return clean


def notification_subject(
    domain: str,
    resource_id: str,
    *,
    scope: str = "",
    resource_type: str = "",
) -> str:
    clean_domain = _token(domain, "domain")
    clean_id = _token(resource_id, "resource id")
    if clean_domain == "resource":
        clean_type = _token(resource_type, "resource type")
        return f"portal.resource.changed.{clean_type}.{clean_id}"
    if clean_domain == "execution":
        clean_scope = _token(scope or "changed", "scope")
        return f"portal.execution.{clean_scope}.{clean_id}"
    if clean_domain == "resident":
        clean_scope = _token(scope or "identity", "scope")
        return f"portal.resident.changed.{clean_id}.{clean_scope}"
    if clean_domain == "lineage":
        if scope == "runtime":
            return f"portal.lineage.runtime.{clean_id}"
        return f"portal.lineage.changed.{clean_id}"
    raise ValueError(f"unsupported notification domain: {clean_domain}")


def resource_projection_subject(adapter: object) -> str:
    """Notify Portal that one ResourceService projection partition changed."""

    clean_adapter = _token(adapter, "resource adapter")
    return f"portal.resource.changed.projection.{clean_adapter}"


def business_projection_subject(projection: object, target_id: object) -> str:
    """Address a service-owned business projection change for Portal."""

    clean_projection = _token(projection, "business projection")
    clean_target = _token(target_id, "projection target")
    return f"portal.projection.changed.{clean_projection}.{clean_target}"
