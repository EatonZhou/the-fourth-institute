#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.portal_source_layout import COMPONENTS, INSTANCE_LAYOUT, SERVICE_ENTRY_MODULES
from scripts.portal_dev_generation import (
    source_state,
)
from scripts.portal_dev_restart_requests import (
    acknowledge_restart_request,
    read_restart_request,
    restart_request_delay,
)

from management_common.format_tags import schema_matches
from management_common.instance_paths import instance_root
from management_portal.shared_state import atomic_replace_json


ALL_COMPONENTS = set(COMPONENTS)

RESTART_OK = "ok"
RESTART_FAILED = "failed"
DEFAULT_SOURCE_SCAN_INTERVAL_SECONDS = 60.0 * 60.0
_WAKE_EVENT = threading.Event()
DEPLOYED_SOURCE_STATE = (
    instance_root() / 'dev'
    / "deployed-component-source-state.json"
)
DEPLOYED_SOURCE_STATE_SCHEMA = "tfi.portal-deployed-component-source-state.v1"


def _wake_for_restart(_signum: int, _frame: object) -> None:
    _WAKE_EVENT.set()


def changed_paths(
    previous: tuple[tuple[str, int, int], ...],
    current: tuple[tuple[str, int, int], ...],
) -> set[str]:
    before = {path: (mtime, size) for path, mtime, size in previous}
    after = {path: (mtime, size) for path, mtime, size in current}
    return {
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    }


# Each background service restarts for every repository file it imports when
# it starts (module-level imports, followed through the repository), whether
# or not a path rule below names that file.  ResourceService runs SocietyService
# in its own process (PORTAL_SOCIETY_SERVICE_MODE defaults to local), so it
# loads society_service and the execution modules SocietyService imports, such
# as agent_data_store; once a path rule alone kept it on an old agent_data_store
# that still read a dropped table.
#
# Portal Web also imports modules inside request handlers (the Chat read
# receipt loads the resident conversation projection on its first call) and
# keeps each one for the life of the process, so its closure follows imports
# inside functions as well.  10-07 a society_service change restarted only
# SocietyService and ResourceService; Dev Web kept a state store that read a
# removed column, every Chat read receipt failed and the Resident stayed unread.
FUNCTION_IMPORT_COMPONENTS = frozenset({"web", "resource", "lineage"})
# Modules a service never loads although an import names them: Web reaches
# SocietyService over its socket (PORTAL_SOCIETY_SERVICE_MODE=client), so
# get_society_service never imports the in-process server.
NOT_LOADED = {
    "web": frozenset({"management_portal.society_service.server"}),
}
IMPORT_ROOTS = frozenset({
    "management_common",
    "management_portal",
    "scripts",
})
# Modules loaded by a name in a string rather than by an import: the string
# arguments of the named call, in the module that holds the calls.  Each
# Provider's declaration (AgentProvider) names
# its catalog, Adapter, runtime updater and credential paths; every service
# that reads the declarations loads them.  10-11 a publish of an OpenCode
# catalog change restarted only Release Web, and SocietyService,
# ResourceService and ExecutionService kept offering the old routes.
NAMED_MODULE_CALLS = {
    "management_portal.execution.provider_catalog": "AgentProvider",
}
# Each feature's declaration (PortalFeature) names its parts as
# "module:attribute" under a hook; a service loads the part when it reads the
# hook.  Hooks not listed here are read by every service that reads the
# declarations.  The ``modules`` patterns name no loaded module.
FEATURE_DECLARATIONS = "management_portal.portal_features"
FEATURE_HOOK_READERS = (
    ("web.", frozenset({"web"})),
    ("watchboard.", frozenset({"web"})),
    ("resource.", frozenset({"web", "resource"})),
    # Feature applications (Notice Board classification, TAU) resolve their targets in Web.
    ("application.", frozenset({"web"})),
)
_MODULE_IMPORTS: dict[tuple[str, bool, str], tuple[int, int, tuple[str, ...]]] = {}


def _module_file(module: str) -> Path | None:
    parts = module.split(".")
    if parts[0] not in IMPORT_ROOTS:
        return None
    base = ROOT.joinpath(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _module_level(body: list[ast.stmt]):
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.ClassDef):
            yield from _module_level(node.body)
            continue
        yield node
        for field in ("body", "orelse", "finalbody"):
            yield from _module_level(getattr(node, field, None) or [])
        for handler in getattr(node, "handlers", None) or []:
            yield from _module_level(handler.body)


