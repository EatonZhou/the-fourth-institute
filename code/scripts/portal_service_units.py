#!/usr/bin/env python3
"""Provider service units: Codex app-server instances and the Claude backend.

Provider daemons and account hosts are components registered in the selected
instance's manifest. Their definitions are created, verified and removed only
through scripts.instance_services, which checks that a definition selects this
same instance; nothing here operates a service outside that inventory.
"""
from __future__ import annotations

import subprocess
import sys
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
sys.dont_write_bytecode = True
from management_common.file_lock import lock_stream
from management_common import code_environment
from management_common.code_environment import provider_entry

DAEMONS = {"portal-claude-code.service": "claude-code", "portal-kimi-code.service": "kimi-code"}


def _instance():
    """The selected instance, which must run this code."""
    from management_common.instance_manifest import load
    instance = load()
    if SOURCE_ROOT.resolve() not in {Path(row['code_root']) for row in instance.deployments.values()}:
        raise ValueError('Provider service code is not registered in this instance')
    return instance


def _daemon_component(unit: str) -> str:
    try:
        return DAEMONS[unit]
    except KeyError:
        raise ValueError("Unknown Provider daemon") from None


def _definition_path(instance, component: str) -> Path:
    from scripts import instance_services
    return instance_services.adapter().unit_path(instance_services.definition_directory(instance),
                                                 instance_services.unit_name(instance, component))


def installation_service_name(unit: str) -> str:
    """Resolve a logical service through the selected instance's inventory."""
    from management_common.instance_manifest import load
    from scripts.instance_services import unit_name
    return unit_name(load(), unit.removeprefix('portal-').removesuffix('.service'))


def provider_runtime_services(unit: str) -> tuple[str, ...]:
    """A daemon's running service in this instance; () when not defined."""
    from scripts import instance_services
    component = _daemon_component(unit)
    instance = _instance()
    if component not in instance.document['services']['components']:
        return ()
    instance_services.verify_definitions(instance, exact=False, selected_components=(component,))
    if not _definition_path(instance, component).exists():
        return ()
    name = instance_services.unit_name(instance, component)
    return (name,) if instance_services.controller(instance).status(name)["active"] == "active" else ()


def _run(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    env = None
    if args[:2] == ("systemctl", "--user"):
        from scripts.instance_services import owned_units
        units = owned_units(_instance())
        for argument in args[2:]:
            if argument.endswith((".service", ".target")) and argument not in units:
                raise ValueError("Refusing unregistered instance service control")
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


def _define(component: str) -> list[str]:
    """Register the component, then create or refresh its owned definition."""
    from management_common.host_instances import add_service_component
    from scripts.instance_services import install_definitions
    instance = add_service_component(_instance().root, component)
    return install_definitions(instance, (component,))


def _undefine(component: str) -> None:
    """Withdraw a definition created by a failed first start, and its registration."""
    from management_common.host_instances import remove_service_component
    from scripts.instance_services import remove_definitions
    instance = _instance()
    remove_definitions(instance, (component,))
    remove_service_component(instance.root, component)


def install_claude_unit(runtime: Path, package: Path) -> Path:
    """Install a packaged daemon whose environment contains only its instance."""
    instance = _instance()
    provider_runtime_services('portal-claude-code.service')
    if (runtime != provider_entry('claude_runtime_current', instance)
            or package != provider_entry('claude_app_server_current', instance)):
        raise ValueError('Claude service requires the code-owned current runtime and package')
    _define('claude-code')
    provider_runtime_services('portal-claude-code.service')
    return _definition_path(_instance(), 'claude-code')


def _switch_claude_link(link: Path, target: Path) -> None:
    if link.exists() and not link.is_symlink():
        raise ValueError("Refusing to replace non-symlink Claude current path")
    link.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=link.parent) as temporary:
        candidate = Path(temporary) / "current"
        candidate.symlink_to(target)
        candidate.replace(link)


