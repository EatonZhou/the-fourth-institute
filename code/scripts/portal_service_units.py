#!/usr/bin/env python3
"""Install Portal units from common source, including declared Provider units."""
from __future__ import annotations

import subprocess
import sys
import argparse
import hashlib
import json
import os
import shutil
import re
import shlex
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
sys.dont_write_bytecode = True
from scripts.portal_instance_storage import install_unit as _install_unit
from scripts.portal_source_layout import SHARED_ROOT as DEV_ROOT, INSTANCE_LAYOUT, DEV_AUX_UNITS, DEV_WEB_DROPINS, AI_SOCKET_RELAY_UNIT
from management_common.instance_paths import instance_root
from management_common.file_lock import lock_stream
from management_common.site_compatibility import compatibility_value
from management_common.postgres_configuration import postgres_settings

USER_UNIT_ROOT = Path.home() / ".config" / "systemd" / "user"
RELEASE_SERVICES = INSTANCE_LAYOUT['release']['services']
RELEASE_WEB_SERVICE = INSTANCE_LAYOUT['release']['web']
# All unit definitions come from the common Dev tree, including Release Web.
RELEASE_UNITS = (RELEASE_WEB_SERVICE, *RELEASE_SERVICES, INSTANCE_LAYOUT['release']['target'])
DEV_UNITS = (*INSTANCE_LAYOUT['dev']['services'], INSTANCE_LAYOUT['dev']['target'])
DEV_WEB_SERVICE = INSTANCE_LAYOUT['dev']['web']
DEV_WEB_UNIT_SOURCE = DEV_ROOT / "management_portal" / DEV_WEB_SERVICE
CODEX_TEMPLATE = "portal-codex-app-server@.service"
LEGACY_AI_SOCKET_RELAY_UNIT = compatibility_value('ai_socket_relay_unit', '')


def _isolated_codex_context() -> dict | None:
    """Resolve a release installation's owned Linux Provider service namespace."""
    if not sys.platform.startswith("linux"):
        return None
    configured = os.environ.get("PORTAL_PATHS_CONFIG")
    if not configured:
        return None
    config = Path(configured).resolve()
    root = config.parent.parent
    state_path = root / "installation.json"
    if not state_path.is_file():
        return None
    target = json.loads(state_path.read_text())["target"]
    from scripts.tfi_instance_layout import code_root, validate_shared_code
    validate_shared_code(target)
    prefix = target["service_prefix"]
    if (config != root / "config/paths.json" or
            Path(target["install_root"]).resolve() != root or
            SOURCE_ROOT.resolve() != code_root(target).resolve() or
            not re.fullmatch(r"tfi-[a-z0-9][a-z0-9-]*", prefix)):
        raise ValueError("Isolated Provider service ownership mismatch")
    from management_common.portal_paths import path
    for key, value in target["data_roots"].items():
        if path(key).resolve() != Path(value).resolve():
            raise ValueError("Isolated Provider data roots differ from installation")
    import pwd
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return {"prefix": prefix + "-codex-app-server@", "root": root,
            "directory": home / ".config/systemd/user"}


def provider_units() -> tuple[str, ...]:
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    return tuple(dict.fromkeys(unit for definition in AGENT_PROVIDERS.values()
                              if (runtime := definition.updater()) is not None
                              for unit in runtime.unit_files))


