#!/usr/bin/env python3
"""Provider service units: Codex app-server instances and the Claude backend."""
from __future__ import annotations

import subprocess
import sys
import hashlib
import json
import os
import re
import shlex
import plistlib
import tempfile
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
sys.dont_write_bytecode = True
from scripts.portal_instance_storage import install_unit as _install_unit
from scripts.portal_source_layout import SHARED_ROOT as DEV_ROOT
from management_common.instance_paths import instance_root
from management_common.file_lock import lock_stream
from management_common.provider_runtime_paths import runtime_entry

USER_UNIT_ROOT = Path.home() / ".config" / "systemd" / "user"
CODEX_TEMPLATE = "portal-codex-app-server@.service"


def _isolated_provider_context() -> dict | None:
    """Resolve a release installation's owned Linux Provider service namespace."""
    from management_common.instance_config import read_installation
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
    target = read_installation(root)["target"]
    from scripts.tfi_instance_layout import code_root, validate_shared_code
    validate_shared_code(target)
    prefix = target["service_prefix"]
    if (config not in {root / 'config/target.json', root / 'config/paths.json'} or
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
    return {"service_prefix": prefix, "root": root,
            "directory": home / ".config/systemd/user"}


def _isolated_codex_context() -> dict | None:
    context = _isolated_provider_context()
    return ({**context, "prefix": context["service_prefix"] + "-codex-app-server@"}
            if context else None)


def _native_provider_service(unit):
    """Resolve and verify the installed daemon, without creating or starting it."""
    from management_common.instance_config import read_installation
    from management_common.portal_paths import path as data_path
    from scripts.tfi_instance_layout import code_root, validate_shared_code
    if sys.platform == "win32":
        from scripts import tfi_release_platform_windows as adapter
    elif sys.platform == "darwin":
        from scripts import tfi_release_platform_macos as adapter
    else:
        raise ValueError("Native Provider service requires Windows or macOS")
    configured = os.environ.get("PORTAL_PATHS_CONFIG")
    if not configured:
        raise ValueError("Provider activation requires an installed instance")
    config = Path(configured).resolve()
    root = config.parent.parent
    target = read_installation(root)["target"]
    validate_shared_code(target)
    if (config not in {root / "config/target.json", root / "config/paths.json"}
            or Path(target["install_root"]).resolve() != root
            or code_root(target).resolve() != SOURCE_ROOT.resolve()
            or not re.fullmatch(r"tfi-[a-z0-9][a-z0-9-]*", target["service_prefix"])
            or any(data_path(key).resolve() != Path(value).resolve()
                   for key, value in target["data_roots"].items())):
        raise ValueError("Provider service installation ownership mismatch")
    component = {"portal-claude-code.service": "claude", "portal-kimi-code.service": "kimi"}[unit]
    name = adapter.unit_name(target, component)
    directory = adapter.unit_directory()
    definition = adapter.unit_path(directory, name)
    expected = adapter.unit_text(target, component, (component,))
    if definition.exists() or definition.is_symlink():
        if definition.is_symlink() or not definition.is_file():
            raise ValueError("Provider daemon service ownership mismatch")
        decode = json.loads if sys.platform == "win32" else lambda text: plistlib.loads(text.encode())
        if decode(definition.read_text(encoding="utf-8")) != decode(expected):
            raise ValueError("Provider daemon belongs to another instance")
        if sys.platform == "win32":
            adapter.verify_service(name, expected)
        else:
            # A plist on disk is not proof of the loaded launchd definition.
            observed = adapter.service_controller().run(
                "launchctl", "print", adapter.service_controller()._launch_target(name), check=False)
            if not observed.returncode:
                match = re.search(r"(?m)^\s*path\s*=\s*(.+)$", observed.stdout or "")
                if not match or Path(match[1].strip()).resolve() != definition.resolve():
                    raise ValueError("Provider daemon has a foreign loaded launchd job")
    return adapter, directory, name, expected


def provider_runtime_services(unit: str) -> tuple[str, ...]:
    """Resolve a daemon's running services in this installation only."""
    if unit not in {"portal-claude-code.service", "portal-kimi-code.service"}:
        raise ValueError("Unknown Provider daemon")
    if sys.platform in {"win32", "darwin"}:
        adapter, directory, name, _ = _native_provider_service(unit)
        if not adapter.unit_path(directory, name).exists():
            return ()
        return (name,) if adapter.service_controller().status(name)["active"] == "active" else ()
    isolated = _isolated_provider_context()
    if isolated is None:
        return (unit,)
    name = isolated["service_prefix"] + "-" + unit.removeprefix("portal-")
    path = isolated["directory"] / name
    if not path.exists() and not path.is_symlink():
        return ()
    if path.is_symlink() or not path.is_file():
        raise ValueError("Provider daemon service ownership mismatch")
    environment = {}
    for line in path.read_text().splitlines():
        if line.startswith("Environment="):
            for value in shlex.split(line.removeprefix("Environment=")):
                key, _, value = value.partition("=")
                environment[key] = value.replace("%%", "%")
    if (environment.get("HOME") != str(isolated['root'] / 'home') or
            environment.get("PORTAL_PATHS_CONFIG") not in {
                str(isolated['root'] / 'config/target.json'),
                str(isolated['root'] / 'config/paths.json')}):
        raise ValueError("Provider daemon belongs to another instance")
    observed = _run("systemctl", "--user", "show", name,
                    "--property=FragmentPath,DropInPaths,ActiveState")
    properties = dict(line.split("=", 1) for line in observed.stdout.splitlines() if "=" in line)
    if properties.get("FragmentPath") != str(path) or properties.get("DropInPaths"):
        raise ValueError("Provider daemon has a foreign definition or overrides")
    return (name,) if properties.get("ActiveState") == "active" else ()


def _run(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    env = None
    isolated = _isolated_provider_context()
    if isolated and args[:2] == ("systemctl", "--user"):
        prefix = isolated["service_prefix"] + "-"
        for argument in args[2:]:
            if argument.endswith((".service", ".target")) and not argument.startswith(prefix):
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


def install_codex_unit_template(*, reload: bool = True, refresh: bool = False) -> Path:
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
                                  f"Environment=XDG_CONFIG_HOME={isolated['root'] / 'home/.config'}\n" +
                                  f"Environment=XDG_CACHE_HOME={isolated['root'] / 'home/.cache'}\n" +
                                  f"Environment=TMPDIR={isolated['root'] / 'runtime'}\n")
        if target.is_symlink():
            raise ValueError("Refusing to replace a different Provider service template")
        if target.exists() and target.read_text() != content:
            if not refresh:
                raise ValueError("Refusing to replace a different Provider service template")
            # Explicit deployment maintenance may refresh this instance's own
            # template. Account startup never overwrites a different definition.
            required = {line for line in content.splitlines() if line.startswith((
                "Environment=HOME=", "Environment=CODEX_HOME=", "WorkingDirectory="))}
            locators = {line for line in content.splitlines() if line.startswith("Environment=PORTAL_PATHS_CONFIG=")}
            locators.update(f"Environment=PORTAL_PATHS_CONFIG={isolated['root'] / 'config' / name}"
                            for name in ("target.json", "paths.json"))
            existing = set(target.read_text().splitlines())
            if not required.issubset(existing) or not existing.intersection(locators):
                raise ValueError("Provider service template belongs to another instance")
            from management_common.file_permissions import atomic_write_private_bytes
            atomic_write_private_bytes(target, content.encode())
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x") as stream:
                stream.write(content)
    else:
        _install_unit(source, target)
    if reload:
        _run("systemctl", "--user", "daemon-reload")
    return target


def _claude_unit_path() -> Path:
    context = _isolated_provider_context()
    if context:
        return context["directory"] / (context["service_prefix"] + "-claude-code.service")
    return Path(os.environ.get("PORTAL_SYSTEMD_USER_UNIT_ROOT", USER_UNIT_ROOT)) / "portal-claude-code.service"


def install_claude_unit(runtime: Path, package: Path) -> Path:
    """Install the local daemon using a reusable SDK, never another instance's unit."""
    from management_common.instance_config import read_installation
    from management_common.portal_paths import path
    from scripts.portal_instance_storage import render_unit

    target = _claude_unit_path()
    isolated = _isolated_provider_context()
    if isolated:
        # Check the previous definition and drop-ins before replacing any bytes.
        provider_runtime_services("portal-claude-code.service")
        from scripts.tfi_release_install import clean_environment
        installation = read_installation(isolated["root"])
        env = clean_environment(installation["target"])
        env["CLAUDE_CODE_SOCKET"] = str(path("staging_root") / "portal_locks/claude-code-daemon.sock")
        content = "[Unit]\nDescription=Portal Claude Code daemon\n\n[Service]\nType=simple\nUMask=0077\n"
        # systemd expands percent specifiers even inside quoted assignments.
        for key, value in env.items():
            assignment = key + "=" + str(value).replace("%", "%%")
            content += "Environment=" + json.dumps(assignment, ensure_ascii=False) + "\n"
        content += ("Restart=always\nRestartSec=2\nTimeoutStopSec=90\n"
                    "\n[Install]\nWantedBy=default.target\n")
    else:
        content = render_unit(SOURCE_ROOT / "systemd/portal-claude-code.service")
        env_source = SOURCE_ROOT / "systemd/portal-claude-code.env"
        _install_unit(env_source, instance_root() / "systemd/portal-claude-code.env")
    # Clear the template command/cwd. Pin the external interpreter or keep the
    # managed runtime link so later SDK activation retains its restart semantics.
    content = "\n".join(line for line in content.splitlines()
                        if not line.startswith(("ExecStart=", "WorkingDirectory="))) + "\n"
    quote = lambda value: json.dumps(str(value).replace("%", "%%"), ensure_ascii=False)
    command = f"ExecStart={quote(runtime / 'bin/python')} -m management_portal.claude_code_daemon"
    command += " --socket " + quote(path("staging_root") / "portal_locks/claude-code-daemon.sock")
    # WorkingDirectory is a single path, not an ExecStart argument list; quotes
    # become literal path characters. Spaces need no quoting, but % is a specifier.
    directory = str(package).replace("%", "%%")
    if any(char in directory for char in "\r\n\0"):
        raise ValueError("Invalid Claude app-server working directory")
    content = content.replace("[Service]\n", f"[Service]\nWorkingDirectory={directory}\n{command}\n", 1)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError("Refusing a symlink Provider service definition")
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    _run("systemctl", "--user", "daemon-reload")
    if isolated:
        provider_runtime_services("portal-claude-code.service")
    return target


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

    provider_runtime_services("portal-claude-code.service")
    runtime = updates.ClaudeRuntime()
    runtime_root, versions = runtime.link(), runtime.installed()
    # Retain the standard installer's explicit current-path override.
    if os.environ.get("PORTAL_CLAUDE_RUNTIME_CURRENT") and not os.environ.get("PORTAL_CLAUDE_RUNTIME_ROOT"):
        runtime_root = Path(os.environ["PORTAL_CLAUDE_RUNTIME_CURRENT"])
        found = updates._claude_sdk(runtime_root / "bin/python")
        versions = (found[1] if found else "0",)
    staged = False
    if versions == ("0",):
        if app_server_only:
            raise RuntimeError("Claude Agent SDK runtime is missing")
        versions = runtime.available()
        configured = os.environ.get("PORTAL_CLAUDE_RUNTIME_ROOT")
        runtime_root = runtime.stage(versions, root=Path(configured) if configured else None)
        staged = True
    version = (SOURCE_ROOT / "management_portal/claude_app_server_version.txt").read_text().strip()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}", version):
        raise ValueError("Invalid Claude app-server version")
    package = Path(os.environ.get("PORTAL_CLAUDE_APP_SERVER_ROOT", updates.RUNTIMES / f"claude-app-server-{version}"))
    current = Path(os.environ.get("PORTAL_CLAUDE_APP_SERVER_CURRENT", runtime_entry(
        updates._PROVIDER_DEPLOYMENT, "claude_app_server_current", updates.RUNTIMES / "claude-app-server-current")))
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
        current_sdk = Path(os.environ.get("PORTAL_CLAUDE_RUNTIME_CURRENT", runtime.activation_link()))
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
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with (STATE_ROOT / "claude-backend-install.lock").open("a+") as stream, lock_stream(stream):
        if sys.platform in {"win32", "darwin"}:
            adapter, directory, name, definition = _native_provider_service("portal-claude-code.service")
            created = adapter.register_services(directory, {name: definition})
            controller = adapter.service_controller()
            was_active = controller.status(name)["active"] == "active"
            try:
                controller.start(name)
                if wait_ready:
                    wait_ready()
            except Exception:
                if not was_active:
                    controller.stop(name)
                if created:
                    adapter.remove_services(directory, created)
                raise
            return
        target = _claude_unit_path()
        active = provider_runtime_services("portal-claude-code.service")
        if not target.exists():
            target = _install_claude_backend(app_server_only=True)
        elif _isolated_provider_context() and active:
            if wait_ready:
                wait_ready()
            return
        # enable --now is idempotent; unlike restart it preserves running turns.
        _run("systemctl", "--user", "enable", "--now", target.name)
        if wait_ready:
            try:
                wait_ready()
            except Exception:
                if not active:
                    _run("systemctl", "--user", "stop", target.name)
                raise


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
    # Keep the managed entry point, not its current versioned symlink target.
    # Runtime updates switch this link without replacing account launchd jobs.
    if configured:
        if not isinstance(configured, str) or any(c in configured for c in "\0\n\r"):
            raise ValueError("Codex binary must be a path")
        entry = Path(configured).expanduser()
        binary = str(resolve_directory(str(entry.parent)) / entry.name)
    else:
        binary = str(CodexRuntime().link())
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
        configured_paths = os.environ.get("PORTAL_PATHS_CONFIG")
        root = Path(configured_paths).resolve().parent.parent if configured_paths else instance_root()
        installation = root / "installation.json"
        from management_common.instance_config import read_installation
        target = read_installation(root)["target"] if installation.is_file() else {}
        account = account or target.get("windows_service_account", "")
        if target.get("windows_service_mode") == "user":
            content = json.dumps({"mode": "user", "name": name, "component": "codex",
                                  "root": str(root), "code": str(SOURCE_ROOT),
                                  "python": sys.executable, "command": command},
                                 sort_keys=True, ensure_ascii=False) + "\n"
        else:
            if not account:
                raise ValueError("PORTAL_WINDOWS_SERVICE_ACCOUNT is required for Codex SCM service")
            content = adapter.command_unit_text(name, command, account=account,
                                                cwd=SOURCE_ROOT, log_path=instance_root() / "logs" / f"{name}.log",
                                                directory=directory)
    else:
        from scripts.portal_instance_storage import site_environment
        # launchd does not inherit the installing process's environment. In
        # particular npm's Codex entry uses /usr/bin/env node, and Node lives in
        # the desktop bundle rather than the GUI login's default PATH.
        environment = site_environment(deployment)
        for key in ("PATH", "HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR",
                    "LANG", "LC_ALL", "TFI_MACOS_SERVICE_DIRECTORY"):
            if key in os.environ:
                environment[key] = os.environ[key]
        environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
        log_root = instance_root() / "logs"
        log_root.mkdir(parents=True, exist_ok=True)
        content = adapter.command_unit_text(name, command, cwd=SOURCE_ROOT,
                                            log_root=log_root, log_name=name, environment=environment)
    return adapter, directory, name, content, _native_codex_controller()


