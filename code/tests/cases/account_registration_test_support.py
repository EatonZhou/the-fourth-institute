"""Explicit execution registration fixtures; no host YAML or credentials."""
from pathlib import Path
import json

from management_portal.execution.account_registrations import create_registration
from management_portal.execution.account_adapters import CodexRoutes


def register_codex(monkeypatch, root, *, names=("zero", "one"), default="one", limit=2, fallbacks=()):
    from management_portal.execution.account_registrations import database
    with database().transaction("fixture") as c:
        c.execute("DELETE FROM backend_roles WHERE account_id IN (SELECT account_id FROM registrations WHERE provider='codex')")
        c.execute("DELETE FROM registrations WHERE provider='codex'")
    from tests.cases.instance_test_support import write_instance
    instance = Path(root).with_name(Path(root).name + "-fixture-instance")
    write_instance(instance)
    monkeypatch.setenv("PORTAL_INSTANCE_ROOT", str(instance))
    for name in names:
        create_registration({"account_id": f"codex_{name}", "provider": "codex", "kind": "subscription_login",
                             "route_id": name, "max_concurrency": limit,
                             "config": {"account_dir": str(Path(root) / "accounts" / f"codex_{name}")}}, "test")
    from management_portal.execution import account_selection, account_registrations
    def observed():
        return [{**row, "backend_route_id": row["route_id"], "login_state": "logged_in",
                 "quota_reserve": {"fresh": True, "status": "available", "remaining": 100 if row["route_id"] == default else 80,
                                   "windows": [{"remaining_percent": 100 if row["route_id"] == default else 80}]}}
                for row in account_registrations.registrations()]
    monkeypatch.setattr(account_selection, "observations", observed)
    return CodexRoutes()


def register_site_defaults(monkeypatch, root, *, legacy_codex_sockets=False):
    """Seed the isolated site's routes; HTTP APIs remain explicit test fixtures."""
    from management_portal.execution import account_hosting
    from management_portal.execution.account_registrations import update_role

    # Existing execution fixture users identify fake clients by these names.
    # Other cases, including host path contracts, use normal path derivation.
    if legacy_codex_sockets:
        monkeypatch.setattr(account_hosting, "codex_socket_path", lambda account_id:
                            Path(root) / "instances" / "sockets" / f"codex-app-server-{account_id.removeprefix('codex_')}.sock")
    routes = register_codex(
        monkeypatch, root, names=("zero", "one", "two", "three", "four", "five"), limit=10,
        fallbacks=("codex_zero",),
    )
    create_registration({"account_id": "claude_one", "provider": "claude", "kind": "subscription_login",
                         "route_id": "claude_one", "config": {"account_dir": str(Path(root) / "accounts" / "claude_one"), "models": [{"id": "sonnet"}],
                                                                 "default_model": "sonnet"}}, "test")
    for account_id, provider, route_id in (("deepseek_primary", "claude", "deepseek_primary"),
                                           ("dsh_deepseek", "dsh", "deepseek")):
        create_registration({"account_id": account_id, "provider": provider, "kind": "api_profile",
                             "route_id": route_id, "config": {"api_id": "deepseek_primary"}}, "test")
    create_registration({"account_id": "kimi_default", "provider": "kimi", "kind": "service"}, "test")
    update_role("default", "codex_one", "", "", 0, "test", "fixed")
    return routes
