""":importance: P2"""
from __future__ import annotations


import concurrent.futures
import multiprocessing
import socket
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from management_portal.society_service.contracts import DOMAIN_OPERATIONS
from management_portal.society_service import changes as lineage_changes
from management_portal.society_service import server as lineage_server
from management_portal.society_service import transport as lineage_transport
from management_portal.hatg.contracts import InstructionError
from management_portal.society_service.server import SocietyServiceServer
from management_portal.society_service.transport import (
    LineageConflictError,
    SocietyServiceClient,
    SocietyServiceUnavailable,
)
from management_portal.resources.errors import ResourceServiceUnavailable


class _Publisher:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict]] = []

    def publish(self, subject: str, payload: dict) -> None:
        self.items.append((subject, payload))

    @staticmethod
    def health() -> dict:
        return {"enabled": False}


def test_read_instructions_use_process_pool_without_routing_mutations_or_streams(monkeypatch):
    """只有非流式读指令进入进程池，写、Watch、未知或格式错误的指令留在主进程处理"""
    from management_portal.hatg import socket_rpc
    server = object.__new__(SocietyServiceServer)
    routed, inline, responses = [], [], []
    server.read_pool = SimpleNamespace(workers=2, call_instruction=lambda *args: routed.append(args) or {"id": args[1], "ok": True})
    server._finish_changes = lambda changes: None
    monkeypatch.setattr(socket_rpc, "serve", lambda *args, **kwargs: inline.append((args, kwargs)))
    params = {"call": {"instruction": "GetLineage", "arguments": {"lineage_id": "lineage_test"}},
              "context": {"caller": "CASE-A", "entry": "web"}}
    server.serve_instruction(params, "read-1", responses.append, verified_caller="CASE-A")
    assert routed == [(params, "read-1", "CASE-A")]
    assert responses == [{"id": "read-1", "ok": True}]
    for name in ("CancelInput", "DeleteResident", "WatchLineage", "UnknownInstruction"):
        server.serve_instruction({**params, "call": {"instruction": name}}, name, responses.append, verified_caller="CASE-A")
    for malformed in (None, {}, {"recovery_release": {}, **params}):
        server.serve_instruction(malformed, "bad", responses.append, verified_caller="CASE-A")
    assert len(routed) == 1
    assert len(inline) == 7


def test_read_instruction_worker_rechecks_verified_caller_and_rejects_mutations():
    """读指令 worker 复核已验证的调用者，并拒绝写指令"""
    params = {"call": {"instruction": "GetLineage", "arguments": {"lineage_id": "lineage_test"}},
              "context": {"caller": "CASE-A", "entry": "web"}}
    response = lineage_server._read_instruction_response(params, "denied", "different-caller")
    assert response["ok"] is False
    assert response["error"]["code"] == "instruction.forbidden"
    with pytest.raises(ValueError, match="non-streaming read"):
        lineage_server._read_instruction_response({**params, "call": {"instruction": "DeleteResident"}}, "write", "CASE-A")


def _initialize_instruction_contract_worker():
    from management_portal.hatg import socket_rpc
    import os
    def serve(params, request_id, write_line, *, owner, verified_caller):
        write_line({"id": request_id, "ok": True, "pid": os.getpid(), "caller": verified_caller,
                    "call": params.get("call")})
    socket_rpc.serve = serve


def test_read_instruction_pool_executes_in_child_and_preserves_caller(monkeypatch):
    """读指令在子进程中执行并保留已验证的调用者与调用内容"""
    import os
    monkeypatch.setattr(lineage_server.LineageReadProcessPool, "_new_executor", lambda self:
        concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"),
                                               initializer=_initialize_instruction_contract_worker))
    pool = lineage_server.LineageReadProcessPool(1)
    try:
        params = {"call": {"instruction": "GetLineage"}}
        result = pool.call_instruction(params, "request", "CASE-A")
        assert result["pid"] != os.getpid()
        assert result["caller"] == "CASE-A"
        assert result["call"] == params["call"]
    finally:
        pool.close()


