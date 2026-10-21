import os
import json
import secrets
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest


# pytest never uses the Portal database server, even when the shell loaded the
# Portal environment: it takes an explicit separate test server, or starts a
# disposable one for this session. Child processes inherit the same choice.
from tests.cases.postgres_test_database import (
    TEMP_PREFIX,
    choose_test_database,
    mark_owner,
    sweep_abandoned_roots,
)

_PYTEST_DATABASE_URL = choose_test_database(dict(os.environ))
os.environ["PORTAL_DATABASE_URL"] = _PYTEST_DATABASE_URL
os.environ["PORTAL_POSTGRES_TEST_DSN"] = _PYTEST_DATABASE_URL

# Native-client tests use installed programs against a local fake server.
# Capture only program paths before the disposable site replaces deployment
# settings; accounts, credentials and all other site values stay isolated.
from management_common.portal_paths import path as _portal_path, resolve_directory
from management_common.site_config import read_site_config

_PROVIDER_PATH_SETTINGS = read_site_config().get("deployment", {}).get("providers", {})
_INSTALLED_PROVIDER_RUNTIME_ROOT = resolve_directory(_PROVIDER_PATH_SETTINGS.get(
    "runtime_root", str(_portal_path("staging_root") / "provider-runtimes")))
_INSTALLED_CODEX_BINARY = (_PROVIDER_PATH_SETTINGS.get("codex_binary") or shutil.which("codex"))
_INSTALLED_CODEX_BINARY = resolve_directory(_INSTALLED_CODEX_BINARY) if _INSTALLED_CODEX_BINARY else None
del _PROVIDER_PATH_SETTINGS


@pytest.fixture(scope="session")
def installed_provider_paths():
    return {"runtime_root": _INSTALLED_PROVIDER_RUNTIME_ROOT, "codex_binary": _INSTALLED_CODEX_BINARY}

_PYTEST_DATABASE_NAMESPACE = (
    f"pytest_{os.getpid()}_{secrets.token_hex(4)}"
)
os.environ["PORTAL_DATABASE_NAMESPACE"] = _PYTEST_DATABASE_NAMESPACE

# Runtime credentials belong to the invoking Resident, never to isolated
# tests. Authorization transport cases install their own explicit files.
for environment_key in ("PORTAL_EXECUTION_CONTEXT_FILE", "PORTAL_AUTHORIZATION_CONTEXT_FILE"):
    os.environ.pop(environment_key, None)


# Directories left by sessions that were killed are removed first.
sweep_abandoned_roots(Path(tempfile.gettempdir()))
_PYTEST_DATABASE_TEMP_DIR = tempfile.TemporaryDirectory(prefix=TEMP_PREFIX)
_PYTEST_DATABASE_ROOT = Path(_PYTEST_DATABASE_TEMP_DIR.name)
mark_owner(_PYTEST_DATABASE_ROOT)
# Database selection above checks the real deployment before isolation. All
# subsequent domain configuration belongs to this disposable test site.
_PYTEST_SITE_CONFIG = _PYTEST_DATABASE_ROOT / "site.json"
from tests.cases.postgres_test_database import _binary, _postgres_environment
_PYTEST_SITE_CONFIG.write_text(json.dumps({"deployment": {"postgres": {
    "bin": str(_binary("pg_dump").parent),
    "lib": _postgres_environment()["LD_LIBRARY_PATH"].split(os.pathsep)[0],
}}}), encoding="utf-8")
os.environ["PORTAL_SITE_CONFIG"] = str(_PYTEST_SITE_CONFIG)
# Bootstrap this disposable site's rule files from the source distribution.
# Prompt materialization exercises real Skills without reading deployed data.
os.environ["PORTAL_PATHS_CONFIG"] = str(_PYTEST_DATABASE_ROOT / "paths.json")
# Set every directory, including paths that providers and subprocesses read.
# portal_paths was imported above to capture installed binaries; update its
# cached config path as well as the environment before any domain imports.
from management_common import portal_paths as _isolated_paths
_isolated_paths.CONFIG_PATH = Path(os.environ["PORTAL_PATHS_CONFIG"])
_isolated_values = {key: str(_PYTEST_DATABASE_ROOT / key) for key in _isolated_paths.KEYS}
_isolated_values['share_root'] = str(_PYTEST_DATABASE_ROOT / 'share')
for _key, _variable in _isolated_paths.ENV_KEYS.items():
    os.environ[_variable] = _isolated_values[_key]
