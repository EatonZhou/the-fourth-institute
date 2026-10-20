"""Explicit API, execution account and relay fixtures in the isolated test database."""
from __future__ import annotations

import json
from typing import Any

from management_portal import model_api_store


# The DeepSeek registration that Claude's default route and the DSH route use.
DEEPSEEK_PRIMARY = {
    "id": "deepseek_primary", "kind": "openai_compatible_chat", "enabled": True, "provider": "deepseek",
    "base_url": "https://deepseek.invalid", "api_key_file": "/nonexistent/deepseek-test-key",
    "default_model": "deepseek-v4-pro",
    "models": [{"id": "deepseek-v4-pro", "enabled": True, "support_efforts": ["low", "high", "max"]},
               {"id": "deepseek-v4-flash", "enabled": True, "support_efforts": ["low", "high", "max"]}],
}
# The Moonshot registration the Kimi Code server's default profile names.
KIMI_PRIMARY = {
    "id": "kimi_primary", "kind": "openai_compatible_chat", "enabled": True, "provider": "moonshot",
    "base_url": "https://moonshot.invalid/v1", "api_key_file": "/nonexistent/kimi-test-key",
    "default_model": "kimi-k2.6", "models": [{"id": "kimi-k2.6", "enabled": True}],
}


def claude_subscription(route_id: str, config_dir: str) -> dict[str, Any]:
    """A Claude subscription route registration for an account directory."""

    return {"id": route_id, "kind": "claude_code_subscription", "enabled": True, "provider": "anthropic",
            "config_dir": config_dir, "default_model": "sonnet", "models": [{"id": "sonnet", "enabled": True}]}


def register_apis(*entries: dict[str, Any]) -> None:
    """Replace HTTP API fixtures; execution accounts are registered separately."""

    for entry, _version in model_api_store.apis():
        model_api_store.delete_api(entry["id"])
    for entry in entries:
        assert model_api_store.create_api(entry, "test")


def register_accounts(*registrations: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Create explicit execution identities without host directories or services.

    API metadata alone never discovers an execution account. Cases choose the
    Provider, route, API binding and initial capacity in each registration.
    The case database fixture owns cleanup; existing accounts are not replaced.
    """
    from management_portal.execution import account_registrations

    created = []
    for registration in registrations:
        row = account_registrations.create_registration(registration, "test")
        assert row is not None, f"duplicate fixture account: {registration['account_id']}"
        created.append(row)
    return tuple(created)


def register_relay_resources(*resources: dict[str, Any]) -> None:
    """Replace every AI relay resource with ``resources``, in order.

    Portal has no instruction that writes relay resources; their rows come
    from the registry migration.
    """

    with model_api_store.database().transaction("relay_resources") as connection:
        connection.execute("DELETE FROM relay_resources")
        for position, resource in enumerate(resources):
            connection.execute(
                "INSERT INTO relay_resources(resource_id,position,resource_json,updated_at,updated_by) "
                "VALUES (%s,%s,%s::jsonb,%s,'test')",
                (resource["id"], position, json.dumps(model_api_store.body(resource)), model_api_store.now()),
            )