def _install_claude_backend(*, app_server_only: bool) -> Path:
    from management_portal.execution import provider_updates as updates
    from scripts.claude_app_server_package import build_package

    instance = _instance()
    provider_runtime_services("portal-claude-code.service")
    runtime = updates.ClaudeRuntime()
    runtime_root, versions = runtime.link(), runtime.installed()
    staged = False
    if versions == ("0",):
        if app_server_only:
            raise RuntimeError("Claude Agent SDK runtime is missing")
        versions = runtime.available()
        runtime_root = runtime.stage(versions)
        staged = True
    version = (SOURCE_ROOT / "management_portal/claude_app_server_version.txt").read_text().strip()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}", version):
        raise ValueError("Invalid Claude app-server version")
    # The package and its current link belong to the same code-owned directory.
    package = code_environment.provider_directory(instance) / f"claude-app-server-{version}"
    current = provider_entry("claude_app_server_current", instance)
    package.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".claude-app-server-", dir=package.parent) as temporary:
        candidate = Path(temporary) / "package"
        build_package(SOURCE_ROOT, candidate)
        subprocess.run([str(runtime_root / "bin/python"), "-I", "-B", "-c",
                        "import sys; sys.path.insert(0, sys.argv[2]); "
                        "from management_portal import claude_code_daemon as daemon; "
                        "assert daemon.APP_SERVER_VERSION == sys.argv[1]; "
                        "assert daemon.APP_SERVER_PROTOCOL_VERSION == 1",
                        version, str(candidate)], check=True, timeout=30,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if package.exists():
            def digest(root):
                return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in root.rglob("*") if p.is_file()}
            if digest(candidate) != digest(package):
                raise ValueError("Refusing to replace immutable Claude app-server runtime; bump APP_SERVER_VERSION")
        else:
            candidate.replace(package)
    if staged:
        current_sdk = runtime.activation_link()
        _switch_claude_link(current_sdk, runtime_root)
        runtime_root = current_sdk
    _switch_claude_link(current, package)
    return install_claude_unit(runtime_root, current)


def install_claude_backend(*, app_server_only: bool = False) -> Path:
    """The standard installer and onboarding share one serialized install owner."""
    from management_portal.execution.provider_updates import STATE_ROOT
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with (STATE_ROOT / "claude-backend-install.lock").open("a+") as stream, lock_stream(stream):
        return _install_claude_backend(app_server_only=app_server_only)


def start_claude_service(*, wait_ready=None) -> None:
    from management_portal.execution.provider_updates import STATE_ROOT
    from scripts.instance_services import controller, unit_name
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with (STATE_ROOT / "claude-backend-install.lock").open("a+") as stream, lock_stream(stream):
        instance = _instance()
        if provider_runtime_services("portal-claude-code.service"):
            if wait_ready:
                wait_ready()
            return
        created = not ("claude-code" in instance.document['services']['components']
                       and _definition_path(instance, "claude-code").exists())
        if created and sys.platform in {"win32", "darwin"}:
            # Native packages ship the prepared daemon; first use defines it.
            _define("claude-code")
        elif created:
            _install_claude_backend(app_server_only=True)
        instance = _instance()
        name = unit_name(instance, "claude-code")
        control = controller(instance)
        try:
            if sys.platform in {"win32", "darwin"}:
                control.start(name)
            else:
                # enable --now is idempotent; unlike restart it preserves running turns.
                _run("systemctl", "--user", "enable", "--now", name)
            if wait_ready:
                wait_ready()
        except Exception:
            control.stop(name)
            if created:
                _undefine("claude-code")
            raise


def _codex_component(account_id: str) -> str:
    from management_portal.execution.account_hosting import validate_account_id
    return 'codex-app-server@' + validate_account_id(account_id)


def _codex_unit(account_id: str) -> str:
    from management_common.instance_manifest import load
    from scripts.instance_services import unit_name
    return unit_name(load(), _codex_component(account_id))


def _native_codex_controller():
    from scripts.instance_services import controller
    return controller(_instance())