_isolated_paths.write_config(_isolated_values)
shutil.copytree(
    Path(__file__).resolve().parents[2] / "core_skills",
    _PYTEST_DATABASE_ROOT / "share" / "Skills",
)
for environment_key, relative_path in {
    # Index workers created by unrelated cases must not walk the live Paper
    # archive, including after a case restores its local PaperStore patch.
    "PAPER_STORE_ROOT": "papers",
    "PAPER_WORKSPACE_ROOT": "paper-workspace",
    "PORTAL_CODEX_APP_SERVER_THREAD_LOCK_ROOT": "codex-thread-locks",
    "PORTAL_LINEAGE_ROOT": "lineages",
    "PORTAL_TWO_FACTOR_KEY_PATH": "two-factor.key",
    "PORTAL_EXECUTION_STATE_ROOT": "execution-state",
    "PORTAL_EFFECT_ARTIFACT_ROOT": "effect-artifacts",
    "PORTAL_TEST_FRAMEWORK_STATE_ROOT": "test-framework",
    # Test executors started by cases never take the host's test slots.
    "PORTAL_TEST_SLOT_ROOT": "test-slots",
    "PORTAL_ARTICLE_BUILD_ROOT": "article-builds",
    # Lineage folders, Resident bearer files and avatars of test users,
    # Residents, Lineages and Projects stay out of /share/Agents and ~/.archive.
    "PORTAL_LINEAGE_FOLDER_ROOT": "lineage-folders",
    "PORTAL_RESIDENT_HATG_TOKEN_ROOT": "resident-hatg-tokens",
    "PORTAL_RESIDENT_AVATAR_ROOT": "resident-avatars",
    "PORTAL_USER_AVATAR_ROOT": "user-avatars",
    "PORTAL_SCOPE_AVATAR_ROOT": "scope-avatars",
    # A case never reaches a deployed service: every owner socket it could
    # inherit from a Worker's environment points into the case root instead.
    "PORTAL_SOCIETY_SERVICE_SOCKET": "sockets/lineage.sock",
    "PORTAL_RESOURCE_SERVICE_SOCKET": "sockets/resource.sock",
    "PORTAL_EXECUTION_SERVICE_SOCKET": "sockets/execution.sock",
    "PORTAL_WEB_SERVICE_SOCKET": "sockets/web.sock",
}.items():
    os.environ[environment_key] = str(_PYTEST_DATABASE_ROOT / relative_path)
# File-watcher cases create their own bounded temporary watches.
os.environ["PORTAL_RESOURCE_INDEX_WATCH"] = "0"
# Domain tests use the in-process notification hub, not a deployed NATS
# server. Transport cases explicitly enable or replace their own client.
os.environ["PORTAL_NATS_ENABLED"] = "0"
# Cases run ResourceService and every HATG owner in process over the
# namespaced test database, even when started from a deployed Worker.
os.environ["PORTAL_RESOURCE_SERVICE_MODE"] = "local"
os.environ["PORTAL_ISA_ROUTING"] = "local"

from management_portal.society_service.application import (
    configure_default_domain_ports,
)
from management_portal.execution.agent_data_store import (
    reset_agent_data_store_for_tests,
)
from management_portal.society_service.state_store import (
    reset_lineage_data_store_for_tests,
)
from management_portal.society_service.resident_conversation_stream import (
    reset_repositories_for_tests,
)


configure_default_domain_ports()

# An ExecutionService client also tries its peer instance's socket (Dev falls
# back to Release).  Cases resolve every instance's ExecutionService socket in
# the case root, so that fallback never reaches a deployed service.
from management_portal.execution import config as _execution_config  # noqa: E402

_execution_config.DEFAULT_INSTANCE_ROOT = _PYTEST_DATABASE_ROOT / "instances"


def _reset_process_stores() -> None:
    from management_portal import resource_index_backend
    from management_portal.notifications import close_notifications

    close_notifications()

    with resource_index_backend._SERVICE_LOCK:
        resource_index = resource_index_backend._SERVICE
        resource_index_backend._SERVICE = None
    if resource_index is not None:
        resource_index.adapter_layer.stop()
    reset_agent_data_store_for_tests()
    reset_lineage_data_store_for_tests()
    from management_portal.resources.registry_store import reset_registry_store_for_tests

    reset_registry_store_for_tests()
    reset_repositories_for_tests()
    from management_portal.execution.harness_evolution import PolicyCellCache

    PolicyCellCache.reset_for_tests()
    # The permission system caches each principal's entries by revision; a
    # truncated instance starts its revisions again, so the cache goes too.
    from management_portal import permission_system

    permission_system.forget_implementation()
    permission_system._MODE = None
    from management_common.postgres_database import close_all_database_pools

    close_all_database_pools()