def _feature_parts(tree: ast.Module, reads: Callable[[str], bool]) -> list[str]:
    """The modules the feature declarations name under the hooks ``reads`` accepts."""

    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "PortalFeature"):
            continue
        for table in (argument for argument in node.args if isinstance(argument, ast.Dict)):
            for hook, part in zip(table.keys, table.values):
                if not (isinstance(hook, ast.Constant) and isinstance(part, ast.Constant) and isinstance(part.value, str)):
                    continue
                if reads(str(hook.value)):
                    found.append(part.value.partition(":")[0])
    return found


def _component_reads(component: str) -> Callable[[str], bool]:
    def reads(hook: str) -> bool:
        readers = next((readers for prefix, readers in FEATURE_HOOK_READERS if hook.startswith(prefix)), None)
        return readers is None or component in readers

    return reads


def _feature_instruction_files() -> frozenset[str]:
    """The features' instruction declarations (``isa.instructions``): every service's catalog reads them."""

    path = _module_file(FEATURE_DECLARATIONS)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8")) if path else None
    except (OSError, SyntaxError, UnicodeDecodeError):
        tree = None
    if tree is None:
        return frozenset()
    return frozenset(
        str(file.relative_to(ROOT))
        for module in _feature_parts(tree, lambda hook: hook == "isa.instructions")
        if (file := _module_file(module)) is not None
    )


def _module_imports(path: Path, module: str, *, in_functions: bool = False, component: str = "") -> tuple[str, ...]:
    """Modules this file imports when it is loaded, or also inside its functions."""

    stat = path.stat()
    key = (str(path), in_functions, component if module == FEATURE_DECLARATIONS else "")
    cached = _MODULE_IMPORTS.get(key)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2]
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        names: tuple[str, ...] = ()
    else:
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        found: list[str] = []
        for node in ast.walk(tree) if in_functions else _module_level(tree.body):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.split(".")[: len(package.split(".")) - node.level + 1]
                    prefix = ".".join([*base, *([node.module] if node.module else [])])
                else:
                    prefix = node.module or ""
                found.append(prefix)
                found.extend(f"{prefix}.{alias.name}" for alias in node.names)
        call = NAMED_MODULE_CALLS.get(module)
        if call:
            # A name may end in a class or function; the walk in service_imports
            # keeps its longest prefix that is a module.
            found.extend(
                argument.value.partition(":")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == call
                for argument in ast.walk(node)
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                and argument.value.partition(".")[0] in IMPORT_ROOTS
            )
        if module == FEATURE_DECLARATIONS:
            found.extend(_feature_parts(tree, _component_reads(component)))
        names = tuple(found)
    _MODULE_IMPORTS[key] = (stat.st_mtime_ns, stat.st_size, names)
    return names


def service_imports() -> dict[str, frozenset[str]]:
    """The repository files each service loads (Web: may load while it runs)."""

    result: dict[str, frozenset[str]] = {}
    for component, entries in SERVICE_ENTRY_MODULES.items():
        in_functions = component in FUNCTION_IMPORT_COMPONENTS
        not_loaded = NOT_LOADED.get(component, frozenset())
        files: dict[str, Path] = {}
        pending = list(entries)
        while pending:
            parts = pending.pop().split(".")
            for end in range(1, len(parts) + 1):
                module = ".".join(parts[:end])
                if module in files or module in not_loaded:
                    continue
                path = _module_file(module)
                if path is None:
                    continue
                files[module] = path
                pending.extend(_module_imports(path, module, in_functions=in_functions, component=component))
        result[component] = frozenset(
            str(path.relative_to(ROOT)) for path in files.values()
        )
    return result


# Shared modules only Portal Web imports. Every other management_common module
# (database access, identities, operation errors, artifacts) is imported by the
# background services too, and management_common is shared source for Release.
MANAGEMENT_COMMON_WEB_ONLY = frozenset({
    "management_common/knowledge_metadata.py",
    "management_common/portal_agent_http.py",
})