def test_read_worker_startup_does_not_open_database_pools(monkeypatch):
    """读进程池 worker 启动时不打开数据库连接"""
    import os
    import psycopg
    from management_common.postgres_database import PostgresDatabase

    connect = psycopg.connect

    def unexpected(*args, **kwargs):
        raise AssertionError("idle projection startup must not open a database")

    # The startup prohibition ends before the registration fixture cleans its
    # isolated database; teardown is not an idle worker opening a connection.
    with monkeypatch.context() as startup:
        startup.setattr(psycopg, "connect", unexpected)
        startup.setattr(PostgresDatabase, "_connection_pool", unexpected)
        startup.setenv("PORTAL_LINEAGE_READ_WORKER", "0")
        lineage_server._initialize_read_worker()
        # Spawn overlapping generations: patching the parent cannot hide a child
        # opening its own connections during initialization.
        pools = []
        try:
            for _ in range(2):
                pools.append(lineage_server.LineageReadProcessPool(1))
            pids = [str(pid) for pool in pools for pid in pool.health()["worker_pids"]]
            assert len(pids) == 2
            with connect(os.environ["PORTAL_DATABASE_URL"], autocommit=True) as db:
                count = db.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE split_part(application_name, ':', 4) = ANY(%s)", (pids,),
                ).fetchone()[0]
            assert count == 0
        finally:
            for pool in pools:
                pool.close()


class _ResidentDomain:
    @staticmethod
    def resident_aggregate_lineage_id(_resident_id: str) -> str:
        return "lineage_123456789abc"

    @staticmethod
    def cancel_resident_service_input(resident_id: str, actor: str, message_id: str):
        lineage_changes.resident_changed(resident_id, scope="identity")
        lineage_changes.resident_changed(resident_id, scope="identity")
        return {"resident_id": resident_id, "actor": actor, "message_id": message_id}

    @staticmethod
    def get_resident_session_projection(resident_id: str, actor: str):
        return {"resident_id": resident_id, "actor": actor}

    @staticmethod
    def list_resident_conversation_activity(actor: str, resident_ids=None, *, page_size=None, page_token=""):
        return {"actor": actor, "resident_ids": resident_ids, "page_size": page_size}

    @staticmethod
    def get_resident_resource_projection(resident_id: str):
        return {"resource_type": "resident", "resource_id": resident_id}

    @staticmethod
    def resume_pending_resident_executions():
        return {"main": 0, "fork": 0, "active": 0}

    @staticmethod
    def resume_resident_execution(_resident_id):
        return {"main": 0, "fork": 0, "active": 0}

    @staticmethod
    def ready_resident_lifecycle_operations():
        return []

    @staticmethod
    def resident_lifecycle_health():
        return {"pending": 0, "last_error": ""}


class _LineageDomain:
    scopes: list[tuple[str, str]] = []

    @staticmethod
    def reconcile_lineage_system_events_as_service(_lineage_id):
        return {}

    @staticmethod
    @contextmanager
    def lineage_state_scope(lineage_id: str, operation: str = ""):
        _LineageDomain.scopes.append((lineage_id, operation))
        yield

    @staticmethod
    def list_lineages(_actor: str):
        return []

    @staticmethod
    def get_lineage_resource_projection(lineage_id: str):
        return {"resource_type": "lineage", "resource_id": lineage_id}

    @staticmethod
    def authorize_lineage_resident_principal(
        lineage_id: str,
        resident_id: str,
    ):
        raise PermissionError("resident membership denied")


class _WorkspaceDomain:
    @staticmethod
    def get_workspace_link_file(path: str, actor: str):
        return {"path": path, "actor": actor}


class _ProjectDomain:
    @staticmethod
    def validate_project_deletion(project_id: str, actor: str):
        return {"project_id": project_id, "actor": actor}


class _ProjectionDomain:
    pass


class _RuntimePort:
    @staticmethod
    def poll_all():
        return {"checked": 0}

    @staticmethod
    def consume_terminal(_fact):
        return {}


def _install_domains(monkeypatch, domains):
    monkeypatch.setattr(lineage_server, "_domain_module", domains.__getitem__)
    implementations = {
        operation.implementation: domains[domain]
        for domain, operations in DOMAIN_OPERATIONS.items() if domain in domains
        for operation in operations.values()
    }
    monkeypatch.setattr(lineage_server, "import_module", implementations.__getitem__)


