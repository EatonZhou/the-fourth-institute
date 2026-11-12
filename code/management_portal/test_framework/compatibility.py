"""Explicit installation-owned test identity."""
from __future__ import annotations

from management_common.site_config import read_site_config


def test_identity() -> tuple[str, str | None]:
    """Read the installation's account and optional required User category together."""
    from management_portal.permission_system.categories import CATEGORIES

    config = read_site_config().get("deployment", {}).get("test_framework", {})
    actor = config.get("actor", "test-actor")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Test actor must be an explicit nonempty identity")
    category = config.get("actor_category")
    if "actor_category" in config and (not isinstance(category, str) or category not in CATEGORIES):
        raise ValueError("Test actor_category must be portal_admin, member or intern")
    return actor.strip(), category


def test_actor() -> str:
    return test_identity()[0]
