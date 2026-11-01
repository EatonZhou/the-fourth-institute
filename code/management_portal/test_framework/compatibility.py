"""Explicit installation-owned test identity."""
from __future__ import annotations

from management_common.site_config import read_site_config


def test_actor() -> str:
    actor = read_site_config().get("deployment", {}).get("test_framework", {}).get("actor", "test-actor")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Test actor must be an explicit nonempty identity")
    return actor.strip()