def _truncate_test_schemas() -> None:
    """Reset test data without changing the production database route."""

    _reset_process_stores()
    import psycopg
    from psycopg import sql

    prefix = f"{_PYTEST_DATABASE_NAMESPACE}_"
    contract_tables = {
        "agent_store_meta",
        "execution_store_meta",
        "lineage_meta",
        "projection_meta",
        "resource_index_meta",
        "registry_meta",
        "state_schema",
    }
    for attempt in range(5):
        try:
            # A case may remove/change the route while checking configuration.
            # Cleanup always targets the isolated server selected at startup.
            with psycopg.connect(_PYTEST_DATABASE_URL, autocommit=True) as connection:
                all_tables = list(connection.execute(
                    "SELECT schemaname, tablename FROM pg_tables "
                    "WHERE schemaname LIKE %s ORDER BY schemaname, tablename",
                    (f"{prefix}%",),
                ))
                tables = [row for row in all_tables if str(row[1]) not in contract_tables]
                # lineage_meta keeps the schema version between cases; the
                # Lineage timer lease stored beside it belongs to one case.
                for schema, table in all_tables:
                    if str(table) == "lineage_meta":
                        connection.execute(
                            sql.SQL("DELETE FROM {} WHERE key <> 'schema'").format(
                                sql.Identifier(str(schema), str(table))
                            )
                        )
                if tables:
                    relation_list = sql.SQL(", ").join(
                        sql.Identifier(str(schema), str(table))
                        for schema, table in tables
                    )
                    connection.execute(
                        sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                            relation_list
                        )
                    )
            break
        except psycopg.errors.DeadlockDetected:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))
    _reset_process_stores()


# The Lineage domain ports forward every attribute they do not hold to their
# bound implementation.  monkeypatch.setattr on a port restores the original
# by writing it onto the port, which then never forwards that name again: after
# test_session_restart patched execution_port.health and friends, later tests
# that patched execution_client were bypassed and failed only in that order.
_DOMAIN_PORT_FIELDS = frozenset({"_name", "_implementation", "_lock", "_bound", "_handler"})


def _unpin_domain_ports() -> None:
    from management_portal.society_service import domain_ports

    for value in list(vars(domain_ports).values()):
        if isinstance(value, type) or type(value).__module__ != domain_ports.__name__:
            continue
        for port in (value, vars(value).get("_bound")):
            if port is None:
                continue
            for name in [name for name in vars(port) if name not in _DOMAIN_PORT_FIELDS]:
                del vars(port)[name]


_CODEX_RUNTIME_CATALOG = Path(__file__).with_name("fixtures") / "codex_runtime_catalog.json"


@pytest.fixture(autouse=True)
def recorded_codex_runtime_catalog(monkeypatch):
    """A case without its own ExecutionService reads a recorded Codex catalog.

    Cases used to get the model catalog from the deployed Release service
    through the client's peer fallback.  A case that runs an ExecutionService
    in its root still gets that service's catalog, and a case that needs
    another catalog replaces this one with its own fake.
    """
    import copy
    import json

    from management_portal.execution.service import ExecutionService
    from management_portal.execution.transport import ExecutionServiceUnavailable

    recorded = json.loads(_CODEX_RUNTIME_CATALOG.read_text(encoding="utf-8"))
    original = ExecutionService.runtime_catalog

    def runtime_catalog(self, account_id="", *, backend="", include_backends=False):
        try:
            return original(self, account_id, backend=backend, include_backends=include_backends)
        except ExecutionServiceUnavailable:
            if backend in {"", "codex"} and not include_backends:
                return copy.deepcopy(recorded)
            raise

    monkeypatch.setattr(ExecutionService, "runtime_catalog", runtime_catalog)


def pytest_configure(config):
    config.addinivalue_line("markers", "service_restart_wait: the case tests how a client waits for a restarting service")
    config.addinivalue_line("markers", "empty_execution_registrations: the case owns its execution registration data")