def _run(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    env = None
    isolated = _isolated_codex_context()
    if isolated and args[:2] == ("systemctl", "--user"):
        for argument in args[2:]:
            if argument.endswith((".service", ".target")) and not argument.startswith(isolated["prefix"]):
                raise ValueError("Refusing source service control from an isolated installation")
        env = dict(os.environ)
        runtime = f"/run/user/{os.getuid()}"
        env.update(XDG_RUNTIME_DIR=runtime, DBUS_SESSION_BUS_ADDRESS=f"unix:path={runtime}/bus")
    return subprocess.run(
        list(args),
        env=env,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def _install_provider_environment() -> None:
    # EnvironmentFile does not expand template placeholders. Materialize these
    # beside instance state, leaving checked-in Provider templates unchanged.
    for name in ("portal-claude-code.env", "portal-kimi-code.env"):
        source = DEV_ROOT / "systemd" / name
        if source.is_file():
            _install_unit(source, instance_root() / "systemd" / name)


def install_units() -> dict[str, Any]:
    USER_UNIT_ROOT.mkdir(parents=True, exist_ok=True)
    installed = []
    for unit in (*RELEASE_UNITS, *provider_units()):
        source = DEV_ROOT / "systemd" / unit
        if not source.is_file():
            raise FileNotFoundError(source)
        target = USER_UNIT_ROOT / unit
        _install_unit(source, target)
        installed.append({"unit": unit, "source": str(source)})
    installed.extend(install_dev_units(reload=False)["installed"])
    template = install_codex_unit_template(reload=False)
    installed.append({"unit": template.name, "source": str(DEV_ROOT / "config/systemd" / f"{CODEX_TEMPLATE}.in")})
    _run("systemctl", "--user", "daemon-reload", capture=False)
    return {"ok": True, "installed": installed}


def _native_definition(name: str, platform: str, *, adapter, directory: Path,
                       values: dict[str, str], site_env: dict[str, str],
                       account: str) -> str:
    """Translate the same checked-in service entrypoint into a native job.

    Split the template before replacing path tokens, so installation roots with
    spaces remain one argv element on both native platforms.
    """
    source = DEV_WEB_UNIT_SOURCE if name == DEV_WEB_SERVICE else DEV_ROOT / "systemd" / name
    raw = source.read_text(encoding="utf-8")
    tokens = {**values, **site_env, "PORTAL_GROUP_COMMAND": ""}

    def substitute(value: str) -> str:
        result = re.sub(r"@([A-Z][A-Z0-9_]+)@",
                        lambda match: tokens[match.group(1)] if match.group(1) in tokens else
                        (_ for _ in ()).throw(ValueError(f"Unknown service token: {match.group(0)}")), value)
        return result

    fields: dict[str, list[str]] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"ExecStart", "Environment", "EnvironmentFile", "WorkingDirectory"}:
            fields.setdefault(key, []).append(value)
    command = [substitute(part) for part in shlex.split(fields["ExecStart"][0])]
    root = Path(values["PORTAL_INSTANCES_ROOT"])
    host_python = str(root / "dev" / "runtime-venv" /
                      ("Scripts/python.exe" if platform == "windows" else "bin/python"))
    for index, part in enumerate(command):
        if part.endswith("/runtime-venv/bin/python"):
            instance = "release" if "/release/" in part else "dev"
            command[index] = str(root / instance / "runtime-venv" /
                                 ("Scripts/python.exe" if platform == "windows" else "bin/python"))
        elif part == "/usr/bin/python3":
            command[index] = host_python
        elif part == "/usr/sbin/nats-server":
            executable = shutil.which("nats-server")
            if not executable:
                raise FileNotFoundError("nats-server is required to register Portal native services")
            command[index] = executable
    environment = dict(site_env)
    for assignment in fields.get("Environment", ()):
        key, separator, value = assignment.partition("=")
        if not separator:
            raise ValueError(f"Invalid service environment in {source}")
        environment[key] = substitute(value)
    env_files = [substitute(value.lstrip("-")) for value in fields.get("EnvironmentFile", ())]
    if site_env.get("PORTAL_NETWORK_CONFIG"):
        env_files.append(site_env["PORTAL_NETWORK_CONFIG"])
    cwd = Path(substitute(fields.get("WorkingDirectory", [values["PORTAL_SOURCE_ROOT"]])[0]))
    log_root = root / "native-service-logs"
    log_root.mkdir(parents=True, exist_ok=True)
    if platform == "windows":
        return adapter.command_unit_text(name, command, account=account, cwd=cwd,
                                         log_path=log_root / f"{name}.log", directory=directory,
                                         python=host_python, environment=environment,
                                         env_files=env_files)
    wrapped = [host_python, str(DEV_ROOT / "scripts/portal_native_service_child.py")]
    for env_file in env_files:
        wrapped.extend(("--env-file", env_file))
    wrapped.extend(("--", *command))
    return adapter.command_unit_text(name, wrapped, cwd=cwd, log_root=log_root,
                                     log_name=name, environment=environment)


def install_native_units(instance: str, platform: str) -> dict[str, Any]:
    """Register Portal Dev/Release with the TFI native SCM/launchd adapter.

    Registration is idempotent and never starts a service. Existing foreign or
    changed definitions are rejected by the native adapter.
    """
    if platform not in {"windows", "macos"}:
        raise ValueError(f"Unsupported native Portal platform: {platform}")
    if instance not in INSTANCE_LAYOUT:
        raise ValueError(f"Unknown Portal instance: {instance}")
    from management_common.site_config import read_site_config
    from scripts.portal_instance_storage import _deployment_values, site_environment
    from scripts import tfi_release_platform_windows, tfi_release_platform_macos

    adapter = tfi_release_platform_windows if platform == "windows" else tfi_release_platform_macos
    deployment = read_site_config().get("deployment", {})
    values = _deployment_values(deployment)
    values["PORTAL_INSTANCES_ROOT"] = str(instance_root())
    environment = site_environment(deployment)
    account = deployment.get("windows_service_account") or os.environ.get("PORTAL_WINDOWS_SERVICE_ACCOUNT", "")
    if platform == "windows" and not account:
        raise ValueError("Set deployment.windows_service_account or PORTAL_WINDOWS_SERVICE_ACCOUNT before registering Portal SCM services")
    directory = adapter.unit_directory()
    layout = INSTANCE_LAYOUT[instance]
    names = (*layout["services"], layout["web"], "portal-user-router.service")
    if instance == "dev":
        names += ("portal-dev-source-reloader.service",)
    units = {name: _native_definition(name, platform, adapter=adapter, directory=directory,
                                      values=values, site_env=environment, account=account)
             for name in names}
    created = adapter.register_services(directory, units)
    return {"ok": True, "installed": list(units), "created": created}


def install_dev_units(*, reload: bool = True) -> dict[str, Any]:
    USER_UNIT_ROOT.mkdir(parents=True, exist_ok=True)
    _install_provider_environment()
    installed: list[dict[str, str]] = []
    _install_unit(DEV_WEB_UNIT_SOURCE, USER_UNIT_ROOT / DEV_WEB_SERVICE)
    installed.append({
        "unit": DEV_WEB_SERVICE,
        "source": str(DEV_WEB_UNIT_SOURCE),
    })
    for unit in (*DEV_UNITS, *DEV_AUX_UNITS):
        source_name = 'portal-postgresql.service' if unit == postgres_settings()['unit'] else unit
        source = DEV_ROOT / "systemd" / source_name
        target = USER_UNIT_ROOT / unit
        _install_unit(source, target)
        installed.append({"unit": unit, "source": str(source)})
    dropin_root = USER_UNIT_ROOT / f"{DEV_WEB_SERVICE}.d"
    for target_name, source_name in DEV_WEB_DROPINS.items():
        source = DEV_ROOT / "systemd" / source_name
        target = dropin_root / target_name
        _install_unit(source, target)
        installed.append({
            "unit": f"{DEV_WEB_SERVICE}.d/{target_name}",
            "source": str(source),
        })
    if reload:
        _run("systemctl", "--user", "daemon-reload", capture=False)
    return {"ok": True, "installed": installed}


def install_codex_unit_template(*, reload: bool = True) -> Path:
    if sys.platform == "win32":
        target = instance_root() / "service-units"
        target.mkdir(parents=True, exist_ok=True)
        return target
    if sys.platform == "darwin":
        from scripts.tfi_release_platform_macos import unit_directory
        target = unit_directory()
        target.mkdir(parents=True, exist_ok=True)
        return target
    isolated = _isolated_codex_context()
    target = ((isolated["directory"] / (isolated["prefix"] + ".service"))
              if isolated else USER_UNIT_ROOT / CODEX_TEMPLATE)
    source = DEV_ROOT / "config/systemd" / f"{CODEX_TEMPLATE}.in"
    if isolated:
        from scripts.portal_instance_storage import render_unit
        content = render_unit(source)
        content = content.replace("[Service]\n", "[Service]\nUMask=0077\n" +
                                  f"Environment=HOME={isolated['root'] / 'home'}\n" +
                                  f"Environment=XDG_CONFIG_HOME={isolated['root'] / 'home/.config'}\n")
        if target.is_symlink() or (target.exists() and target.read_text() != content):
            raise ValueError("Refusing to replace a different Provider service template")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x") as stream:
                stream.write(content)
    else:
        _install_unit(source, target)
    if reload:
        _run("systemctl", "--user", "daemon-reload")
    return target


def install_ai_socket_relay_unit() -> dict[str, Any]:
    """Prepare the new definition without starting or stopping either identity."""
    source = DEV_ROOT / "systemd" / AI_SOCKET_RELAY_UNIT
    target = USER_UNIT_ROOT / AI_SOCKET_RELAY_UNIT
    _install_unit(source, target)
    _run("systemctl", "--user", "daemon-reload")
    return {"ok": True, "status": "prepared", "unit": AI_SOCKET_RELAY_UNIT,
            "source": str(source), "target": str(target), "processes_reloaded": False}


def _relay_unit_state(unit: str) -> dict[str, Any]:
    result = _run("systemctl", "--user", "show", unit,
                  "--property=Id,LoadState,ActiveState,SubState,MainPID,UnitFileState,FragmentPath,DropInPaths", check=False)
    values = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
    required = {"LoadState", "ActiveState", "SubState", "MainPID"}
    if (not required.issubset(values) or
            (result.returncode and not (result.returncode == 4 and values["LoadState"] == "not-found"))):
        raise RuntimeError(f"Cannot read unit state: {unit}")
    try:
        pid = int(values["MainPID"])
    except ValueError as exc:
        raise RuntimeError(f"Invalid unit state: {unit}") from exc
    return {"unit": unit, "load": values["LoadState"], "active": values["ActiveState"],
            "sub": values["SubState"], "pid": pid, "enabled": values.get("UnitFileState", ""),
            "fragment": values.get("FragmentPath", ""), "dropins": values.get("DropInPaths", "")}


def _relay_migration_plan() -> dict[str, Any]:
    if not LEGACY_AI_SOCKET_RELAY_UNIT:
        return {'ok': False, 'status': 'needs_configuration',
                'reason': 'legacy_unit_not_configured', 'processes_reloaded': False}
    if (not isinstance(LEGACY_AI_SOCKET_RELAY_UNIT, str)
            or not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', LEGACY_AI_SOCKET_RELAY_UNIT)
            or LEGACY_AI_SOCKET_RELAY_UNIT == AI_SOCKET_RELAY_UNIT):
        raise ValueError('Invalid configured historical relay unit')
    legacy = _relay_unit_state(LEGACY_AI_SOCKET_RELAY_UNIT)
    new = _relay_unit_state(AI_SOCKET_RELAY_UNIT)
    reason = ""
    if legacy["active"] not in {"inactive", "failed"} or legacy["pid"]:
        reason = "legacy_not_stopped"
    elif any(state["load"] not in {"loaded", "not-found"} for state in (legacy, new)):
        reason = "unit_not_loadable"
    elif any(state["fragment"] and Path(state["fragment"]).absolute() != (USER_UNIT_ROOT / state["unit"]).absolute()
             for state in (legacy, new)):
        reason = "foreign_fragment"
    else:
        dropins = USER_UNIT_ROOT / f"{LEGACY_AI_SOCKET_RELAY_UNIT}.d"
        if legacy["dropins"] or (dropins.is_dir() and any(dropins.iterdir())):
            reason = "legacy_overrides"
    return {"ok": not reason, "status": "blocked" if reason else "planned", "reason": reason,
            "legacy": legacy, "new": new, "processes_reloaded": False}


def migrate_ai_socket_relay(*, apply: bool = False) -> dict[str, Any]:
    """Retire a stopped legacy unit; never infer drain from a connection snapshot.

    The activation owner must first exclude new traffic and stop the drained old
    service. This lock serializes this migration, not unrelated systemctl calls.
    A failed activation leaves the old definition for diagnosis and a later retry.
    """
    plan = _relay_migration_plan()
    if not apply or not plan["ok"]:
        return plan
    USER_UNIT_ROOT.mkdir(parents=True, exist_ok=True)
    with (USER_UNIT_ROOT / ".portal-ai-socket-relay-migration.lock").open("a+b") as stream, lock_stream(stream):
        plan = _relay_migration_plan()
        if not plan["ok"]:
            return plan
        install_ai_socket_relay_unit()
        legacy_path = USER_UNIT_ROOT / LEGACY_AI_SOCKET_RELAY_UNIT
        has_legacy = plan["legacy"]["load"] != "not-found" or legacy_path.exists() or legacy_path.is_symlink()
        if has_legacy:
            _run("systemctl", "--user", "disable", LEGACY_AI_SOCKET_RELAY_UNIT)
        _run("systemctl", "--user", "enable", "--now", AI_SOCKET_RELAY_UNIT)
        observed = _relay_migration_plan()
        if not observed["ok"]:
            return observed
        if observed["legacy"]["enabled"] in {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}:
            return {**observed, "ok": False, "status": "blocked", "reason": "legacy_still_enabled"}
        new = observed["new"]
        if new["active"] != "active" or new["sub"] != "running" or new["pid"] <= 0:
            raise RuntimeError("New relay unit did not reach running state; legacy definition retained")
        if has_legacy:
            _run("systemctl", "--user", "reset-failed", LEGACY_AI_SOCKET_RELAY_UNIT, check=False)
            # unlink removes a legacy symlink itself, never its source definition.
            legacy_path.unlink(missing_ok=True)
            _run("systemctl", "--user", "daemon-reload")
        return {"ok": True, "status": "completed", "legacy": _relay_unit_state(LEGACY_AI_SOCKET_RELAY_UNIT),
                "new": new, "processes_reloaded": False,
                "new_process_started": plan["new"]["pid"] != new["pid"]}


def _codex_unit(account_id: str) -> str:
    from management_portal.execution.account_hosting import validate_account_id
    identity = validate_account_id(account_id)
    isolated = _isolated_codex_context()
    if isolated:
        return f"{isolated['prefix']}{identity}.service"
    if sys.platform in {"win32", "darwin"}:
        # Native service names, like local RPC endpoints, are installation scoped.
        scope = hashlib.sha256(os.fsencode(instance_root().resolve())).hexdigest()[:10]
        identity = f"{identity}-{scope}"
    return f"portal-codex-app-server@{identity}.service"


def _native_codex_service(account_id: str):
    from management_portal.execution.account_hosting import account_home, codex_socket_path
    from management_common.site_config import read_site_config
    from management_common.portal_paths import resolve_directory
    from scripts.portal_service_control import ServiceControl
    if sys.platform == "win32":
        from scripts import tfi_release_platform_windows as adapter
    elif sys.platform == "darwin":
        from scripts import tfi_release_platform_macos as adapter
    else:
        raise RuntimeError("Native Codex service requires Windows or macOS")
    deployment = read_site_config().get("deployment", {})
    providers = deployment.get("providers", {}) if isinstance(deployment, dict) else {}
    configured = providers.get("codex_binary") if isinstance(providers, dict) else None
    from management_portal.execution.provider_updates import CodexRuntime
    binary = str(resolve_directory(configured)) if configured else shutil.which("codex") or str(CodexRuntime().link())
    if sys.platform == "win32":
        # Keep the SCM definition stable across Provider version switches.
        from management_portal.execution.provider_updates import CodexRuntime
        binary = str(CodexRuntime().link() / "codex.cmd")
    if not binary or not Path(binary).is_absolute() or not Path(binary).is_file():
        raise FileNotFoundError("Absolute Codex executable is unavailable")
    name = _codex_unit(account_id)
    home = account_home("codex", account_id)
    command = [sys.executable, "-m", "management_portal.codex_app_server_host",
               "--socket", str(codex_socket_path(account_id)), "--binary", binary,
               "--account-home", str(home)]
    directory = install_codex_unit_template()
    if sys.platform == "win32":
        account = os.environ.get("PORTAL_WINDOWS_SERVICE_ACCOUNT", "")
        if not account:
            raise ValueError("PORTAL_WINDOWS_SERVICE_ACCOUNT is required for Codex SCM service")
        content = adapter.command_unit_text(name, command, account=account,
                                            cwd=SOURCE_ROOT, log_path=instance_root() / "logs" / f"{name}.log",
                                            directory=directory)
    else:
        log_root = instance_root() / "logs"
        log_root.mkdir(parents=True, exist_ok=True)
        content = adapter.command_unit_text(name, command, cwd=SOURCE_ROOT,
                                            log_root=log_root, log_name=name)
    return adapter, directory, name, content, ServiceControl()


def _native_codex_state(account_id: str) -> dict[str, Any]:
    from scripts.portal_service_control import ServiceControl
    unit = _codex_unit(account_id)
    state = ServiceControl().status(unit)
    return {"unit": unit, "load": state["load"], "active": state["active"],
            "sub": state["sub"], "pid": state["pid"], "enabled": state["load"] == "loaded"}


def start_codex_instance(account_id: str) -> dict[str, Any]:
    from management_portal.execution.account_hosting import ensure_account_home, codex_socket_path
    ensure_account_home("codex", account_id)
    codex_socket_path(account_id).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.platform in {"win32", "darwin"}:
        adapter, directory, name, content, controller = _native_codex_service(account_id)
        created = adapter.register_services(directory, {name: content}, **(
            {"password_env": "PORTAL_WINDOWS_SERVICE_PASSWORD"} if sys.platform == "win32" else {}))
        try:
            controller.start(name)
        except Exception:
            if created:
                try:
                    controller.stop(name)
                finally:
                    adapter.remove_services(directory, created)
            raise
        return _native_codex_state(account_id)
    install_codex_unit_template()
    _run("systemctl", "--user", "enable", "--now", _codex_unit(account_id))
    return codex_instance_state(account_id)


def stop_codex_instance(account_id: str) -> dict[str, Any]:
    if sys.platform in {"win32", "darwin"}:
        from scripts.portal_service_control import ServiceControl
        ServiceControl().stop(_codex_unit(account_id))
        return _native_codex_state(account_id)
    _run("systemctl", "--user", "stop", _codex_unit(account_id))
    return codex_instance_state(account_id)


def remove_codex_instance(account_id: str) -> None:
    from management_portal.execution.account_hosting import codex_socket_path
    if sys.platform in {"win32", "darwin"}:
        from scripts.portal_service_control import ServiceControl
        if sys.platform == "win32":
            from scripts import tfi_release_platform_windows as adapter
            directory = instance_root() / "service-units"
        else:
            from scripts import tfi_release_platform_macos as adapter
            directory = adapter.unit_directory()
        name = _codex_unit(account_id)
        ServiceControl().stop(name)
        adapter.remove_services(directory, [name])
        codex_socket_path(account_id).unlink(missing_ok=True)
        return
    _run("systemctl", "--user", "disable", "--now", _codex_unit(account_id))
    _run("systemctl", "--user", "reset-failed", _codex_unit(account_id), check=False)
    codex_socket_path(account_id).unlink(missing_ok=True)


def codex_instance_state(account_id: str) -> dict[str, Any]:
    if sys.platform in {"win32", "darwin"}:
        return _native_codex_state(account_id)
    unit = _codex_unit(account_id)
    result = _run("systemctl", "--user", "show", unit,
                  "--property=LoadState,ActiveState,SubState,MainPID,UnitFileState", check=False)
    values = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
    return {"unit": unit, "load": values.get("LoadState", "not-found"),
            "active": values.get("ActiveState", "inactive"), "sub": values.get("SubState", "dead"),
            "pid": int(values.get("MainPID") or 0), "enabled": values.get("UnitFileState") == "enabled"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("relay-migration",))
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--prepare", action="store_true", help="Install the new definition without changing running processes")
    action.add_argument("--apply", action="store_true", help="Activate the new unit and retire an already stopped legacy unit")
    args = parser.parse_args()
    try:
        result = install_ai_socket_relay_unit() if args.prepare else migrate_ai_socket_relay(apply=args.apply)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