def _native_codex_state(account_id: str) -> dict[str, Any]:
    unit = _codex_unit(account_id)
    state = _native_codex_controller().status(unit)
    return {"unit": unit, "load": state["load"], "active": state["active"],
            "sub": state["sub"], "pid": state["pid"], "enabled": state["load"] == "loaded"}


def verify_native_codex_services(names):
    """Each running account host must be a registered, owned, current definition."""
    from scripts.instance_services import unit_name, verify_definitions
    instance = _instance()
    accounts = {unit_name(instance, component): component for component in instance.document['services']['components']
                if component.startswith('codex-app-server@')}
    if set(names) - set(accounts):
        raise ValueError('Provider service is not a registered account host of this instance')
    verify_definitions(instance, selected_components=tuple(accounts[name] for name in names))


def start_codex_instance(account_id: str) -> dict[str, Any]:
    from management_portal.execution.account_hosting import ensure_account_home, codex_socket_path
    from scripts.instance_services import verify_definitions
    ensure_account_home("codex", account_id)
    from management_portal.execution.account_hosting import write_codex_launch
    write_codex_launch(account_id)
    component = _codex_component(account_id)
    codex_socket_path(account_id).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_root = _instance().directory("runtime") / "service-locks"
    lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (lock_root / f"{component}.lock").open("a+b") as stream, lock_stream(stream):
        created = _define(component)
        instance = _instance()
        verify_definitions(instance, selected_components=(component,))
        name = _codex_unit(account_id)
        if sys.platform in {"win32", "darwin"}:
            try:
                _native_codex_controller().start(name)
            except Exception:
                if created:
                    try:
                        _native_codex_controller().stop(name)
                    finally:
                        _undefine(component)
                raise
            return _native_codex_state(account_id)
        _run('systemctl', '--user', 'enable', '--now', name)
    return codex_instance_state(account_id)


def stop_codex_instance(account_id: str) -> dict[str, Any]:
    from scripts.instance_services import verify_definitions
    verify_definitions(_instance(), exact=False, selected_components=(_codex_component(account_id),))
    if sys.platform in {"win32", "darwin"}:
        _native_codex_controller().stop(_codex_unit(account_id))
        return _native_codex_state(account_id)
    _run("systemctl", "--user", "stop", _codex_unit(account_id))
    return codex_instance_state(account_id)


def remove_codex_instance(account_id: str) -> None:
    from management_portal.execution.account_hosting import codex_socket_path
    from scripts.instance_services import controller, verify_definitions
    instance = _instance()
    component = _codex_component(account_id)
    if component not in instance.document['services']['components']:
        return
    verify_definitions(instance, exact=False, selected_components=(component,))
    controller(instance).stop(_codex_unit(account_id))
    _undefine(component)
    codex_socket_path(account_id).unlink(missing_ok=True)


def codex_instance_state(account_id: str) -> dict[str, Any]:
    from management_common.instance_manifest import load
    from management_common.provider_account_paths import validate_identity
    instance = load()
    component = 'codex-app-server@' + validate_identity(account_id)
    if component not in instance.document['services']['components']:
        return {'unit': instance.service_prefix + '-' + component + ('' if sys.platform == 'win32' else '.service'),
                'load': 'not-found', 'active': 'inactive', 'sub': 'dead', 'pid': 0, 'enabled': False}
    if sys.platform in {"win32", "darwin"}:
        return _native_codex_state(account_id)
    unit = _codex_unit(account_id)
    result = _run("systemctl", "--user", "show", unit,
                  "--property=LoadState,ActiveState,SubState,MainPID,UnitFileState", check=False)
    values = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
    return {"unit": unit, "load": values.get("LoadState", "not-found"),
            "active": values.get("ActiveState", "inactive"), "sub": values.get("SubState", "dead"),
            "pid": int(values.get("MainPID") or 0), "enabled": values.get("UnitFileState") == "enabled"}