def components_for_paths(
    paths: set[str],
    imports: dict[str, frozenset[str]] | None = None,
) -> set[str]:
    components: set[str] = set()
    if imports is None:
        imports = service_imports()
    instruction_files = _feature_instruction_files()
    for path in paths:
        components.update(
            component for component, files in imports.items() if path in files
        )
        if path in {
            "management_portal/portal_access_control.py",
            "management_common/principal.py",
            "management_common/execution_context_file.py",
        } or path in instruction_files or path.startswith(("management_portal/hatg/", "management_portal/permission_system/")):
            # The HATG ISA kernel, catalog (with the features' instruction
            # declarations) and routing, and the permission system they ask,
            # run in every owning service.
            components.update(ALL_COMPONENTS)
            continue
        if path.startswith("management_portal/execution/"):
            # ExecutionService and ExecutionWorker import from the same Python
            # package.  Treat every package change as shared so a rolling Dev
            # deployment cannot leave either process running stale code.
            components.update({"execution-service", "execution-worker"})
            name = path.rsplit("/", 1)[-1]
            if name not in {
                "service.py",
                "scheduler.py",
                "worker.py",
                "worker_manager.py",
                "runtime.py",
                "backends.py",
                "binding.py",
                "account_registrations.py",
                "account_adapters.py",
                "session_manager.py",
            }:
                components.add("web")
            if name == "event_projection.py":
                components.add("lineage")
            if name.startswith("harness_"):
                # SocietyService normalizes persisted Agent Harness settings
                # with harness_settings, which imports these defaults.
                components.add("lineage")
            continue
        if path.startswith("management_portal/workbench_projections/") or path in {
            "management_portal/lineage_workflows.py",
            "management_portal/semantic_search_surfaces.py",
            "management_portal/application_targets.py",
            "management_portal/isa_client.py",
            "management_portal/paper_projection.py",
            "management_portal/wall_workflows.py",
            "management_portal/test_framework/workflows.py",
            # ResourceService indexes the Test Catalog with it (resource.index_adapters).
            "management_portal/test_framework/resource_index.py",
        }:
            components.update({"web", "resource"})
            continue
        if path.startswith("management_portal/resources/"):
            components.add("resource")
            if path.rsplit("/", 1)[-1] in {
                "__init__.py",
                "address.py",
                "contracts.py",
                "transport.py",
            }:
                components.add("web")
            continue
        if path.startswith("management_portal/society_service/"):
            components.add("lineage")
            if path.rsplit("/", 1)[-1] in {
                "__init__.py",
                "contracts.py",
                "transport.py",
            }:
                components.add("web")
            continue
        unit_components = {component for layout in INSTANCE_LAYOUT.values()
                           for component, unit in layout['component_units'].items()
                           if path == f'systemd/{unit}'}
        if unit_components:
            components.update(unit_components)
            continue
        if path in {
            "systemd/management-portal-routed.conf",
            "management_portal/management-portal.service",
        }:
            components.add("web")
            continue
        if path == "management_portal/ai_provider_registry.py":
            components.update(ALL_COMPONENTS)
            continue
        if path in {
            "management_portal/claude_code_client.py",
            "management_portal/codex_app_server_client.py",
            "management_portal/kimi_code_client.py",
        }:
            components.add("execution-worker")
            continue
        if path.startswith("systemd/"):
            components.update(ALL_COMPONENTS)
            continue
        if path.startswith("scripts/"):
            # Operational scripts are loaded on invocation, not by daemons.
            continue
        if path.startswith("management_common/") and path not in MANAGEMENT_COMMON_WEB_ONLY:
            # A change here once restarted only Web, so ResourceService,
            # SocietyService and ExecutionService/Worker kept the old module.
            components.update(ALL_COMPONENTS)
            continue
        if path.startswith("management_portal/") or path.startswith(
            (
                "knowledge_board_lan/",
                "management_common/",
                "project_board_lan/",
                "skill_board_lan/",
            )
        ):
            components.add("web")
            continue
        if path == "requirements-portal-runtime.txt":
            components.update(ALL_COMPONENTS)
    return components


