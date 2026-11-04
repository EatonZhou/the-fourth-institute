#!/usr/bin/env python3
from __future__ import annotations
from management_common.portal_paths import path as portal_path

import json
import os
import sys
import tomllib
from pathlib import Path

from management_common.postgres_database import close_all_database_pools
from management_common.process_runtime import run_process
from management_common.site_config import read_site_config
from management_common.code_environment import provider_entry
from management_portal import kimi_portal_tools, kimi_windows_runtime
from management_portal.ai_provider_registry import AIProviderRegistry


def settings() -> dict:
    return read_site_config().get('deployment', {}).get('kimi', {})
DEFAULT_BINARY = provider_entry("kimi_runtime_current") / (
        "kimi.cmd" if sys.platform == "win32" else "bin/kimi")
DEFAULT_HOME = portal_path("provider-state") / "kimi-code"
DEFAULT_CONFIG_TEMPLATE = Path(__file__).resolve().parents[1] / "systemd" / "portal-kimi-code-config.toml"


def model_catalog() -> dict:
    template = DEFAULT_CONFIG_TEMPLATE
    text = template.read_text(encoding="utf-8")
    for placeholder in ("__PORTAL_KIMI_DEFAULT_MODEL__", "__PORTAL_KIMI_BASE_URL__", "__PORTAL_KIMI_API_KEY__"):
        text = text.replace(placeholder, '""')
    entries = tomllib.loads(text).get("models") or {}
    models = list(entries)
    if not models:
        raise ValueError("Kimi Code config template has no models")
    default = str(settings().get("default_model", "")).strip() or models[0]
    if default not in models:
        raise ValueError("Kimi Code default model is not in the config template")
    return {"models": models, "default_model": default, "efforts_by_model": {}, "service_tiers_by_model": {},
            "context_windows": {model: int(entry.get("max_context_size") or 0) for model, entry in entries.items()}}


DEFAULT_MODEL = model_catalog()["default_model"]


def provider_profile_id() -> str:
    return str(settings().get("provider_profile", "kimi_primary")).strip() or "kimi_primary"


def provider_profile() -> dict:
    return _provider_profile(provider_profile_id())


def service_configured() -> bool:
    """Whether this site has the Model API the shared Kimi Code service bills to.

    A Kimi ``service`` account runs on that shared service; without the
    profile the service cannot start, so the account kind is not usable here.
    """
    try:
        provider_profile()
    except RuntimeError:
        return False
    return True


def _provider_profile(profile_id: str) -> dict:
    profiles = AIProviderRegistry().entries()
    for item in profiles:
        if str(item.get("id") or "") == profile_id:
            if not AIProviderRegistry.is_enabled(item):
                raise RuntimeError(f"Kimi Code provider profile is disabled: {profile_id}")
            if str(item.get("kind") or "").strip() != "openai_compatible_chat":
                raise RuntimeError(f"Kimi Code provider protocol is unsupported: {profile_id}")
            return item
    raise RuntimeError(f"Kimi Code provider profile not found: {profile_id}")



def _write_runtime_config(
    kimi_home: Path,
    template_path: Path,
    profile: dict,
    api_key: str,
    default_model: str,
) -> Path:
    if not template_path.is_file():
        raise RuntimeError(f"Kimi Code config template is missing: {template_path}")
    rendered = template_path.read_text(encoding="utf-8")
    base_url = AIProviderRegistry.base_url(profile)
    if not base_url:
        raise RuntimeError("Kimi Code provider base URL is unavailable")
    replacements = {
        "__PORTAL_KIMI_API_KEY__": api_key,
        "__PORTAL_KIMI_BASE_URL__": base_url,
        "__PORTAL_KIMI_DEFAULT_MODEL__": default_model,
    }
    for placeholder, value in replacements.items():
        if placeholder not in rendered:
            raise RuntimeError(f"Kimi Code config template placeholder is missing: {placeholder}")
        rendered = rendered.replace(placeholder, json.dumps(value, ensure_ascii=False))
    # Kimi Code tells Portal which session makes each Portal tool call.
    rendered = rendered.rstrip("\n") + "\n" + kimi_portal_tools.hook_config()
    try:
        parsed = tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"Kimi Code runtime config is invalid: {exc}") from exc
    models = parsed.get("models") if isinstance(parsed.get("models"), dict) else {}
    if default_model not in models:
        raise RuntimeError(f"Kimi Code default model is not registered: {default_model}")

    config_path = kimi_home / "config.toml"
    temp_path = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
    try:
        temp_path.unlink()
    except FileNotFoundError:
        pass
    descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, config_path)
        os.chmod(config_path, 0o600)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return config_path


