"""Explicit installation-owned test identity."""
from __future__ import annotations

from management_common.site_compatibility import compatibility_value


def test_actor() -> str:
    actor = compatibility_value("test_framework.actor", "test-actor")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Test actor must be an explicit nonempty identity")
    return actor.strip()