@pytest.fixture(autouse=True)
def absent_services_fail_at_once(isolate_portal_databases, request, monkeypatch):
    """A case's Portal services are not restarting, so a client does not wait for them.

    Clients wait up to ``SERVICE_RESTART_WAIT_SECONDS`` for a service socket
    that is absent or refuses, as a publication restarts it.  A case runs no
    ExecutionService or SocietyService of its own, so every call to one
    stalled that long before its fallback (a Family case took minutes).
    Cases of the wait itself are marked ``service_restart_wait``.
    """

    del isolate_portal_databases  # set up first, so it is torn down after monkeypatch
    if request.node.get_closest_marker("service_restart_wait"):
        return
    from management_common import service_socket
    from management_portal.execution import transport as execution_transport
    from management_portal.hatg import socket_rpc
    from management_portal.society_service import transport as lineage_transport
    from management_portal.resources import transport as resource_transport

    original = service_socket.connect_service

    def connect_at_once(paths, *, timeout, restart_wait=0.0):
        return original(paths, timeout=timeout, restart_wait=0.0)

    for module in (execution_transport, lineage_transport, resource_transport, socket_rpc):
        monkeypatch.setattr(module, "connect_service", connect_at_once)


@pytest.fixture(autouse=True)
def domain_ports_forward_again():
    _unpin_domain_ports()
    yield
    _unpin_domain_ports()


def _register_test_site(request, monkeypatch, root):
    # Store/adapter contracts exercise first registration, import and empty
    # sites. Keep their existing empty-store boundary without editing cases.
    if (request.path.stem in {"test_account_registrations", "test_execution_accounts"}
            or request.node.get_closest_marker("empty_execution_registrations")):
        return None
    from tests.cases.account_registration_test_support import register_site_defaults

    return register_site_defaults(
        monkeypatch, root,
        legacy_codex_sockets="registered_execution_defaults" in request.fixturenames,
    )


@pytest.fixture(autouse=True)
def isolate_portal_databases(request, monkeypatch, tmp_path):
    _truncate_test_schemas()
    try:
        yield _register_test_site(request, monkeypatch, tmp_path)
    finally:
        _truncate_test_schemas()


@pytest.fixture(autouse=True)
def permission_system_off(isolate_portal_databases, monkeypatch: pytest.MonkeyPatch):
    """Cases run with the permission system switched off unless they ask for it.

    A case of permissions turns it on with ``permission_test_support.permissions``
    (the default scheme on an instance the case builds).
    """

    del isolate_portal_databases  # set up first, so it is torn down after monkeypatch
    from management_portal import permission_system

    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)


@pytest.fixture
def registered_execution_defaults(isolate_portal_databases):
    """Compatibility name for the site's already registered default routes."""
    return isolate_portal_databases


@pytest.fixture(autouse=True)
def isa_result_contract(isolate_portal_databases, monkeypatch: pytest.MonkeyPatch):
    """With ISA_RESULT_CONTRACT=1, fail a test whose instruction handler
    returned a result without a field its declaration marks as required."""

    del isolate_portal_databases  # set up first, so it is torn down after monkeypatch
    if os.getenv("ISA_RESULT_CONTRACT") != "1":
        yield
        return
    from management_portal.hatg import kernel
    from management_portal.hatg.catalog import instruction_catalog

    violations: list[str] = []
    original = kernel.OwnerBinding.handler

    def handler(self, name):
        inner = original(self, name)
        required = [field.name for field in instruction_catalog()[name].result if field.required]

        def checked(invocation):
            result = inner(invocation)
            if isinstance(result, dict):
                missing = [field for field in required if field not in result]
                if missing:
                    violations.append(f"{name} result lacks {missing}")
            return result

        return checked

    monkeypatch.setattr(kernel.OwnerBinding, "handler", handler)
    yield
    assert not violations, "; ".join(sorted(set(violations)))


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Release process-scoped stores before removing their temporary root."""

    del session, exitstatus
    _reset_process_stores()
    import psycopg
    from psycopg import sql

    with psycopg.connect(_PYTEST_DATABASE_URL, autocommit=True) as connection:
        schemas = [
            str(row[0])
            for row in connection.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname LIKE %s",
                (f"{_PYTEST_DATABASE_NAMESPACE}_%",),
            )
            if str(row[0]).startswith(f"{_PYTEST_DATABASE_NAMESPACE}_")
        ]
        for schema in schemas:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
    _PYTEST_DATABASE_TEMP_DIR.cleanup()


@pytest.fixture
def isolated_portal_case(tmp_path: Path, request, isolate_portal_databases):
    case_index = 0

    @contextmanager
    def isolate():
        nonlocal case_index
        case_index += 1
        case_path = tmp_path / f"c{case_index}"
        case_path.mkdir()
        _truncate_test_schemas()
        try:
            with pytest.MonkeyPatch.context() as site_patch:
                _register_test_site(request, site_patch, case_path)
                yield case_path
        finally:
            _truncate_test_schemas()

    return isolate
