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
    monkeypatch.setenv("PORTAL_ACCOUNTS_ROOT", str(Path(root) / "accounts"))
    monkeypatch.setenv("PORTAL_INSTANCES_ROOT", str(Path(root) / "instances"))
    from management_common import portal_paths
    values = {**portal_paths.path_values(),
        "accounts_root": str(Path(root) / "accounts"), "instances_root": str(Path(root) / "instances")}
    # Installation configuration is outside the directory under test, just as
    # it is in an installed instance. Atomic-write tests can then inspect that
    # directory for leaked temporary files without seeing our fixture itself.
    config = Path(root).with_name(Path(root).name + "-fixture-paths.json")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(values))
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(config))
    monkeypatch.setattr(portal_paths, "CONFIG_PATH", config)
    for name in names:
        create_registration({"account_id": f"codex_{name}", "provider": "codex", "kind": "subscription_login",
                             "route_id": name, "max_concurrency": limit,
                             "config": {"is_default": name == default,
                                        **({"fallback_account_ids": list(fallbacks)} if name == default and fallbacks else {})}}, "test")
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
                         "route_id": "claude_one", "config": {"models": [{"id": "sonnet"}],
                                                                 "default_model": "sonnet"}}, "test")
    for account_id, provider, route_id in (("deepseek_primary", "claude", "deepseek_primary"),
                                           ("dsh_deepseek", "dsh", "deepseek")):
        create_registration({"account_id": account_id, "provider": provider, "kind": "api_profile",
                             "route_id": route_id, "config": {"api_id": "deepseek_primary"}}, "test")
    create_registration({"account_id": "kimi_default", "provider": "kimi", "kind": "service"}, "test")
    update_role("default", "codex_one", "", "", 0, "test")
    return routes