def component_source_states(
    state: tuple[tuple[str, int, int], ...],
) -> dict[str, tuple[tuple[str, int, int], ...]]:
    """Project one source snapshot into independently deployable components."""

    projected: dict[str, list[tuple[str, int, int]]] = {
        component: [] for component in ALL_COMPONENTS
    }
    imports = service_imports()
    for item in state:
        path = item[0]
        for component in components_for_paths({path}, imports):
            projected[component].append(item)
    return {
        component: tuple(items)
        for component, items in projected.items()
    }


def read_deployed_source_states(
    current: dict[str, tuple[tuple[str, int, int], ...]],
    path: Path | None = None,
) -> dict[str, tuple[tuple[str, int, int], ...]]:
    path = DEPLOYED_SOURCE_STATE if path is None else path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(current)
    if (
        not isinstance(payload, dict)
        or not schema_matches(payload.get("schema"), "tfi.portal-deployed-component-source-state.v1")
        or not isinstance(payload.get("components"), dict)
    ):
        return dict(current)
    result: dict[str, tuple[tuple[str, int, int], ...]] = {}
    for component in ALL_COMPONENTS:
        raw = payload["components"].get(component)
        if not isinstance(raw, list):
            return dict(current)
        try:
            result[component] = tuple(
                (str(item[0]), int(item[1]), int(item[2]))
                for item in raw
            )
        except (IndexError, TypeError, ValueError):
            return dict(current)
    return result


def write_deployed_source_states(
    states: dict[str, tuple[tuple[str, int, int], ...]],
    path: Path | None = None,
) -> None:
    path = DEPLOYED_SOURCE_STATE if path is None else path
    atomic_replace_json(
        path,
        {
            "schema": DEPLOYED_SOURCE_STATE_SCHEMA,
            "components": {
                component: list(states.get(component, ()))
                for component in sorted(ALL_COMPONENTS)
            },
        },
    )


def changed_components(
    accepted: dict[str, tuple[tuple[str, int, int], ...]],
    current: dict[str, tuple[tuple[str, int, int], ...]],
) -> set[str]:
    return {
        component
        for component in ALL_COMPONENTS
        if accepted.get(component, ()) != current.get(component, ())
    }


def accept_components(
    accepted: dict[str, tuple[tuple[str, int, int], ...]],
    current: dict[str, tuple[tuple[str, int, int], ...]],
    components: set[str],
) -> dict[str, tuple[tuple[str, int, int], ...]]:
    updated = dict(accepted)
    for component in components:
        updated[component] = current.get(component, ())
    write_deployed_source_states(updated)
    return updated


def restart_dev(components: set[str], *, force_worker_restart: bool = False) -> str:
    if not components:
        return RESTART_OK
    entry = ([sys.executable, str(ROOT / "scripts" / "portal_services.py")]
             if sys.platform == "win32" else [str(ROOT / "scripts" / "portal_services")])
    command = [
        *entry,
        "restart",
        "dev",
        "--notice",
        "5",
    ]
    if not force_worker_restart:
        command.append("--generation-only")
    for component in sorted(components):
        command.extend(["--component", component])
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0:
        print(json.dumps({"ok": True, "event": "restarted", "components": sorted(components)}), flush=True)
        return RESTART_OK
    if result.stdout:
        print(result.stdout.rstrip(), flush=True)
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr, flush=True)
    return RESTART_FAILED


