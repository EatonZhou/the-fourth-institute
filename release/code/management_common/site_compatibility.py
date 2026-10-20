"""Explicit installation parameters; runtime aliases are retired.

Values belong to the private site configuration. Domain owners decide which
values are meaningful and which non-secret fields may be projected to clients.
Neither environment aliases nor historical format prefixes are read here.
"""
from __future__ import annotations

from typing import Any

from management_common.site_config import read_site_config


def _configuration() -> dict:
    deployment = read_site_config().get('deployment', {})
    if not isinstance(deployment, dict):
        raise ValueError('deployment must be an object')
    value = deployment.get('compatibility', {})
    if not isinstance(value, dict):
        raise ValueError('deployment.compatibility must be an object')
    return value


def format_namespaces() -> tuple[str, ...]:
    """The public configuration contains no additional accepted namespaces."""
    return ()


def compatibility_value(key: str, default: Any = None) -> Any:
    """Read an explicit installation parameter, without alias lookup or fallback.

    The existing configuration field stores site paths, services and identities.
    Its name does not enable historical protocol or format readers.
    """
    values = _configuration().get('domain_values', {})
    if not isinstance(values, dict):
        raise ValueError('compatibility.domain_values must be an object')
    return values.get(key, default)