@pytest.fixture()
def lineage_process(tmp_path, monkeypatch, capfd):
    _LineageDomain.scopes = []
    domains = {
        "resident": _ResidentDomain,
        "workspace": _WorkspaceDomain,
        "project": _ProjectDomain,
        "lineage": _LineageDomain,
        "projection": _ProjectionDomain,
    }
    _install_domains(monkeypatch, domains)
    monkeypatch.setattr(lineage_server, "_validate_domain_handlers", lambda: None)
    runtime = _RuntimePort()
    monkeypatch.setattr(
        lineage_server,
        "build_backend_runtime_ports",
        lambda: SimpleNamespace(
            events=runtime,
            resident_terminals=runtime,
            reconciliation=runtime,
            system_events=runtime,
        ),
    )
    publisher = _Publisher()
    socket_path = tmp_path / "society-service.sock"
    server = SocietyServiceServer(socket_path, publisher=publisher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = SocietyServiceClient(socket_path, timeout=2)
    try:
        yield client, publisher
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert "coordinator failed" not in capfd.readouterr().err, (
            "transport fixture must not hide background coordinator failures"
        )


def test_committed_execution_work_wakes_coordinator_across_client_boundary(
    lineage_process, monkeypatch,
):
    """Web 进程提交 Resident 执行工作后经客户端立即唤醒 SocietyService 协调器"""
    import management_portal.society_service as society_service
    from management_portal.society_service import lifecycle

    client, _publisher = lineage_process
    resident_id = "resident_abcdef123456"
    resumed = threading.Event()
    monkeypatch.setattr(
        _ResidentDomain,
        "resume_resident_execution",
        staticmethod(lambda target: resumed.set() if target == resident_id else None),
    )
    monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_MODE", "client")
    monkeypatch.setattr(society_service, "_CLIENT", client)
    # The Portal Web process has no in-process coordinator callback.
    monkeypatch.setattr(lifecycle, "_WAKE", None)

    lifecycle.notify_execution_binding(resident_id)

    assert resumed.wait(1), "a committed job must not wait for periodic recovery"


def test_society_service_routes_reads_and_mutations(lineage_process):
    """SocietyService 读操作不发布变更，取消服务输入后发布 Resident 身份变更"""
    client, publisher = lineage_process

    assert publisher.items == []

    assert client.workspace.get_workspace_link_file("/share/example", "CASE-A") == {
        "path": "/share/example",
        "actor": "CASE-A",
    }
    assert client.project.validate_project_deletion("example", "CASE-A") == {"project_id": "example", "actor": "CASE-A"}

    assert client.resident.get_resident_session_projection(
        "resident_abcdef123456", "CASE-A"
    ) == {
        "resident_id": "resident_abcdef123456",
        "actor": "CASE-A",
    }
    assert client.resident.get_resident_resource_projection(
        "resident_abcdef123456"
    ) == {
        "resource_type": "resident",
        "resource_id": "resident_abcdef123456",
    }
    assert client.lineage.get_lineage_resource_projection(
        "lineage_abcdef123456"
    ) == {
        "resource_type": "lineage",
        "resource_id": "lineage_abcdef123456",
    }

    result = client.resident.cancel_resident_service_input(
        "resident_123456789abc", "CASE-A", "msg_service"
    )

    assert result["message_id"] == "msg_service"
    assert publisher.items == [
        (
            "portal.resident.changed.resident_123456789abc.identity",
            {"kind": "changed"},
        )
    ]


def test_society_service_process_pool_routes_only_reads_contracts(monkeypatch, subtests, isolated_portal_case):
    """读操作进读进程池、收件活动走专用池、写操作留在主进程，读池报错不重启 worker"""
    with subtests.test(contract='test_society_service_process_pool_routes_only_reads'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            domains = {
                "resident": _ResidentDomain,
                "lineage": _LineageDomain,
                "projection": _ProjectionDomain,
            }
            _install_domains(monkeypatch, domains)

            routed: list[dict] = []
            server = object.__new__(SocietyServiceServer)
            server.publisher = _Publisher()
            server.projections_enabled = False
            server.read_pool = SimpleNamespace(
                workers=4,
                call=lambda params: routed.append(params) or {"worker": True},
            )
            activity_routed = []
            server.conversation_index_pool = SimpleNamespace(
                call=lambda params: activity_routed.append(params) or {"inbox_worker": True},
            )

            read = {
                "domain": "resident",
                "name": "get_resident_session_projection",
                "args": ["resident_abcdef123456", "CASE-A"],
                "kwargs": {},
            }
            assert server._domain_call(read) == {"worker": True}
            assert routed == [read]

            activity = {"domain": "resident", "name": "list_resident_conversation_activity",
                        "args": ["CASE-A"], "kwargs": {"page_size": 50}}
            assert server._domain_call(activity) == {"inbox_worker": True}
            assert activity_routed == [activity]
            assert routed == [read]

            targeted = {"domain": "resident", "name": "list_resident_conversation_activity",
                        "args": ["CASE-A", ["resident_abcdef123456"]], "kwargs": {}}
            assert server._domain_call(targeted) == {"worker": True}
            assert routed[-1] == targeted
            assert len(activity_routed) == 1

            assert server._domain_call(
                {
                    "domain": "resident",
                    "name": "cancel_resident_service_input",
                    "args": ["resident_123456789abc", "CASE-A", "msg_service"],
                    "kwargs": {},
                }
            )["message_id"] == "msg_service"
            assert routed == [read, targeted]
    with subtests.test(contract='test_uncollected_background_change_notifies_local_projection'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            calls: list[tuple[str, str, str]] = []
            publisher = _Publisher()
            monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_PROCESS", "1")
            monkeypatch.setattr(
                lineage_changes,
                "get_notification_publisher",
                lambda: publisher,
            )
            lineage_changes.set_local_change_handler(
                lambda domain, resource_id, scope: calls.append(
                    (domain, resource_id, scope)
                )
            )
            try:
                lineage_changes.resident_changed(
                    "resident_123456789abc",
                    scope="runtime",
                )
            finally:
                lineage_changes.set_local_change_handler(None)

            assert calls == [
                ("resident", "resident_123456789abc", "runtime")
            ]
            assert publisher.items == [
                (
                    "portal.resident.changed.resident_123456789abc.runtime",
                    {"kind": "changed"},
                )
            ]
    with subtests.test(contract='test_read_pool_preserves_instruction_error_without_restarting_worker'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(
                lineage_server.LineageReadProcessPool, "_new_executor",
                lambda self: concurrent.futures.ProcessPoolExecutor(
                    max_workers=1,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=_initialize_contract_read_worker,
                ),
            )
            pool = lineage_server.LineageReadProcessPool(1)
            params = {"domain": "workspace", "name": "get_workspace_link_file", "args": ["/denied"], "kwargs": {}}
            try:
                before = pool.health()["worker_pids"]
                with pytest.raises(InstructionError) as caught:
                    pool.call(params)
                assert caught.value.code == "instruction.capability_denied"
                assert caught.value.details == {"capability": "hatg:read"}
                assert caught.value.http_status == 403
                assert pool.call({**params, "args": ["/allowed"]}) == {"path": "/allowed", "actor": "CASE-A"}
                assert pool.health()["worker_pids"] == before
            finally:
                pool.close()


def test_resident_calls_bind_their_owning_lineage_scope(lineage_process):
    """Resident 领域调用绑定到该 Resident 所属 Lineage 的作用域"""
    client, _publisher = lineage_process

    client.resident.get_resident_session_projection(
        "resident_abcdef123456",
        "CASE-A",
    )

    assert _LineageDomain.scopes[-1] == (
        "lineage_123456789abc",
        "resident.get_resident_session_projection",
    )


def test_society_service_preserves_remote_permission_error(lineage_process):
    """SocietyService 远端抛出的 PermissionError 经客户端原样传回"""
    client, _publisher = lineage_process

    with pytest.raises(PermissionError, match="membership denied"):
        client.lineage.authorize_lineage_resident_principal(
            "lineage_123456789abc",
            "resident_123456789abc",
        )


def test_lineage_dependency_outage_is_a_typed_service_unavailability(lineage_process, monkeypatch):
    """读取 Resident 会话投影时 ResourceService 断连，客户端报 SocietyService 不可用"""
    client, _publisher = lineage_process

    def unavailable(resident_id, actor):
        raise ResourceServiceUnavailable("ResourceService connection refused during rollout")

    monkeypatch.setattr(
        _ResidentDomain, "get_resident_session_projection", staticmethod(unavailable),
    )
    with pytest.raises(SocietyServiceUnavailable, match="ResourceService connection refused"):
        client.resident.get_resident_session_projection("resident_123456789abc", "CASE-A")


def test_society_service_rejects_unowned_methods(lineage_process):
    """SocietyService 客户端拒绝调用领域未公开的内部方法"""
    client, _publisher = lineage_process

    with pytest.raises(AttributeError):
        client.resident._resident_roots()


def test_lineage_change_notifies_coordinator_without_ui_projection_contracts(subtests, isolated_portal_case):
    """Lineage 变更只通知协调器，Resident 变更发布活跃列表投影和身份变更通知"""
    with subtests.test(contract='test_lineage_change_notifies_coordinator_without_ui_projection'), isolated_portal_case() as case_path:
        server = object.__new__(SocietyServiceServer)
        server.projections_enabled = True
        calls: list[str] = []
        server.coordinator = SimpleNamespace(
            notify_lineage=lambda lineage_id: calls.append(f"notify:{lineage_id}")
        )
        server.publisher = _Publisher()

        server._finish_changes([
            ("lineage", "lineage_123456789abc", "identity", {}),
        ])

        assert calls == ["notify:lineage_123456789abc"]
    with subtests.test(contract='test_resident_change_notifies_active_view_to_reload'), isolated_portal_case() as case_path:
        server = object.__new__(SocietyServiceServer)
        server.projections_enabled = True
        server.coordinator = SimpleNamespace(notify_lineage=lambda _lineage_id: None)
        server.publisher = _Publisher()

        server._finish_changes([
            ("resident", "resident_123456789abc", "identity", {}),
        ])

        assert server.publisher.items == [
            (
                "portal.projection.changed.active-agents.snapshot",
                {
                    "kind": "changed",
                    "fields": ["resident_123456789abc"],
                },
            ),
            (
                "portal.resident.changed.resident_123456789abc.identity",
                {},
            ),
        ]


@pytest.mark.parametrize("mode", ["local", "socket", "read_worker", "read_pool"])
@pytest.mark.parametrize(
    "args,kwargs",
    [
        (["resident_abcdef123456"], {}),
        (["resident_abcdef123456", "CASE-A"], {"actor": "CASE-A"}),
        (["resident_abcdef123456", "CASE-A"], {"unexpected": True}),
    ],
)
def test_domain_binding_rejects_invalid_calls_before_state_access(
    lineage_process, monkeypatch, mode, args, kwargs,
):
    """参数不匹配的领域调用在本地、socket 和读进程池中都在访问状态前被拒绝"""
    client, publisher = lineage_process

    def unexpected_state_access(*args, **kwargs):
        raise AssertionError("invalid arguments reached state access or read pool")

    monkeypatch.setattr(
        lineage_server, "_operation_lineage_id", unexpected_state_access,
    )
    params = {
        "domain": "resident",
        "name": "get_resident_session_projection",
        "args": args,
        "kwargs": kwargs,
    }
    with pytest.raises(ValueError, match="invalid arguments for resident.get_resident_session_projection"):
        if mode == "local":
            lineage_server._LocalDomain("resident").get_resident_session_projection(*args, **kwargs)
        elif mode == "socket":
            client.call("resident", "get_resident_session_projection", *args, **kwargs)
        elif mode == "read_worker":
            lineage_server._execute_read_domain_call(params)
        else:
            server = object.__new__(SocietyServiceServer)
            server.read_pool = SimpleNamespace(workers=1, call=unexpected_state_access)
            server._domain_call(params)
    assert publisher.items == []


@pytest.mark.parametrize("mode", ["local", "socket"])
def test_domain_missing_key_preserves_original_arguments(lineage_process, monkeypatch, mode):
    """领域处理函数抛出的 KeyError 经本地或 socket 调用后保留原始参数"""
    client, _publisher = lineage_process

    def missing(path: str, actor: str):
        raise KeyError("workspace-file")

    monkeypatch.setattr(_WorkspaceDomain, "get_workspace_link_file", staticmethod(missing))
    domain = lineage_server._LocalDomain("workspace") if mode == "local" else client.workspace
    with pytest.raises(KeyError) as caught:
        domain.get_workspace_link_file("/share/example", "CASE-A")
    assert caught.value.args == ("workspace-file",)
    assert str(caught.value) == str(KeyError("workspace-file"))


@pytest.mark.parametrize("mode", ["local", "socket"])
def test_domain_dependency_error_contract_is_transport_independent(lineage_process, monkeypatch, mode):
    """Workspace 领域依赖的 ResourceService 不可用时，本地和 socket 调用都报服务不可用"""
    client, _publisher = lineage_process

    def unavailable(path: str, actor: str):
        raise ResourceServiceUnavailable("resource dependency unavailable")

    monkeypatch.setattr(_WorkspaceDomain, "get_workspace_link_file", staticmethod(unavailable))
    domain = lineage_server._LocalDomain("workspace") if mode == "local" else client.workspace
    with pytest.raises(SocietyServiceUnavailable, match="resource dependency unavailable"):
        domain.get_workspace_link_file("/share/example", "CASE-A")


@pytest.mark.parametrize(
    "domain,method", [
        ("lineage", "authorize_lineage_resident_principal"),
        ("resident", "filter_visible_resident_ids"),
        ("resident", "search_resident_directory"),
        ("project", "validate_project_deletion"),
    ],
)
def test_nonprefixed_read_contract_does_not_infer_changes(lineage_process, monkeypatch, domain, method):
    """名称不带 get 前缀的读操作按契约进入读路径且不发布变更"""
    client, publisher = lineage_process

    def read_only(resident_id: str, actor: str, lineage_id: str = ""):
        return {"resident_id": resident_id, "actor": actor}

    original = lineage_server._domain_handler
    monkeypatch.setattr(
        lineage_server, "_domain_handler",
        lambda requested_domain, name: read_only if (requested_domain, name) == (domain, method) else original(requested_domain, name),
    )
    assert lineage_server.operation_contract(domain, method).mode == "read"
    result = client.call(domain, method, "resident_abcdef123456", "CASE-A")
    assert result["resident_id"] == "resident_abcdef123456"
    assert publisher.items == []
    server = object.__new__(SocietyServiceServer)
    routed = []
    server.read_pool = SimpleNamespace(workers=1, call=lambda params: routed.append(params) or result)
    assert server._domain_call({"domain": domain, "name": method, "args": [], "kwargs": result}) == result
    assert len(routed) == 1


def _contract_read_handler(path: str, *, actor: str = "CASE-A"):
    if path == "/denied":
        raise InstructionError(
            "instruction.capability_denied", "read denied",
            details={"capability": "hatg:read"}, http_status=403,
        )
    return {"path": path, "actor": actor}


def _initialize_contract_read_worker():
    lineage_server._domain_module = lambda domain: _LineageDomain
    lineage_server._domain_handler = lambda domain, method: _contract_read_handler


@pytest.mark.parametrize("mode", ["local", "socket"])
def test_domain_signature_defaults_and_instruction_error_contract(lineage_process, monkeypatch, mode):
    """领域调用沿用签名默认值，InstructionError 保留错误码、详情和 403 且不发布变更"""
    client, publisher = lineage_process
    monkeypatch.setattr(_WorkspaceDomain, "get_workspace_link_file", staticmethod(_contract_read_handler))
    domain = lineage_server._LocalDomain("workspace") if mode == "local" else client.workspace
    assert domain.get_workspace_link_file("/allowed") == {"path": "/allowed", "actor": "CASE-A"}
    assert domain.get_workspace_link_file(path="/allowed", actor="sample_admin")["actor"] == "sample_admin"
    with pytest.raises(InstructionError) as caught:
        domain.get_workspace_link_file("/denied")
    assert caught.value.code == "instruction.capability_denied"
    assert caught.value.details == {"capability": "hatg:read"}
    assert caught.value.http_status == 403
    assert publisher.items == []


@pytest.mark.parametrize("mode", ["local", "socket"])
def test_domain_internal_type_error_is_not_an_argument_rejection(lineage_process, monkeypatch, mode):
    """领域处理函数内部的 TypeError 原样抛出，不被当作参数错误"""
    client, _publisher = lineage_process

    def broken(path: str, actor: str):
        raise TypeError("handler internal failure")

    monkeypatch.setattr(_WorkspaceDomain, "get_workspace_link_file", staticmethod(broken))
    domain = lineage_server._LocalDomain("workspace") if mode == "local" else client.workspace
    with pytest.raises((TypeError, RuntimeError), match="handler internal failure"):
        domain.get_workspace_link_file("/allowed", "CASE-A")


def test_uncertain_mutation_timeout_is_not_retried(monkeypatch, tmp_path):
    """写操作等待响应超时后报 SocietyServiceBusy，且不重发请求"""
    sent = []
    closed = []

    def timeout():
        raise socket.timeout("response deadline exceeded")

    stream = SimpleNamespace(readline=timeout, close=lambda: closed.append("stream"))
    connection = SimpleNamespace(
        settimeout=lambda value: None,
        connect=lambda path: None,
        sendall=lambda payload: sent.append(payload),
        makefile=lambda mode: stream,
        close=lambda: closed.append("socket"),
    )
    monkeypatch.setattr(lineage_transport.socket, "socket", lambda *args: connection)
    from management_common.rpc_auth import RpcAuthenticator
    RpcAuthenticator(tmp_path / "lineage.sock")
    client = SocietyServiceClient(tmp_path / "lineage.sock")
    with pytest.raises(lineage_transport.SocietyServiceBusy):
        client.resident.cancel_resident_service_input("resident_abcdef123456", "CASE-A", "msg_service")
    assert len(sent) == 1
    assert closed == ["stream", "socket"]


@pytest.mark.parametrize("mode", ["local", "socket"])
def test_domain_conflict_preserves_its_payload(lineage_process, monkeypatch, mode):
    """领域冲突错误经 RPC 保留结构化细节

    验证 LineageConflictError 在本地调用和 socket RPC 中都保留 conflict 与 resource 细节。
    """
    client, publisher = lineage_process
    payload = {"resource": "program"}

    def conflict(path: str, actor: str):
        raise LineageConflictError("the component was deleted", **payload)

    monkeypatch.setattr(_WorkspaceDomain, "get_workspace_link_file", staticmethod(conflict))
    domain = lineage_server._LocalDomain("workspace") if mode == "local" else client.workspace
    with pytest.raises(LineageConflictError) as caught:
        domain.get_workspace_link_file("/share/example", "CASE-A")
    assert caught.value.payload == {"conflict": True, **payload}
    assert caught.value.conflict_details() == caught.value.payload
    assert publisher.items == []


def test_read_pools_split_the_cores_between_dev_and_release(monkeypatch):
    """Dev 与 Release 的 SocietyService 读取进程分用主机核数

    未设置 PORTAL_LINEAGE_READ_WORKERS 时，24 核主机上每个 SocietyService 默认 12 个读取进程，另有 3 个 Inbox 活动进程；Dev 与 Release 的 unit 都设为 12。

    :importance: P1
    :design: decision_1b2bdf5c33d6
    """
    # Four read processes and one for Inbox activity queued reads into 504s.
    from pathlib import Path

    monkeypatch.delenv("PORTAL_LINEAGE_READ_WORKERS", raising=False)
    monkeypatch.setattr(lineage_server.os, "cpu_count", lambda: 24)
    assert lineage_server.parser().parse_args([]).read_workers == lineage_server.DEFAULT_READ_WORKERS == 12
    assert lineage_server.CONVERSATION_INDEX_WORKERS == 3
    systemd = Path(__file__).resolve().parents[2] / "systemd"
    for instance in ("dev", "release"):
        unit = (systemd / f"portal-{instance}-society-service.service").read_text(encoding="utf-8")
        assert "Environment=PORTAL_LINEAGE_READ_WORKERS=12\n" in unit