def account_main(account_id, binary, port):
    """Own the API adapter for exactly as long as this native account server."""
    from . import kimi_account_runtime, kimi_provider_catalog, model_api_bridge
    from .provider_daemon_host import run
    from .resident_shell_environment import resident_process_environment
    account = kimi_account_runtime.account_for_route(account_id)
    if account['kind'] not in {'api_profile', 'subscription_login'} or not 0 < port < 65536:
        raise ValueError('Invalid on-demand Kimi account server')
    target = kimi_account_runtime.home(account)
    try:
        kimi_provider_catalog.write_config(account, target)
        env, _ = resident_process_environment({})
        env.update(KIMI_CODE_HOME=str(target))
        env.update(kimi_windows_runtime.environment())
        env = model_api_bridge.local_proxy_bypass(env)
        close_all_database_pools()
        return run([str(binary), 'web', '--no-open', '--host', '127.0.0.1', '--port', str(port),
                    '--dangerous-bypass-auth'], env=env, cwd=target)
    finally:
        model_api_bridge.close_bridges()
        close_all_database_pools()


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account-id')
    parser.add_argument('--binary')
    parser.add_argument('--port', type=int)
    args = parser.parse_args(argv or [])
    if args.account_id:
        if not args.binary or args.port is None:
            parser.error('Account mode requires its binary and port')
        return account_main(args.account_id, Path(args.binary), args.port)
    from management_common.instance_manifest import load
    instance = load()
    component = 'kimi-code' if 'kimi-code' in instance.document['services']['components'] else 'kimi'
    listener = instance.listener(component)
    host = listener['bind']
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("Portal Kimi Code server must listen on a loopback address")
    port = listener['port']
    profile_id = provider_profile_id()
    profile = provider_profile()
    binary = DEFAULT_BINARY
    if not binary.is_file():
        raise RuntimeError(f"Kimi Code runtime is missing: {binary}")
    kimi_home = DEFAULT_HOME
    kimi_home.mkdir(parents=True, exist_ok=True)
    os.chmod(kimi_home, 0o700)
    template_path = DEFAULT_CONFIG_TEMPLATE
    default_model = str(settings().get("default_model", DEFAULT_MODEL)).strip() or DEFAULT_MODEL
    api_key = AIProviderRegistry.read_secret(AIProviderRegistry.secret_path(profile))
    _write_runtime_config(kimi_home, template_path, profile, api_key, default_model)
    # Portal tools reach Kimi Code through one user-level MCP server.
    kimi_portal_tools.install(kimi_home)

    # The server read its registration from the Portal database; Kimi Code
    # and the shells it starts get neither its connection nor its address.
    close_all_database_pools()
    env = dict(os.environ)
    for key in (
        "PORTAL_DATABASE_URL",
        "KIMI_MODEL_NAME",
        "KIMI_MODEL_API_KEY",
        "KIMI_MODEL_PROVIDER_TYPE",
        "KIMI_MODEL_BASE_URL",
        "KIMI_MODEL_MAX_CONTEXT_SIZE",
        "KIMI_MODEL_CAPABILITIES",
        "KIMI_MODEL_DISPLAY_NAME",
        "KIMI_MODEL_MAX_OUTPUT_SIZE",
        "KIMI_MODEL_REASONING_KEY",
        "KIMI_MODEL_THINKING_EFFORT",
        "KIMI_MODEL_ADAPTIVE_THINKING",
    ):
        env.pop(key, None)
    env.update({
        "KIMI_CODE_HOME": str(kimi_home),
        "KIMI_CODE_AGENT_SWARM_MAX_CONCURRENCY": str(settings().get("swarm_max_concurrency", 3)),
        "KIMI_SUBAGENT_TIMEOUT_MS": str(settings().get("subagent_timeout_ms", 2147000000)),
    })
    env.update(kimi_windows_runtime.environment())
    command = [
            str(binary),
            "web",
            "--no-open",
            "--host",
            host,
            "--port",
            str(port),
            "--dangerous-bypass-auth",
        ]
    if sys.platform == "win32":
        # npm installs a .cmd shim. Windows execvpe/CreateProcess cannot run it
        # directly; the shared process owner resolves it and owns its full tree.
        return run_process(command, env=env).returncode
    os.execvpe(str(binary), command, env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