def _log_blocked(event: str, components: set[str], **details: object) -> None:
    print(
        json.dumps(
            {
                "ok": False,
                "event": event,
                "components": sorted(components),
                **details,
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
        flush=True,
    )


def process_restart_request(
    accepted: dict[str, tuple[tuple[str, int, int], ...]],
    failed_request_id: str,
) -> tuple[
    dict[str, tuple[tuple[str, int, int], ...]],
    str,
    str | None,
]:
    request = read_restart_request()
    request_id = str(request.get("request_id") or "").strip()
    revision = int(request.get("revision") or 0)
    request_token = f"{request_id}:{revision}" if request_id and revision else ""
    if not request_token or request_token == failed_request_id:
        return accepted, failed_request_id, None
    if restart_request_delay(request) > 0:
        return accepted, failed_request_id, None
    requested_components = {
        str(component)
        for component in request.get("components") or []
        if str(component)
    }
    accepted = read_deployed_source_states(accepted)
    source_before_restart = component_source_states(source_state())
    source_changes = changed_components(
        accepted,
        source_before_restart,
    )
    # An explicit component list is an operator command, not a source-change
    # filter.  It must remain capable of repairing a stale or partially
    # deployed component even when its projected source state is unchanged.
    # An empty list retains the automatic "deploy what changed" behaviour.
    components = requested_components | (source_changes if request.get('auto', not requested_components) else set())
    if not components:
        acknowledge_restart_request(request_id, revision)
        return accepted, "", RESTART_OK
    if "execution-worker" in requested_components:
        outcome = restart_dev(components, force_worker_restart=True)
    else:
        outcome = restart_dev(components)
    if outcome == RESTART_OK:
        acknowledge_restart_request(request_id, revision)
        return (
            accept_components(accepted, source_before_restart, components),
            "",
            outcome,
        )
    _log_blocked(
        "portal_dev_restart_request_blocked",
        components,
        request_id=request_id,
        reason="deployment verification failed; submit a new restart request after correction",
    )
    return accepted, request_token, outcome


def scan_source_fallback(
    accepted: dict[str, tuple[tuple[str, int, int], ...]],
    failed: tuple[tuple[str, int, int], ...] | None,
) -> tuple[
    dict[str, tuple[tuple[str, int, int], ...]],
    tuple[tuple[str, int, int], ...] | None,
]:
    accepted = read_deployed_source_states(accepted)
    current_source = source_state()
    current = component_source_states(current_source)
    components = changed_components(accepted, current)
    if not components:
        return accepted, None
    if failed is not None and current_source == failed:
        return accepted, failed
    outcome = restart_dev(components)
    if outcome == RESTART_OK:
        return accept_components(accepted, current, components), None
    _log_blocked(
        "portal_dev_source_reload_blocked",
        components,
        reason="deployment verification failed; waiting for a new source state",
    )
    return accepted, current_source


def watch(
    *,
    source_scan_interval_seconds: float = DEFAULT_SOURCE_SCAN_INTERVAL_SECONDS,
) -> None:
    # The request file is authoritative.  Unix signals only shorten the wait
    # on Linux; other service managers observe new requests by polling it.
    native_poll = sys.platform != 'linux'
    if not native_poll:
        signal.signal(signal.SIGUSR1, _wake_for_restart)
    notify_socket = os.environ.get('NOTIFY_SOCKET')
    if sys.platform == 'linux' and notify_socket:
        address = '\0' + notify_socket[1:] if notify_socket.startswith('@') else notify_socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.connect(address)
            notifier.sendall(b'READY=1')
    current = component_source_states(source_state())
    accepted = read_deployed_source_states(current)
    write_deployed_source_states(accepted)
    failed_source = None
    failed_request_id = ""
    next_source_scan = time.monotonic() + source_scan_interval_seconds
    next_request_poll = time.monotonic() + 1 if native_poll else float("inf")
    restart_due = float("inf")
    _WAKE_EVENT.set()
    while True:
        now = time.monotonic()
        awakened = _WAKE_EVENT.wait(
            max(0.0, min(next_source_scan, restart_due, next_request_poll) - now)
        )
        if awakened:
            _WAKE_EVENT.clear()
        now = time.monotonic()
        if native_poll and now >= next_request_poll:
            awakened = True
            next_request_poll = now + 1
        if awakened or now >= restart_due:
            accepted, failed_request_id, request_outcome = process_restart_request(
                accepted,
                failed_request_id,
            )
            pending = read_restart_request()
            pending_token = f"{pending.get('request_id')}:{pending.get('revision')}"
            restart_due = (time.monotonic() + restart_request_delay(pending)
                           if pending and pending_token != failed_request_id else float("inf"))
        now = time.monotonic()
        if now >= next_source_scan:
            accepted, failed_source = scan_source_fallback(
                accepted,
                failed_source,
            )
            next_source_scan = time.monotonic() + source_scan_interval_seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Coordinate explicit Portal Dev restart requests and perform an hourly "
            "source-change fallback scan."
        )
    )
    parser.add_argument(
        "--source-scan-interval",
        type=float,
        default=DEFAULT_SOURCE_SCAN_INTERVAL_SECONDS,
    )
    args = parser.parse_args(argv)
    watch(
        source_scan_interval_seconds=max(1.0, args.source_scan_interval),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
