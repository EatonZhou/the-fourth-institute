"""Resolve one enabled account for a candidate's final turn. Never copy conversation history."""
import json
import os
from pathlib import Path
import shutil
import tomllib

from management_common.file_permissions import atomic_write_private_bytes
from management_common.provider_account_paths import registered_home
from . import account_registrations, account_selection


def select_account(provider):
    rows = [r for r in account_registrations.registrations(provider) if r["enabled"]]
    samples = {a["account_id"]: a for a in account_selection.observations() if a["provider"] == provider}
    eligible = [samples[r["account_id"]] for r in rows if r["account_id"] in samples
                and account_selection.available(samples[r["account_id"]])]
    subscriptions = account_selection.candidates(
        [a for a in eligible if a.get("login_state") == "logged_in"],
        {"provider": provider, "kind": "subscription_login"})
    # A paused/expired subscription does not authorize switching to paid API billing.
    # API-only providers use their earliest enabled, available registration.
    choices = (subscriptions if any(r["kind"] == "subscription_login" for r in rows)
               else [a for a in eligible if a["kind"] == "api_profile"])
    if not choices:
        raise FileNotFoundError("No eligible registered account")
    sample = choices[0]
    row = next(r for r in rows if r["account_id"] == sample["account_id"])
    return row, (sample.get("selection_catalog") or {}).get("default_model", "")


def prepare(provider, env):
    home = Path(env["HOME"])
    if provider == "antigravity" and os.environ.get("AGY_TEST_PROFILE"):
        source = Path(os.environ["AGY_TEST_PROFILE"])
    else:
        account, model = select_account(provider)
        route = account["route_id"]
        env.update(TFI_PROBE_ROUTE=route, TFI_PROBE_MODEL=model)
        if provider in {"antigravity", "codex", "kimi"}:
            source = registered_home(account)

    if provider == "antigravity":
        directory = home / "antigravity-acp"
        directory.mkdir(mode=0o700)
        for filename in ("antigravity-oauth-token", "acp_token.json"):
            token = source / directory.name / filename
            if token.is_file():
                atomic_write_private_bytes(directory / filename, token.read_bytes())
        if not any(directory.iterdir()):
            raise FileNotFoundError("No native login")
    elif provider == "codex":
        from .codex_api_accounts import managed_config, render_toml
        from management_portal.ai_provider_registry import shared_ai_provider_registry
        auth = json.loads((source / "auth.json").read_text())
        if account["kind"] == "subscription_login":
            # A disposable client must never rotate the registered refresh token.
            auth["tokens"]["refresh_token"] = ""
        else:
            from management_portal.model_api_bridge import connection
            entry = shared_ai_provider_registry.get(account["config"]["api_id"])
            config = managed_config(entry)
            bridge = config["model_providers"]["portal_api"]
            if "env_key" in bridge:
                url, _, key = connection(entry, "openai_responses", account_id=account["account_id"])
                bridge["base_url"] = url
                env[bridge["env_key"]] = key
            atomic_write_private_bytes(home / "config.toml", render_toml(config).encode())
        atomic_write_private_bytes(home / "auth.json", json.dumps(auth).encode())
    elif provider == "claude":
        from management_portal import claude_provider_catalog
        profile = claude_provider_catalog.profile(route)
        if claude_provider_catalog.is_native(profile):
            login = claude_provider_catalog.login_environment(profile)
            # Keep transcripts in the disposable directory and use only the current access token.
            directory = Path(login.pop("CLAUDE_CONFIG_DIR"))
            oauth = json.loads((directory / ".credentials.json").read_text())["claudeAiOauth"]
            if not oauth.get("accessToken"):
                raise ValueError("Claude login has no current access token")
            env.update(login)
            env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth["accessToken"]
        else:
            from management_portal.model_api_bridge import connection
            from management_portal.model_api_wire import MESSAGES, upstream
            url, auth, key = connection(profile, MESSAGES, account_id=account["account_id"])
            env["ANTHROPIC_BASE_URL"] = url
            env["ANTHROPIC_API_KEY" if auth == "x-api-key" else "ANTHROPIC_AUTH_TOKEN"] = key
            if upstream(profile, MESSAGES) == MESSAGES:
                env["ANTHROPIC_CUSTOM_HEADERS"] = "\n".join(
                    f"{k}: {v}" for k, v in (profile.get("default_headers") or {}).items())
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    elif provider == "kimi":
        if account["kind"] == "subscription_login":
            # Keep native OAuth locking/refresh on the original credential store.
            # Session, server, tools and history live only under the disposable home.
            from .codex_api_accounts import render_toml
            native = tomllib.loads((source / "config.toml").read_text())
            provider_id = "managed:kimi-code"
            model = model or native["default_model"]
            env["TFI_PROBE_MODEL"] = model
            settings = {"default_model": model, "providers": {provider_id: native["providers"][provider_id]},
                        "models": {k: v for k, v in native["models"].items() if v.get("provider") == provider_id}}
            atomic_write_private_bytes(home / "config.toml", render_toml(settings).encode())
            for name in ("credentials", "oauth"):
                directory = source / name
                if directory.is_dir():
                    (home / name).symlink_to(directory, target_is_directory=True)
            if (source / "device_id").is_file():
                shutil.copyfile(source / "device_id", home / "device_id")
        else:
            from management_portal.kimi_provider_catalog import write_config
            write_config(account, home)
            if not model:
                env["TFI_PROBE_MODEL"] = tomllib.loads((home / "config.toml").read_text())["default_model"]
    elif provider == "opencode":
        from management_portal import opencode_provider_catalog as catalog
        service = catalog.service(route)
        directory = Path(env["XDG_DATA_HOME"]) / "opencode"
        directory.mkdir(mode=0o700, parents=True)
        atomic_write_private_bytes(directory / "auth.json", json.dumps({
            service: {"type": "api", "key": catalog.api_key(route)}}).encode())
        env.update(TFI_PROBE_SERVICE=service, TFI_PROBE_MODEL=model or catalog.catalog(route)["default_model"],
                   OPENCODE_DISABLE_AUTOUPDATE="1",
                   OPENCODE_DISABLE_PROJECT_CONFIG="1", OPENCODE_DISABLE_SHARE="1",
                   OPENCODE_CONFIG_CONTENT=json.dumps({"permission": "deny", "enabled_providers": [service],
                                                        "provider": catalog.provider_config(route)}))
    elif provider == "dsh":
        from management_portal.dsh_provider_catalog import profile
        # The isolated runner needs the bound route, not access to the registration database.
        atomic_write_private_bytes(home / "route.json", json.dumps(profile(route), default=str).encode())