def _register_native_codex(adapter, directory, name, content, controller):
    """Repair only owned macOS definitions with matching commands and paths.

    Existing jobs with a different command, account or logs remain foreign.
    The caller holds the account lock; stop before atomically changing a job so
    launchd cannot retain its old environment after a successful registration.
    """
    if sys.platform == "darwin":
        path = adapter.unit_path(directory, name)
        if path.is_file() and not path.is_symlink():
            previous = path.read_text(encoding="utf-8")
            expected = plistlib.loads(content.encode("utf-8"))
            old = plistlib.loads(previous.encode("utf-8"))
            # Earlier versions dereferenced the configured executable link.
            # Only normalize that argument when it is the very same file.
            normalized = dict(old)
            args = list(normalized.get("ProgramArguments", []))
            expected_args = expected["ProgramArguments"]
            binary_index = expected_args.index("--binary") + 1
            if (len(args) == len(expected_args) and all(isinstance(arg, str) for arg in args)
                    and args[binary_index] != expected_args[binary_index]):
                try:
                    if Path(args[binary_index]).samefile(expected_args[binary_index]):
                        args[binary_index] = expected_args[binary_index]
                        normalized["ProgramArguments"] = args
                except OSError:
                    pass
            legacy = dict(expected)
            legacy.pop("EnvironmentVariables", None)
            if previous != content and normalized in (legacy, expected):
                controller.stop(name)
                with tempfile.NamedTemporaryFile(dir=directory, prefix=".codex-", delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        stream.write(content.encode("utf-8"))
                        stream.flush()
                        os.fsync(stream.fileno())
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
    return adapter.register_services(directory, {name: content}, **(
        {"password_env": "PORTAL_WINDOWS_SERVICE_PASSWORD"} if sys.platform == "win32" else {}))


def _native_codex_controller():
    if sys.platform == "win32":
        from scripts.tfi_windows_user_services import WindowsServiceControl
        return WindowsServiceControl(instance_root() / "service-units")
    from scripts.tfi_release_platform_macos import service_controller
    return service_controller()


def _native_codex_state(account_id: str) -> dict[str, Any]:
    unit = _codex_unit(account_id)
    state = _native_codex_controller().status(unit)
    return {"unit": unit, "load": state["load"], "active": state["active"],
            "sub": state["sub"], "pid": state["pid"], "enabled": state["load"] == "loaded"}


def verify_native_codex_services(names):
    """An active OS label alone cannot authorize a Provider update."""
    from management_common.instance_config import read_installation
    from scripts.tfi_release_provider_services import owned_services
    configured = os.environ.get("PORTAL_PATHS_CONFIG")
    if not configured:
        raise ValueError("Native Provider update requires installation metadata")
    root = Path(configured).resolve().parent.parent
    target = read_installation(root)["target"]
    if sys.platform == "win32":
        from scripts import tfi_release_platform_windows as adapter
    else:
        from scripts import tfi_release_platform_macos as adapter
    plan = owned_services(target, adapter.unit_directory())
    if not plan or not set(names).issubset(plan["names"]):
        raise ValueError("Provider host has no owned service definition")


def start_codex_instance(account_id: str) -> dict[str, Any]:
    from management_portal.execution.account_hosting import ensure_account_home, codex_socket_path
    ensure_account_home("codex", account_id)
    codex_socket_path(account_id).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.platform in {"win32", "darwin"}:
        adapter, directory, name, content, controller = _native_codex_service(account_id)
        directory.mkdir(parents=True, exist_ok=True)
        lock_root = instance_root() / "service-locks"
        lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (lock_root / f"{name}.lock").open("a+b") as stream, lock_stream(stream):
            created = _register_native_codex(adapter, directory, name, content, controller)
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
        _native_codex_controller().stop(_codex_unit(account_id))
        return _native_codex_state(account_id)
    _run("systemctl", "--user", "stop", _codex_unit(account_id))
    return codex_instance_state(account_id)


def remove_codex_instance(account_id: str) -> None:
    from management_portal.execution.account_hosting import codex_socket_path
    if sys.platform in {"win32", "darwin"}:
        if sys.platform == "win32":
            from scripts import tfi_release_platform_windows as adapter
            directory = instance_root() / "service-units"
        else:
            from scripts import tfi_release_platform_macos as adapter
            directory = adapter.unit_directory()
        name = _codex_unit(account_id)
        _native_codex_controller().stop(name)
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

