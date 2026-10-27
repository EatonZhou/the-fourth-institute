"""Fixed deployment mechanics for TFI release bundles.

Selection and database sanitization belong to the release generator. This module
owns target isolation, installation state and the lifecycle of its own services.
An installed instance still needs the supervising Resident's functional review.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from urllib.parse import quote
from urllib.request import build_opener, ProxyHandler

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if Path(__file__).parent.name == "runtime":
    SOURCE_ROOT = SOURCE_ROOT / "code"
sys.path.insert(0, str(SOURCE_ROOT))
sys.dont_write_bytecode = True
from management_common.portal_paths import KEYS, ENV_KEYS
from management_common.file_lock import try_lock_descriptor
from management_common.process_runtime import run_process
from management_common.native_output import decode_native_output, run_native_capture, run_utf8_capture
from scripts.tfi_instance_layout import code_root, database_root, installer_path, validate_shared_code
from management_common.instance_config import (STATE_SCHEMA, LEGACY_SCHEMA, read_installation,
                                              write_installation, configuration_digest)

STATE_FILE = "installation.json"
# These probes only address this instance on loopback. System proxy settings
# (including macOS network preferences) must never route them off the machine.
_LOCAL_HTTP = build_opener(ProxyHandler({}))
SCHEMA = STATE_SCHEMA
COMPONENTS = ("postgres", "nats", "resource", "execution", "society", "worker", "web")
MODULES = {"resource": "management_portal.resources.server",
           "execution": "management_portal.execution.service",
           "society": "management_portal.society_service.server",
           "worker": "management_portal.execution.worker_manager"}


def service_controller():
    return platform_adapter().service_controller()


class PlatformPreflightError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__("; ".join(report["reasons"]))


def platform_preflight(manifest):
    from management_common.distribution_targets import preflight_manifest
    report = preflight_manifest(manifest)
    if not report["ok"]:
        raise PlatformPreflightError(report)
    return report


def platform_adapter():
    # The target catalog is the sole capability authority. Import native assets
    # only after it accepts the actual host; no parallel supported-platform list.
    from management_common.distribution_targets import identify_host
    actual = identify_host()["platform"]
    return importlib.import_module(f"scripts.tfi_release_platform_{actual}")


def read_json(path):
    if Path(path).name == STATE_FILE:
        return read_installation(Path(path).parent)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    if path.name == STATE_FILE:
        return write_installation(path.parent, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def safe_path(value):
    return platform_adapter().safe_path(value)


def target_plan(target, *, protected_roots=None, allow_missing_native=False):
    """Validate without writing directories, databases or service definitions."""
    from management_common.portal_paths import paths as current_paths
    root = safe_path(target["install_root"])
    protected = [Path(p) for p in protected_roots] if protected_roots is not None else [SOURCE_ROOT, *current_paths().values()]
    if len(root.parts) < 4 or any(overlap(root, p.resolve()) for p in protected):
        raise ValueError("Install root overlaps the current Portal or its data roots")
    validate_shared_code(target)
    if target.get("shared_code_root"):
        safe_path(target["shared_code_root"])
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", target.get("database_user", "tfi")):
        raise ValueError("Invalid database user")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", target.get("runtime_instance", target["service_prefix"])):
        raise ValueError("Invalid runtime instance")
    prefix = target["service_prefix"]
    if not re.fullmatch(r"tfi-[a-z0-9][a-z0-9-]{2,39}", prefix):
        raise ValueError("service_prefix must be a unique tfi- name (6-44 characters)")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", target["database_name"]):
        raise ValueError("Invalid independent database name")
    supervisor = target.get("supervisor_resident_id", "")
    if supervisor and not re.fullmatch(r"resident_[a-f0-9]{12}", supervisor):
        raise ValueError("supervisor_resident_id must be a real Resident ID")
    if not isinstance(target.get("run_id"), str) or not target["run_id"].strip():
        raise ValueError("run_id is required")
    if any(c in target["run_id"] for c in "\r\n%$"):
        raise ValueError("Invalid run_id")
    roots = target["data_roots"]
    if set(roots) != set(KEYS):
        raise ValueError("All five data_roots must be explicit")
    resolved = {key: safe_path(value) for key, value in roots.items()}
    external = target.get("external_data_roots", [])
    if (not isinstance(external, list) or any(not isinstance(key, str) or key not in KEYS for key in external)
            or len(set(external)) != len(external)):
        raise ValueError("external_data_roots must list unique data root keys")
    reserved = [root / part for part in ("code", "runtime", "config", "home")]
    for key, path in resolved.items():
        if key in external and not path.is_relative_to(root):
            if (len(path.parts) < 2 or path in {Path(p) for p in
                    ('/bin', '/boot', '/dev', '/etc', '/lib', '/lib64', '/proc', '/root',
                     '/run', '/sbin', '/sys', '/tmp', '/usr', '/var', '/home')}
                    or overlap(path, root)
                    or overlap(path, SOURCE_ROOT.resolve()) or overlap(path, code_root(target).resolve())):
                raise ValueError(f"{key} external directory overlaps installation or source paths")
        elif path == root or not path.is_relative_to(root):
            raise ValueError(f"{key} must be an independent directory inside install_root")
        if any(overlap(path, p) for p in reserved):
            raise ValueError(f"{key} overlaps a reserved installation directory")
        if any(overlap(path, other) for name, other in resolved.items() if name != key):
            raise ValueError("The five data roots must not overlap")
    ports = target["ports"]
    if set(ports) != {"web", "postgres", "nats"} or any(type(p) is not int or not 1024 <= p <= 65535 for p in ports.values()) or len(set(ports.values())) != 3:
        raise ValueError("Three different nonprivileged web/postgres/nats ports are required")
    bind = target.get("bind", "127.0.0.1")
    if bind not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("bind must be 127.0.0.1 or 0.0.0.0")
    if type(target.get("enable_lineage_timers", False)) is not bool:
        raise ValueError("enable_lineage_timers must be a boolean")
    adapter = platform_adapter()
    if adapter.__name__.endswith("_windows"):
        adapter.validate_service_mode(target)
    if not adapter.__name__.endswith("_windows"):
        # Core RPC can use authenticated loopback for long paths. PostgreSQL
        # still uses its native Unix socket; validate that actual address.
        limit = 104 if adapter.__name__.endswith("_macos") else 108
        if len(os.fsencode(root / "runtime/s" / f".s.PGSQL.{ports['postgres']}")) >= limit:
            raise ValueError("Install root is too long for the PostgreSQL socket; use a shorter independent directory")
    # Keep the venv interpreter's lexical path: resolve() would bypass its venv.
    python = Path(target["python"]).absolute()
    pg_bin = safe_path(target["pg_bin"])
    pg_share = safe_path(target["pg_share"])
    nats = safe_path(target["nats_server"])
    managed = set(target.get('managed_dependencies', [])) if allow_missing_native else set()
    executables = [python]
    if 'nats' not in managed:
        executables.append(nats)
    if 'postgres' not in managed:
        executables.extend(pg_bin / native_tool(name) for name in ("initdb", "postgres", "pg_isready", "psql", "createdb", "pg_restore"))
    for executable in executables:
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError(f"Required executable is unavailable: {executable}")
    if 'postgres' not in managed and not (pg_share / "postgres.bki").is_file():
        raise ValueError("pg_share must contain the matching PostgreSQL initialization files")
    normalized = {**target, "install_root": str(root), "data_roots": {k: str(v) for k, v in resolved.items()},
                  "python": str(python), "pg_bin": str(pg_bin), "pg_share": str(pg_share),
                  "nats_server": str(nats), "bind": bind, "supervisor_resident_id": supervisor,
                  "enable_lineage_timers": target.get("enable_lineage_timers", False)}
    if target.get("pg_lib"):
        normalized["pg_lib"] = str(safe_path(target["pg_lib"]))
    if target.get('node_bin'):
        normalized['node_bin'] = str(safe_path(target['node_bin']))
    return normalized


def clean_environment(target, *, postgres=False):
    root = Path(target["install_root"])
    runtime = root / "runtime"
    paths = target["data_roots"]
    database_user = target.get("database_user", "tfi")
    timers_enabled = bool(target.get("enable_lineage_timers", False))
    state_path = root / STATE_FILE
    if state_path.is_file():
        local_state = read_json(state_path)
        initialization = local_state.get("feature_initialization", {})
        same_target = (local_state.get('configuration_sha256') == configuration_digest(target)
                       if local_state.get('schema') == STATE_SCHEMA else local_state.get('target') == target)
        if (same_target and initialization.get("status") == "applied"
                and initialization.get("manifest_sha256") == local_state.get("manifest_sha256")):
            timers_enabled = timers_enabled or initialization.get("result", {}).get("lineage_timer_required") is True
    windows = platform_adapter().__name__.endswith("_windows")
    env = {"PATH": (os.environ.get("SystemRoot", r"C:\Windows") + r"\System32;" +
                     os.environ.get("SystemRoot", r"C:\Windows") +
                     (";" + str(Path(target["pg_bin"])) if postgres else ""))
                    if windows else "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
           "HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "home/.config"),
           "XDG_CACHE_HOME": str(root / "home/.cache"), "TMPDIR": str(runtime),
           "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
           "PORTAL_PATHS_CONFIG": str(root / "config/target.json"),
           "PORTAL_SITE_CONFIG": str(root / "config/site.json"),
           "PORTAL_NETWORK_CONFIG": str(root / "config/network.env"),
           "PORTAL_URL": f"http://127.0.0.1:{target['ports']['web']}",
           "PORTAL_POSTGRES_ENV_FILE": str(root / "config/postgres.env"),
           "PORTAL_DATABASE_URL": (f"postgresql://{database_user}@127.0.0.1:{target['ports']['postgres']}/{target['database_name']}"
                                   if windows else f"postgresql://{database_user}@/" + target["database_name"] + "?host=" + quote(str(runtime / "s"), safe="") + "&port=" + str(target["ports"]["postgres"])),
           "PORTAL_INSTANCE": target.get("runtime_instance", target["service_prefix"]), "PORTAL_ISA_ROUTING": "socket",
           "PORTAL_LINEAGE_TIMER_INSTANCE": target.get("runtime_instance", target["service_prefix"]) if timers_enabled else "disabled-until-configured",
           "PORTAL_RESOURCE_DATABASE_SCHEMA": "resource_release",
           "PORTAL_RESOURCE_SERVICE_MODE": "client", "PORTAL_SOCIETY_SERVICE_MODE": "client",
           "PORTAL_NATS_URL": f"nats://127.0.0.1:{target['ports']['nats']}",
           "PORTAL_EXECUTION_STATE_ROOT": str(Path(paths["staging_root"]) / "portal_execution"),
           "PORTAL_EXECUTION_WORKER_SOURCE_ROOT": str(code_root(target)),
           "PORTAL_EXECUTION_WORKER_HEALTH_PATH": str(runtime / "worker-health.json"),
           "PORTAL_EXECUTION_WORKER_ID": target["service_prefix"] + ":worker",
           "PORTAL_EXECUTION_WORKER_GENERATION_ENV": str(root / "config/generation.env"),
           "PORTAL_BUILD_ID": target["run_id"], "PORTAL_LINEAGE_READ_WORKERS": "2",
           "PORTAL_POSTGRES_DOMAIN_POOL_SIZE": "2"}
    env.update({ENV_KEYS[key]: value for key, value in paths.items()})
    generation = root / "config/generation.env"
    if generation.is_file():
        for line in generation.read_text(encoding="utf-8").splitlines():
            if line.startswith("PORTAL_BUILD_ID=") and line.partition("=")[2].strip():
                env["PORTAL_BUILD_ID"] = line.partition("=")[2].strip()
    if windows:
        env.update({"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                    "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
                    "TEMP": str(root / "runtime"), "TMP": str(root / "runtime"),
                    "USERPROFILE": str(root / "home"),
                    "APPDATA": str(root / "home/AppData/Roaming"),
                    "LOCALAPPDATA": str(root / "home/AppData/Local"),
                    # Maintenance helpers retain the operator's service registry
                    # even though application caches belong to the instance.
                    "TFI_WINDOWS_SERVICE_DIRECTORY": str(unit_directory())})
    elif platform_adapter().__name__.endswith("_macos"):
        # launchd belongs to the logged-in OS user even when an application's
        # HOME is isolated under its instance. Keep ownership-marker lookup
        # consistent in setup helpers, lifecycle commands and service children.
        env["TFI_MACOS_SERVICE_DIRECTORY"] = str(unit_directory())
    for component in ("resource", "execution", "society"):
        env[f"PORTAL_{component.upper()}_SERVICE_SOCKET"] = str(runtime / "s" / f"{component}-service.sock")
    if postgres and target.get("pg_lib"):
        if windows:
            env["PATH"] = target["pg_lib"] + ";" + env["PATH"]
        else:
            env["DYLD_LIBRARY_PATH" if platform_adapter().__name__.endswith("_macos") else "LD_LIBRARY_PATH"] = target["pg_lib"]
    from scripts.tfi_instance_layout import dependencies_root
    tool_home = dependencies_root(target) / "tools" if target.get("shared_code_root") else root / "home"
    tool_bins = [root / 'home/.local/bin', tool_home / '.local/bin', *sorted((tool_home / '.TinyTeX/bin').glob('*'))]
    if target.get('node_bin'):
        tool_bins.insert(0, Path(target['node_bin']))
    env['PATH'] = (';' if windows else ':').join([*map(str, tool_bins), env['PATH']])
    env['ARTICLE_TOOL_HOME'] = str(tool_home)
    if target.get("shared_code_root"):
        env['PLAYWRIGHT_BROWSERS_PATH'] = str(dependencies_root(target) / 'browsers')
    return env


def runtime_python(target):
    from scripts.tfi_release_dependencies import runtime_python as resolve
    return resolve(target)


def command_environment(target, command):
    """Keep bundled PostgreSQL libraries private to its native executables.

    Portal and its system tools (Poppler, Node, TeX) must use their own native
    dependencies. A PostgreSQL libstdc++ can be older than the host's libraries.
    """
    executable = Path(command[0])
    tools = {native_tool(name) for name in (
        "postgres", "initdb", "pg_ctl", "pg_isready", "psql", "createdb",
        "dropdb", "pg_dump", "pg_dumpall", "pg_restore",
    )}
    postgres = executable.name in tools and executable.parent == Path(target["pg_bin"])
    environment = clean_environment(target, postgres=postgres)
    # The frozen-history verifier checks the owned OS services before reading
    # the imported database. Its instance HOME stays isolated, but on Linux it
    # must reach the same user manager as the parent installer.
    if (platform_adapter().__name__.endswith('_linux') and len(command) > 1
            and Path(command[1]).name == 'tfi_release_initialization.py'
            and Path(command[1]) == code_root(target) / 'scripts/tfi_release_initialization.py'):
        for key in ('XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'):
            if os.environ.get(key):
                environment[key] = os.environ[key]
    return environment


def prepare_dependencies(state, manifest):
    from scripts.tfi_release_dependencies import prepare
    return prepare(sys.modules[__name__], state, manifest)


def native_tool(name):
    return name + (".exe" if platform_adapter().__name__.endswith("_windows") else "")


def component_command(target, component):
    root = Path(target["install_root"])
    runtime = root / "runtime"
    env = clean_environment(target, postgres=component == "postgres")
    # Application HOME belongs to the instance, but native jobs are owned by
    # the OS user manager. Preserve only its transport coordinates so RunScript
    # can reach systemd without inheriting unrelated operator credentials.
    if platform_adapter().__name__.endswith("_linux"):
        for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
            if os.environ.get(key):
                env[key] = os.environ[key]
    python = runtime_python(target)
    if component == "postgres":
        windows = platform_adapter().__name__.endswith("_windows")
        command = [str(Path(target["pg_bin"]) / native_tool("postgres")), "-D", str(database_root(target)),
                   *([] if windows else ["-k", str(runtime / "s")]),
                   "-p", str(target["ports"]["postgres"]),
                   "-c", "listen_addresses=127.0.0.1" if windows else "listen_addresses=",
                   "-c", "max_connections=200"]
    elif component == "nats":
        command = [target["nats_server"], "-a", "127.0.0.1", "-p", str(target["ports"]["nats"])]
    elif component == "web":
        command = [python, "-m", "uvicorn", "management_portal.asgi:app", "--host", target["bind"],
                   "--port", str(target["ports"]["web"]), "--workers", "1", "--no-access-log"]
    else:
        command = [python, "-m", MODULES[component]]
        if component in {"resource", "society"}:
            env[f"PORTAL_{component.upper()}_SERVICE_MODE"] = "local"
            env[f"PORTAL_{component.upper()}_SERVICE_PROCESS"] = "1"
    # An optional development pair adds a second Web, not a second instance or
    # database. Its shared components continue to use these same owned services.
    development = target.get('development')
    if development is None and (root / STATE_FILE).is_file():
        from management_common.instance_config import has_development, read_development
        if has_development(root):
            development = read_development(root)
    if development is not None and component not in {"postgres", "nats"}:
        from scripts.tfi_development_runtime import adapt_component
        command, env = adapt_component(target, component, command, env, development)
    return command, env


def unit_name(target, component):
    return platform_adapter().unit_name(target, component)


def unit_directory():
    return platform_adapter().unit_directory()


def unit_text(target, component):
    return platform_adapter().unit_text(target, component, COMPONENTS)


def check_available(target, *, existing=False):
    root = Path(target["install_root"])
    controller = service_controller()
    for component in COMPONENTS:
        name = unit_name(target, component)
        adapter = platform_adapter()
        path = adapter.unit_path(unit_directory(), name)
        if existing:
            if path.exists() and (path.is_symlink() or path.read_text(encoding="utf-8") != unit_text(target, component)):
                raise ValueError(f"Service definition ownership mismatch: {name}")
            if path.exists() and hasattr(adapter, "verify_service"):
                adapter.verify_service(name, path.read_text(encoding="utf-8"))
            if not path.exists() and controller.status(name)["load"] != "not-found":
                raise ValueError(f"Service identity exists without ownership marker: {name}")
        elif path.exists() or controller.status(name)["load"] != "not-found":
            raise ValueError(f"Service identity already exists: {name}")
    if not existing:
        if root.exists() and any(p.name != "seed" or not target.get("shared_code_root") or not p.is_dir() or p.is_symlink() for p in root.iterdir()):
            raise ValueError("Installation root is not empty and has no matching installation identity")
        for port in target["ports"].values():
            with socket.socket() as probe:
                probe.bind(("0.0.0.0", port))


def owned_state(root, *, allow_missing_native=False):
    root = Path(root).resolve()
    state = read_json(root / STATE_FILE)
    if state.get("schema") not in {SCHEMA, LEGACY_SCHEMA} or state["target"]["install_root"] != str(root):
        raise ValueError("Installation identity does not match this root")
    target = target_plan(state["target"], protected_roots=state["protected_roots"], allow_missing_native=allow_missing_native)
    if target != state["target"]:
        raise ValueError("Installation target changed (including symlink redirection)")
    check_available(target, existing=True)
    return state


def run_native(command, *, target, cwd=None, timeout=90, merge_output=False):
    return run_native_capture(command, env=command_environment(target, command), cwd=cwd,
                              timeout=timeout, merge_output=merge_output)


def run_checked(command, *, target, cwd=None, timeout=1800, native_output=False):
    try:
        if native_output:
            result = run_native(command, target=target, cwd=cwd, timeout=timeout, merge_output=True)
        else:
            result = run_utf8_capture(command, env=command_environment(target, command), cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"{Path(command[0]).name} exceeded {timeout}s; its process tree was stopped") from exc
    if result.returncode:
        # Child output is retained locally for diagnosis, never echoed with credentials.
        log = Path(target["install_root"]) / "runtime/command-error.log"
        log.write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
        log.chmod(0o600)
        raise RuntimeError(f"{Path(command[0]).name} exited {result.returncode}; diagnostic log: {log}")
    return result.stdout


def database_command(target, tool, *arguments):
    host = "127.0.0.1" if platform_adapter().__name__.endswith("_windows") else str(Path(target["install_root"]) / "runtime/s")
    return [str(Path(target["pg_bin"]) / native_tool(tool)), "-h", host,
            "-p", str(target["ports"]["postgres"]), "-U", target.get("database_user", "tfi"), *arguments]


def wait_database(target):
    for _ in range(60):
        command = database_command(target, "pg_isready", "-d", "postgres")
        try:
            result = run_process(command, env=command_environment(target, command),
                                 capture_output=True, timeout=9)
        except subprocess.TimeoutExpired:
            continue
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("Independent PostgreSQL did not become ready")


def save_stage(state, phase, status, **details):
    state["status"] = status
    state.setdefault("stages", []).append({"phase": phase, "status": status, "time": time.time(), **details})
    write_json(Path(state["target"]["install_root"]) / STATE_FILE, state)


@contextlib.contextmanager
def installation_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".install.lock").open("a") as stream:
        if not try_lock_descriptor(stream.fileno()):
            raise BlockingIOError("Another installer owns this independent instance")
        yield


def prepare_configuration(target):
    root = Path(target["install_root"])
    for path in [root / "home", root / "runtime/s", root / "config", *map(Path, target["data_roots"].values())]:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    site_path = root / "config/site.json"
    site = read_json(site_path) if site_path.exists() else {}
    site.setdefault("site_name", "TFI Portal")
    deployment = site.setdefault("deployment", {})
    deployment.setdefault("installation_id", uuid.uuid4().hex)
    deployment.setdefault("auth", {}).setdefault("session_cookie_name", "db_session_" + target["service_prefix"])
    deployment.setdefault("native_shell", {})["database_env_file"] = str(root / "config/postgres.env")
    deployment.setdefault("native_ui", {})["test_results_root"] = str(root / "runtime/test-results")
    write_json(site_path, site)
    from scripts.tfi_instance_config import projections, _write_bytes
    for name, content in projections(root, target).items():
        _write_bytes(root / name, content)
    (root / "config/generation.env").write_text("PORTAL_BUILD_ID=" + target["run_id"] + "\n")
    for path in (root / "config").iterdir():
        path.chmod(0o600)
    if not target.get("shared_code_root"):
        shutil.copyfile(Path(__file__), root / "runtime/installer.py")


def register_services(target):
    return platform_adapter().register_services(unit_directory(), {
        unit_name(target, c): unit_text(target, c) for c in COMPONENTS})


def initialize_database(state):
    target = state["target"]
    root = Path(target["install_root"])
    pg_data = database_root(target)
    if not (pg_data / "PG_VERSION").exists():
        windows = platform_adapter().__name__.endswith("_windows")
        locale = "C" if platform_adapter().__name__.endswith("_macos") else "C.UTF-8"
        run_checked([str(Path(target["pg_bin"]) / native_tool("initdb")), "-D", str(pg_data),
                     "-L", target["pg_share"], "--username=" + target.get("database_user", "tfi"), "--encoding=UTF8",
                     *([] if windows else ["--locale=" + locale]),
                     "--auth-local=trust", "--auth-host=trust" if windows else "--auth-host=reject"],
                    target=target, native_output=True)
    service_controller().start(unit_name(target, "postgres"))
    wait_database(target)


def engineering_path(bundle, value):
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("Engineering paths must be relative and stay inside the bundle")
    result = bundle / relative
    if not result.resolve().is_relative_to(bundle) or any(p.is_symlink() for p in (result, *result.parents) if p.is_relative_to(bundle)):
        raise ValueError("Engineering contains a symlink or path outside its bundle")
    return result


def validate_engineering(manifest_path, target, *, defer_data_verification=False, skip_file_verification=False):
    manifest_path = Path(manifest_path).resolve(strict=True)
    bundle = manifest_path.parent
    manifest = read_json(manifest_path)
    if manifest.get("schema") != "tfi.release-engineering.v1" or (bundle / "GENERATION_FAILED").exists():
        raise ValueError("Unsupported or incomplete engineering bundle")
    if manifest.get("unresolved"):
        raise ValueError("Engineering has unresolved selection items")
    for key in ("run_id", "supervisor_resident_id"):
        if manifest.get(key) != target[key]:
            raise ValueError(f"Engineering and target {key} must match")
    if overlap(bundle, Path(target["install_root"])):
        raise ValueError("Engineering and installation directories must be independent")
    if manifest["database"]["format"] != "postgresql-sql":
        raise ValueError("This installer requires the generator's postgresql-sql database")
    if skip_file_verification:
        validate_engineering_layout(manifest, bundle)
        return manifest, bundle
    inventory = manifest["inventory"]
    deferred_roots = []
    if defer_data_verification:
        deferred_roots = [engineering_path(bundle, value) for value in manifest["files"].values()]
        core_paths = [engineering_path(bundle, manifest["code"]["path"]),
                      engineering_path(bundle, manifest["database"]["path"])]
        if any(overlap(data, core) for data in deferred_roots for core in core_paths):
            raise ValueError("Deferred data roots must not overlap code or database")
        inventory = [item for item in inventory if not any(
            engineering_path(bundle, item["path"]).is_relative_to(root) for root in deferred_roots)]
    expected = {item["path"] for item in inventory}
    if len(expected) != len(inventory):
        raise ValueError("Duplicate engineering inventory entries")
    actual = set()
    for directory, dirs, files in os.walk(bundle, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink() or path.name == ".git":
                raise ValueError("Engineering may not contain symlinks or Git history")
            if path.is_file() and path != manifest_path:
                actual.add(path.relative_to(bundle).as_posix())
        dirs[:] = [name for name in dirs if Path(directory) / name not in deferred_roots]
    if expected != actual:
        raise ValueError("Engineering inventory does not exactly match its files")
    for item in inventory:
        path = engineering_path(bundle, item["path"])
        if not path.is_file() or digest(path) != item["sha256"]:
            raise ValueError(f"Engineering checksum mismatch: {item['path']}")
    code = engineering_path(bundle, manifest["code"]["path"])
    database =engineering_path(bundle, manifest["database"]["path"])
    if digest(database) != manifest["database"]["sha256"]:
        raise ValueError("Database checksum mismatch")
    code_prefix = code.relative_to(bundle).as_posix() + "/"
    code_files = [{**item, "path": item["path"][len(code_prefix):]} for item in inventory if item["path"].startswith(code_prefix)]
    canonical = json.dumps(code_files, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != manifest["code"]["sha256"]:
        raise ValueError("Code tree checksum mismatch")
    validate_engineering_layout(manifest, bundle)
    return manifest, bundle


def validate_engineering_layout(manifest, bundle):
    code = engineering_path(bundle, manifest["code"]["path"])
    database = engineering_path(bundle, manifest["database"]["path"])
    if not code.is_dir() or not database.is_file():
        raise ValueError("Engineering code or database is missing")
    if set(manifest["files"]) != set(KEYS):
        raise ValueError("Engineering must explicitly supply all five data roots")
    for key, value in manifest["files"].items():
        directory = engineering_path(bundle, value)
        if not directory.is_dir():
            raise ValueError(f"Engineering data directory is missing: {key}")
        if key in {"accounts_root", "secrets_dir"} and any(directory.iterdir()):
            raise ValueError("Engineering must not supply account or secret files")
    declaration = manifest.get("installer")
    entrypoint = declaration.get("entrypoint") if isinstance(declaration, dict) else None
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise ValueError("Engineering manifest requires installer.entrypoint; rebuild it with prepare_install.py")
    installer = engineering_path(bundle, entrypoint)
    if not installer.is_file() or not installer.is_relative_to(code):
        raise ValueError("Engineering installer entrypoint must exist inside code")


def directory_replacements(manifest, target):
    roots = {**target["data_roots"], "code_root": str(code_root(target))}
    replacements = {}
    for entry in manifest["directory_mappings"]:
        key = entry["root"]
        if key not in roots or entry["placeholder"] != "${" + key + "}":
            raise ValueError("Unknown engineering directory placeholder")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Directory mappings must remain inside the named target root")
        if entry["placeholder"] in replacements:
            raise ValueError("Duplicate engineering directory placeholder")
        replacement = Path(roots[key]) / relative
        replacements[entry["placeholder"]] = (replacement.as_posix() if platform_adapter().__name__.endswith("_windows")
                                               else str(replacement))
    return replacements


def map_directories(text, replacements):
    # The generator owns where placeholders occur. Target paths prohibit SQL/JSON
    # quoting characters; this substitutes only its declared installation tokens.
    for token, path in replacements.items():
        text = text.replace(token, path)
    return text


def reject_unsafe_entries(directory, names):
    """Enforce copy boundaries even when inventory verification is skipped."""
    for name in names:
        path = Path(directory) / name
        mode = path.lstat().st_mode
        if name == ".git" or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"Engineering contains an unsafe entry: {path}")
    return []


def copy_materialized_file(src, dst, replacements=None):
    """Publish complete target bytes before exposing the source's file mode.

    A release may contain immutable research files. Materialize a sibling first
    so mapping and retries never write through an existing read-only file.
    """
    destination = Path(dst)
    descriptor, name = tempfile.mkstemp(prefix='.tfi-install-', dir=destination.parent)
    temporary = Path(name)
    prior_mode = None
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            if replacements is None:
                with open(src, 'rb') as source:
                    shutil.copyfileobj(source, stream)
            else:
                content = Path(src).read_bytes()
                try:
                    text = content.decode('utf-8')
                except UnicodeDecodeError:
                    pass
                else:
                    content = map_directories(text, replacements).encode('utf-8')
                stream.write(content)
        shutil.copystat(src, temporary)
        try:
            os.replace(temporary, destination)
        except PermissionError:
            # Windows refuses replacement of a read-only destination. Restore
            # its mode if replacement still fails; source modes are untouched.
            mode = destination.lstat().st_mode
            if not stat.S_ISREG(mode) or mode & stat.S_IWUSR:
                raise
            prior_mode = stat.S_IMODE(mode)
            destination.chmod(prior_mode | stat.S_IWUSR)
            os.replace(temporary, destination)
            prior_mode = None
    finally:
        if prior_mode is not None:
            destination.chmod(prior_mode)
        if temporary.exists():
            temporary.chmod(temporary.stat().st_mode | stat.S_IWUSR)
            temporary.unlink()
    return dst


def copy_engineering(manifest, bundle, target, *, skip_file_verification=False):
    from scripts.tfi_install_progress import FileProgress
    root = Path(target["install_root"])
    replacements = directory_replacements(manifest, target)
    source_code = engineering_path(bundle, manifest["code"]["path"])
    prefixes = list(manifest['files'].values())
    if not target.get('shared_code_root'):
        prefixes.append(manifest['code']['path'])
    sizes = {row['path']: row['size'] for row in manifest['inventory'] if any(
        row['path'].startswith(prefix + '/') for prefix in prefixes)}
    meter = FileProgress('正在复制实例文件', len(sizes), sum(sizes.values()))
    def copy_file(src, dst, mapping=None):
        result = copy_materialized_file(src, dst, mapping)
        meter.advance(sizes.get(Path(src).relative_to(bundle).as_posix(), 0))
        return result
    if target.get("shared_code_root"):
        if not skip_file_verification:
            from scripts.tfi_instance_layout import source_inventory
            if source_inventory(source_code) != source_inventory(code_root(target)):
                raise ValueError("Shared code differs from the verified engineering bundle")
    else:
        shutil.copytree(source_code, code_root(target), dirs_exist_ok=True,
                        ignore=reject_unsafe_entries, copy_function=copy_file)
    for key, source in manifest["files"].items():
        destination = Path(target["data_roots"][key])
        def copy_and_map(src, dst):
            return copy_file(src, dst, replacements)
        shutil.copytree(engineering_path(bundle, source), destination, dirs_exist_ok=True,
                        ignore=reject_unsafe_entries, copy_function=copy_and_map)
    sql_text = engineering_path(bundle, manifest["database"]["path"]).read_text(encoding="utf-8")
    (root / "runtime/distribution.sql").write_text(map_directories(sql_text, replacements), encoding="utf-8")
    shutil.copyfile(bundle / "manifest.json", root / "runtime/engineering-manifest.json")


def import_database(state):
    target = state["target"]
    root = Path(target["install_root"])
    # Repeating a successful install never reimports or overwrites a running DB.
    # An interrupted import must be diagnosed; an existing DB is never dropped.
    run_checked(database_command(target, "createdb", target["database_name"]), target=target, native_output=True)
    run_checked(database_command(target, "psql", "-d", target["database_name"], "-X", "--single-transaction",
                                 "--set", "ON_ERROR_STOP=1", "--file", str(root / "runtime/distribution.sql")),
                target=target, native_output=True)


def binary_architectures(path):
    """Read the native executable header; never execute an unclaimed target ISA."""
    with Path(path).open("rb") as stream:
        data = stream.read(4096)
    machines = {62: "x86_64", 183: "aarch64", 0x8664: "x86_64", 0xAA64: "aarch64",
                0x01000007: "x86_64", 0x0100000C: "aarch64"}
    if data.startswith(b"\x7fELF") and len(data) >= 20:
        endian = "<" if data[5] == 1 else ">" if data[5] == 2 else ""
        return ({machines.get(struct.unpack_from(endian + "H", data, 18)[0], "")} - {""}) if endian else set()
    if data.startswith(b"MZ") and len(data) >= 64:
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if offset + 6 <= len(data) and data[offset:offset + 4] == b"PE\0\0":
            return {machines.get(struct.unpack_from("<H", data, offset + 4)[0], "")}
    magic = data[:4]
    if magic in {b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"} and len(data) >= 8:
        endian = ">" if magic[0] == 0xCA else "<"
        stride = 32 if magic in {b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"} else 20
        count = struct.unpack_from(endian + "I", data, 4)[0]
        return {machines.get(struct.unpack_from(endian + "I", data, 8 + stride * i)[0], "")
                for i in range(min(count, (len(data) - 8) // stride))} - {""}
    if magic in {b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe"} and len(data) >= 8:
        endian = ">" if magic[0] == 0xFE else "<"
        return {machines.get(struct.unpack_from(endian + "I", data, 4)[0], "")} - {""}
    return set()


def dependency_preflight(target, bundle, *, skip_file_verification=False, manifest=None):
    """Execute read-only probes before creating even the installation lock."""
    from management_common.distribution_targets import identify_host
    host = identify_host()
    adapter = platform_adapter()
    if hasattr(adapter, "dependency_preflight"):
        adapter.dependency_preflight(target)
    for name, path in (("PostgreSQL", Path(target["pg_bin"]) / native_tool("postgres")),
                       ("NATS", Path(target["nats_server"]))):
        if host["architecture"] not in binary_architectures(path):
            raise ValueError(f"Dependency preflight failed: {name} binary does not match {host['platform']}/{host['architecture']}")
    checks = []
    probes = [("Python", [target["python"], "--version"]),
              ("PostgreSQL", [str(Path(target["pg_bin"]) / native_tool("postgres")), "--version"]),
              ("NATS", [target["nats_server"], "--version"]),
              ("core dependencies", [target["python"], str(bundle / "code/scripts/bootstrap_portal"),
                                     "doctor", "core", "--venv", str(Path(target["python"]).parent.parent), "--json"])]
    from management_common.release_dependencies import profiles
    selected = profiles(manifest or {})
    if selected:
        probes = probes[:-1]  # Selected packages are prepared in the owned root.
    for name, command in probes:
        try:
            if name in {"PostgreSQL", "NATS"}:
                result = run_native(command, target=target, cwd=bundle / "code", timeout=90)
            else:
                result = run_utf8_capture(command, env=clean_environment(target), cwd=bundle / "code",
                                          timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"Dependency preflight failed: {name} ({type(exc).__name__})") from exc
        if result.returncode:
            raise ValueError(f"Dependency preflight failed: {name} exited {result.returncode}; no installation files were created")
        record = {"dependency": name, "verification": "passed", "source": "existing-local-installation",
                  "executable": str(Path(command[0]).absolute()), "executable_sha256": None if skip_file_verification else digest(Path(command[0]).resolve())}
        if name == "core dependencies":
            # Doctor output is an inventory, never an authenticated download URL.
            doctor = json.loads(result.stdout)
            record["resolved_inventory"] = doctor
            record["constraints"] = [{"path": str(bundle / "code" / filename),
                                      "sha256": None if skip_file_verification else digest(bundle / "code" / filename)}
                                     for filename in ("requirements.txt",) if (bundle / "code" / filename).is_file()]
        else:
            record["version"] = result.stdout.strip().splitlines()[0]
            if name == 'Python' and selected and not re.match(r'Python 3\.12\.', record['version']):
                raise ValueError('Dependency preflight failed: Python 3.12 is required')
        checks.append(record)
    return checks


def initialize_features(state, manifest, **options):
    from scripts.tfi_release_initialization import apply
    return apply(sys.modules[__name__], state, manifest, **options)


def install(manifest_path, raw_target, *, prepare_only=False, freeze_evidence=None, defer_data_verification=False, skip_file_verification=False):
    input_manifest = read_json(manifest_path)
    preflight = platform_preflight(input_manifest)
    from scripts.tfi_release_native_dependencies import plan as native_plan, prepare as prepare_native
    raw_target, native_entries = native_plan(raw_target, input_manifest)
    from management_common.portal_paths import paths as current_paths
    root = safe_path(raw_target["install_root"])
    prior = owned_state(root, allow_missing_native=bool(native_entries)) if (root / STATE_FILE).is_file() else None
    upgrade_journal = root / "maintenance/upgrade.json"
    if upgrade_journal.is_file() and read_json(upgrade_journal).get("phase") not in {"complete", "restored_stopped", "prebackup_failed"}:
        raise ValueError("Interrupted upgrade requires the recover command before installation can resume")
    target = target_plan(raw_target, protected_roots=prior["protected_roots"] if prior else None,
                         allow_missing_native=bool(native_entries))
    if prior and prior["target"] != target:
        raise ValueError("Existing installation has different target parameters")
    manifest, bundle = validate_engineering(manifest_path, target, defer_data_verification=defer_data_verification,
                                            skip_file_verification=skip_file_verification)
    preflight = platform_preflight(manifest)
    manifest_hash = digest(manifest_path)
    preflight.update(engineering_sha256=manifest_hash, checked_at=time.time())
    preflight["data_verification"] = "deferred_by_operator" if defer_data_verification or skip_file_verification else "inventory_sha256"
    preflight["file_verification"] = "skipped_by_operator" if skip_file_verification else "core_inventory_sha256"
    if prior and prior["manifest_sha256"] != manifest_hash:
        raise ValueError("Existing installation belongs to a different immutable engineering bundle")
    check_available(target, existing=bool(prior))
    if not native_entries:
        preflight["dependency_checks"] = dependency_preflight(target, bundle, skip_file_verification=skip_file_verification, manifest=manifest)
    else:
        adapter = platform_adapter()
        if hasattr(adapter, 'dependency_preflight'):
            adapter.dependency_preflight(target)
        python_probe = run_utf8_capture([target['python'], '--version'], env=clean_environment(target),
                                        timeout=90)
        if python_probe.returncode or not re.match(r'Python 3\.12\.', python_probe.stdout):
            raise ValueError('Native dependency preparation requires preinstalled Python 3.12')
    with installation_lock(root):
        state = prior or {"schema": SCHEMA, "target": target, "manifest_sha256": manifest_hash,
                          "protected_roots": [str(SOURCE_ROOT), *map(str, current_paths().values())],
                          "completed": [], "stages": [], "status": "installing"}
        state["platform_preflight"] = preflight
        if not prior and set(p.name for p in root.iterdir()) - ({".install.lock", "seed"} if target.get("shared_code_root") else {".install.lock"}):
            raise ValueError("Concurrent or unowned installation files detected")
        write_json(root / STATE_FILE, state)
        phase = "prepare"
        registered = []
        try:
            if native_entries:
                phase = 'native_dependencies'
                state['native_dependencies'] = prepare_native(sys.modules[__name__], target, native_entries)
                target_plan(target, protected_roots=state['protected_roots'])
                preflight['dependency_checks'] = dependency_preflight(target, bundle,
                    skip_file_verification=skip_file_verification, manifest=manifest)
                save_stage(state, phase, 'prepared')
            phase = 'prepare'
            if "prepare" not in state["completed"]:
                prepare_configuration(target)
                copy_engineering(manifest, bundle, target, skip_file_verification=skip_file_verification)
                state["completed"].append("prepare")
                save_stage(state, phase, "installing")
            phase = "dependencies"
            prepare_dependencies(state, manifest)
            phase = "services"
            registered = register_services(target)
            phase = "database"
            initialize_database(state)
            if "database" not in state["completed"]:
                import_database(state)
                state["completed"].append("database")
                save_stage(state, phase, "installing")
            if prepare_only:
                controller = service_controller()
                if any(controller.status(unit_name(target, c))["active"] not in {"inactive", "failed"} for c in COMPONENTS[1:]):
                    raise RuntimeError("prepare-only requires stopped application services; use this instance's stop command first")
                save_stage(state, "awaiting_start", "prepared")
                return {"status": "prepared", "run_id": target["run_id"],
                        "supervisor_resident_id": target["supervisor_resident_id"],
                        "installation_root": str(root), "database_name": target["database_name"],
                        "running_services": [unit_name(target, "postgres")],
                        "functional_review": "pending_resident"}
            phase = "feature_initialization"
            initialize_features(state, manifest, freeze_evidence=freeze_evidence)
            phase = "start"
            controller = service_controller()
            for component in COMPONENTS[1:]:
                print('@TFI_PROGRESS ' + json.dumps({'message': '正在启动 ' + component}, ensure_ascii=False), flush=True)
                controller.start(unit_name(target, component))
            save_stage(state, phase, "checking")
            phase = "health"
            result = wait_for_ready(state, progress=lambda message: print(
                '@TFI_PROGRESS ' + json.dumps({'message': message}, ensure_ascii=False), flush=True))
            state["result"] = result
            if result["status"] != "installed":
                raise RuntimeError(health_failure(state, result))
            result["external_requirements"] = manifest.get("external_requirements", [])
            result["feature_initialization"] = state.get("feature_initialization", {}).get("result", {"status": "legacy_not_declared"})
            save_stage(state, phase, "installed")
            write_json(root / "installation-result.json", result)
            return result
        except Exception as exc:
            rollback_errors = []
            if not prior and registered:
                controller = service_controller()
                for name in reversed(registered):
                    try:
                        if controller.status(name)["load"] != "not-found":
                            controller.stop(name)
                    except Exception as stop_error:
                        rollback_errors.append(f"{name}: {stop_error}")
                if not rollback_errors:
                    try:
                        platform_adapter().remove_services(unit_directory(), list(reversed(registered)))
                    except Exception as remove_error:
                        rollback_errors.append(str(remove_error))
            save_stage(state, phase, "failed", error=str(exc), rollback_errors=rollback_errors)
            raise


def health_failures(result):
    """Describe the specific checks still blocking startup."""
    failures = [service['service'] + ': ' + service['active'] for service in result['services']
                if service['active'] != 'active']
    for check in result['checks']:
        if check['ok']:
            continue
        detail = check.get('error', '')
        payload = check.get('payload', {})
        if isinstance(payload, dict):
            reasons = [name for name in ('execution_layer', 'society_service')
                       if isinstance(payload.get(name), dict) and payload[name].get('ok') is not True]
            if reasons:
                detail = (detail + '; ' if detail else '') + '未就绪: ' + ', '.join(reasons)
        failures.append((check.get('component') or check.get('route', 'health')) + ': ' + (detail or '未就绪'))
    return failures


def health_failure(state, result):
    """Save the pre-rollback observations and point at instance-owned logs."""
    root = Path(state['target']['install_root'])
    failures = health_failures(result)
    report = root / 'runtime/startup-diagnostics.json'
    excerpts = {}
    error_excerpts = {}
    for component in COMPONENTS:
        name = unit_name(state['target'], component)
        failed_check = any(not check['ok'] and (
            check.get('component') == component or (component == 'web' and check.get('route')))
            for check in result['checks'])
        if not failed_check and not any(s['service'] == name and s['active'] != 'active' for s in result['services']):
            continue
        log = root / 'runtime' / (component + '.log')
        try:
            with log.open('rb') as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 8192))
                tail = stream.read().decode('utf-8', errors='replace')
            excerpts[component] = '\n'.join(tail.splitlines()[-30:])
        except OSError as exc:
            excerpts[component] = f'无法读取组件日志: {exc}'
        # launchd writes stderr separately; Python startup tracebacks usually
        # land here while the corresponding stdout file is empty.
        try:
            with (root / 'runtime' / (component + '.error.log')).open('rb') as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 8192))
                tail = stream.read().decode('utf-8', errors='replace')
            if tail.strip():
                error_excerpts[component] = '\n'.join(tail.splitlines()[-30:])
        except OSError:
            pass
    write_json(report, {'status': 'failed', 'observed': result,
                       'component_log_tails': excerpts,
                       'component_error_log_tails': error_excerpts,
                       'component_logs': {c: str(root / 'runtime' / (c + '.log')) for c in COMPONENTS}})
    detail = ''.join(f'\n\n{c}.log（末尾）：\n{tail}' for c, tail in excerpts.items())
    detail += ''.join(f'\n\n{c}.error.log（末尾）：\n{tail}' for c, tail in error_excerpts.items())
    return '服务启动检查失败：' + '; '.join(failures) + f'。诊断记录：{report}；组件日志：{root / "runtime"}' + detail


def wait_for_ready(state, *, timeout=120, progress=None):
    """One bounded readiness pass shared by installation and desktop startup."""
    progress = progress or (lambda message: None)
    started = time.monotonic()
    deadline = started + timeout
    last_progress = None
    next_progress = started
    last_errors, repeats = None, 0
    progress('正在检查 Portal 就绪状态')
    while True:
        remaining = max(0.1, deadline - time.monotonic())
        result = observed_status(state, request_timeout=min(3, remaining / 2))
        now = time.monotonic()
        if result['status'] == 'installed' or now >= deadline:
            return result
        # A user-mode supervisor does not restart an exited child.
        if state['target'].get('windows_service_mode') == 'user' and any(
                service['active'] in {'inactive', 'failed'} and not service.get('pid')
                for service in result['services']):
            return result
        expected = state['target'].get('runtime_instance', state['target']['service_prefix'])
        if any(isinstance(check.get('payload'), dict)
               and check['payload'].get('instance') not in (None, expected)
               for check in result['checks']):
            return result
        # Briefly retry application errors, but don't spend minutes on a broken
        # login route. Overload/gateway responses can still recover at startup.
        errors = tuple((check.get('route'), check.get('http_status'))
                       for check in result['checks'] if not check['ok']
                       and check.get('http_status', 0) >= 400
                       and check.get('http_status') not in {408, 429, 502, 503, 504})
        repeats = repeats + 1 if errors and errors == last_errors else int(bool(errors))
        last_errors = errors
        if repeats >= 3:
            return result
        pending = '; '.join(health_failures(result))
        if pending != last_progress or now >= next_progress:
            progress(f'正在检查 Portal 就绪状态（已等待 {int(now - started)} 秒）：' + pending)
            last_progress, next_progress = pending, now + 10
        time.sleep(min(1, max(0, deadline - now)))


def observed_status(state, *, request_timeout=3):
    target = state["target"]
    expected_instance = target.get("runtime_instance", target["service_prefix"])
    root = Path(target["install_root"])
    controller = service_controller()
    services = [controller.status(unit_name(target, c)) for c in COMPONENTS]
    url = f"http://127.0.0.1:{target['ports']['web']}"
    checks = []
    for route in ("/api/health", "/login"):
        observation = {"route": route}
        if services[-1]["active"] != "active":
            # The owned Web process is absent. Do not spend Windows connect
            # timeouts probing a stopped server (or mistake another listener
            # for this installation). All readiness requirements still fail.
            checks.append({**observation, "ok": False, "error": "Web service is not running"})
            continue
        try:
            from urllib.error import HTTPError
            try:
                response = _LOCAL_HTTP.open(url + route, timeout=request_timeout)
            except HTTPError as exc:
                response = exc
            with response:
                observation['http_status'] = response.status
                body = response.read()
                ok = response.status == 200
                if not ok:
                    observation['error'] = f'HTTP {response.status}'
            if route == "/api/health":
                try:
                    payload = json.loads(body)
                    if not isinstance(payload, dict):
                        raise ValueError('Expected health object')
                except ValueError:
                    checks.append({**observation, 'ok': False,
                                   'error': observation.get('error') or '健康接口未返回有效的 JSON 对象'})
                    continue
                ok = (ok and payload.get("ok") is True
                      and payload.get("instance") == expected_instance
                      and payload.get("execution_layer", {}).get("ok") is True
                      and payload.get("society_service", {}).get("ok") is True)
                checks.append({**observation, "ok": ok, "payload": payload})
            else:
                checks.append({**observation, "ok": ok, "bytes": len(body)})
        except Exception as exc:
            detail = (observation.get('error', '') + '; ' if observation.get('error') else '') + str(exc)
            checks.append({**observation, "ok": False, "error": detail})
    try:
        worker = read_json(root / "runtime/worker-health.json")
        checks.append({"component": "worker", "ok": worker.get("ok") is True
                       and worker.get("instance") == expected_instance
                       and time.time() - float(worker.get("updated_at", 0)) < 60, "payload": worker})
    except (OSError, ValueError) as exc:
        checks.append({"component": "worker", "ok": False, "error": str(exc)})
    healthy = all(s["active"] == "active" for s in services) and all(c["ok"] for c in checks)
    entry = str(code_root(target) / "scripts/install_tfi_release")
    return {"status": "installed" if healthy else "unhealthy", "run_id": target["run_id"],
            "supervisor_resident_id": target["supervisor_resident_id"], "url": url,
            "listen_address": target["bind"], "services": services, "checks": checks,
            "runtime_dependencies": state.get("dependencies", {"status": "legacy_not_declared"}),
            "functional_review": "pending_resident", "installation_root": str(root),
            "stop_argv": [target["python"], entry, "stop", "--root", str(root)],
            "cleanup_argv": [target["python"], entry, "cleanup", "--root", str(root)]}


def start_blocker(state):
    """One readiness contract shared by the CLI and instance management UI."""
    if state.get("migration", {}).get("business_execution_allowed") is False:
        return "Migration has not completed its cutover checks"
    if "database" not in state.get("completed", []):
        return "Initialize this instance before starting it"
    if not state.get("migration") and not any(stage.get("status") == "installed" for stage in state.get("stages", [])):
        return "Finish installation before starting this instance"
    return ""


def lifecycle(command, root, *, shutdown_timeout=20.0):
    lifecycle_started = time.monotonic()
    if command not in {"status", "start", "stop", "cleanup"}:
        raise ValueError("Unsupported installation lifecycle command")
    # A failed download can leave an owned installation with missing managed
    # binaries. Stopping or removing its registrations must still be possible.
    state = owned_state(root, allow_missing_native=command in {"stop", "cleanup"})
    target = state["target"]
    root = Path(target["install_root"])
    with installation_lock(root):
        if command == "status":
            return observed_status(state)
        controller = service_controller()
        if command == "start":
            blocker = start_blocker(state)
            if blocker:
                raise ValueError(blocker)
            check_available(target, existing=True)
            from scripts.tfi_release_provider_services import owned_services, start_services
            providers = owned_services(target, unit_directory())
            register_services(target)
            controller.start(unit_name(target, "postgres"))
            wait_database(target)
            start_services(providers, controller)
            for component in COMPONENTS[1:]:
                controller.start(unit_name(target, component))
            save_stage(state, "start", "started")
            return {"status": "started", "installation_root": str(root)}
        if sys.platform.startswith("linux") and command == "stop":
            from scripts.tfi_shutdown import stop_instance
            remaining = shutdown_timeout - (time.monotonic() - lifecycle_started)
            if remaining <= 0:
                raise TimeoutError("Instance shutdown deadline elapsed during preparation")
            result = stop_instance(root, timeout=remaining)
            save_stage(state, "stop", "stopped")
            return {"status": "stopped", "installation_root": str(root), **result}
        from scripts.tfi_release_provider_services import owned_services, stop_services, remove_template
        providers = owned_services(target, unit_directory())
        names = [unit_name(target, c) for c in reversed(COMPONENTS)]
        for name in names:
            if controller.status(name)["load"] != "not-found":
                controller.stop(name)
        states = [controller.status(name) for name in names]
        if any(s["active"] not in {"inactive", "failed"} or s["pid"] for s in states):
            raise RuntimeError("Services did not stop; cleanup refused")
        stop_services(providers, controller)
        if command == "stop":
            save_stage(state, "stop", "stopped")
            return {"status": "stopped", "services": states, "installation_root": str(root)}
        # Recheck ownership after stopping and immediately before unlinking units.
        check_available(target, existing=True)
        remove_template(target, unit_directory())
        platform_adapter().remove_services(unit_directory(), names)
    # Windows refuses deletion while .install.lock is still open.
    if target.get("shared_code_root") and (root / "seed").is_dir():
        for child in root.iterdir():
            if child.name != "seed":
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    else:
        shutil.rmtree(root)
    return {"status": "cleaned", "installation_root": str(root), "removed_services": names}


def first_admin(root, username, *, if_needed=False):
    if not username:
        raise ValueError("first-admin requires --username")
    state = owned_state(root)
    target = state["target"]
    root = Path(target["install_root"])
    with installation_lock(root):
        output = run_checked([runtime_python(target), str(code_root(target) / "scripts/portal_setup.py"), "first-admin",
                              "--username", username, "--url", f"http://127.0.0.1:{target['ports']['web']}",
                              *(["--if-needed"] if if_needed else [])],
                             target=target, cwd=code_root(target))
        if if_needed and output.strip() == '{"status":"already_configured"}':
            return {"status": "already_configured"}
        path = Path(target["data_roots"]["secrets_dir"]) / "first-admin-link.txt"
        # The existing setup owner generates the secret. Keep it out of stdout,
        # installation evidence, conversation context and service journals.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(output)
        path.chmod(0o600)
        save_stage(state, "first_admin_invitation", state["status"], username=username, setup_file=str(path))
        return {"status": "setup_link_created", "username": username, "setup_file": str(path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "install", "upgrade", "recover", "status", "start", "stop", "cleanup", "first-admin", "_component", "_service"))
    parser.add_argument("--target", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--component", choices=COMPONENTS)
    parser.add_argument("--username")
    parser.add_argument("--if-needed", action="store_true", help="Create the first administrator invitation only before any account exists")
    parser.add_argument("--prepare-only", action="store_true", help="Import the independent DB but leave application services stopped for Resident inspection")
    parser.add_argument("--freeze-evidence", type=Path, help="Resident verification JSON bound to this prepared run, supervisor and manifest")
    parser.add_argument("--defer-data-verification", action="store_true", help="Explicitly defer data-root inventory/hash checks; code, SQL and isolation checks remain required")
    parser.add_argument("--skip-file-verification", action="store_true", help="Skip installation inventory scans and code/data/SQL hashes; retain configuration and target isolation checks")
    args = parser.parse_args(argv)
    try:
        if args.command == "_service":
            from scripts.tfi_release_windows_service import run_service
            state = read_json(args.root / STATE_FILE)
            if args.component not in {"postgres", "nats"} and state.get("migration", {}).get("business_execution_allowed") is False:
                raise ValueError("Migration has not completed its cutover checks")
            return run_service(state["target"], args.component)
        if args.command == "_component":
            platform_preflight(read_json(args.root / "runtime/engineering-manifest.json"))
            state = read_json(args.root / STATE_FILE)
            if args.component not in {"postgres", "nats"} and state.get("migration", {}).get("business_execution_allowed") is False:
                raise ValueError("Migration has not completed its cutover checks")
            command, env = component_command(state["target"], args.component)
            os.chdir(env.get("PORTAL_COMPONENT_SOURCE_ROOT", str(code_root(state["target"]))))
            os.execve(command[0], command, env)
        elif args.command == "plan":
            manifest = read_json(args.manifest) if args.manifest else {}
            preflight = platform_preflight(manifest)
            from scripts.tfi_release_native_dependencies import plan as native_plan
            raw_target, native_entries = native_plan(read_json(args.target), manifest)
            target = target_plan(raw_target, allow_missing_native=bool(native_entries))
            check_available(target)
            result = {"status": "planned", "target": target, "platform_preflight": preflight,
                      "native_dependencies": native_entries,
                      "services": [unit_name(target, c) for c in COMPONENTS]}
        elif args.command == "install":
            result = install(args.manifest, read_json(args.target), prepare_only=args.prepare_only, freeze_evidence=args.freeze_evidence,
                             defer_data_verification=args.defer_data_verification, skip_file_verification=args.skip_file_verification)
        elif args.command == "first-admin":
            result = first_admin(args.root, args.username, if_needed=args.if_needed)
        elif args.command in {"upgrade", "recover"}:
            target = owned_state(args.root)['target']
            python = runtime_python(target)
            if Path(python).absolute() != Path(sys.executable).absolute():
                # Database maintenance must use this site's verified packages,
                # even when the operator starts from the base Python runtime.
                environment = clean_environment(target)
                # This is still the operator's service controller, not an
                # application service. Keep its user bus and service directory;
                # child services continue to use clean_environment(target).
                for key in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR",
                            "DBUS_SESSION_BUS_ADDRESS"):
                    if key in os.environ:
                        environment[key] = os.environ[key]
                    else:
                        environment.pop(key, None)
                os.execve(python, [python, str(Path(__file__).resolve()),
                                  *(sys.argv[1:] if argv is None else argv)], environment)
            from scripts import tfi_release_upgrade
            # Pass this module even when executed as __main__, preserving one
            # shared installation owner and platform adapter.
            result = tfi_release_upgrade.run(sys.modules[__name__], args.command, args.root, args.manifest)
        else:
            result = lifecycle(args.command, args.root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] not in {"failed", "unhealthy", "recovery_required"} else 1
    except Exception as exc:
        # The launcher retains stderr in its local installation log. Record
        # call sites without locals, command arguments, or source-code lines.
        print(f"Installation command {args.command} failed ({type(exc).__name__}); call stack:",
              file=sys.stderr)
        for frame in traceback.extract_tb(exc.__traceback__):
            print(f"  {frame.filename}:{frame.lineno} in {frame.name}", file=sys.stderr)
        result = {"status": "failed", "error": str(exc), "error_type": type(exc).__name__}
        if isinstance(exc, PlatformPreflightError):
            result["platform_preflight"] = exc.report
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
