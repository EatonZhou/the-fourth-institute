"""
:module: lineage.service
:importance: P1
:aspects: functional, behavior
"""
from __future__ import annotations

import socketserver
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tests.cases.rpc_test_support import socket_rpc_authenticator

from management_portal.society_service import server, service_invocation_domain as domain
from management_portal.society_service.service_invocation_store import ServiceInvocationStore
from management_portal.society_service.transport import SocietyServiceClient
from management_portal.service_invocation_contract import ServiceInvocationConflict
from tests.cases.test_service_invocation_termination import prepare
from tests.cases.test_shared_search_invocation import job, runtime


def fail(runtime, selected=None):
    return runtime.owner.fail_invocation(job=selected or job(), surface="knowledge", plan_fingerprint="")


def test_empty_fingerprint_fences_exact_committed_plan_after_lost_prepare_response(runtime):
    """空指纹失败终结精确已提交计划并保留原身份"""
    plan = prepare(runtime)
    result = runtime.owner.fail_invocation(job=job(), surface="knowledge")
    assert result["fence_reason"] == "failed" and result["receipt"]["status"] == "failed"
    assert result["receipt"]["plan_fingerprint"] == plan["plan_fingerprint"]
    assert result["receipt"]["binding"] == plan["binding"]
    assert all(batch["status"] == "prepared" and batch["fenced"] for batch in result["receipt"]["batches"])
    assert fail(runtime) == result
    with pytest.raises(ServiceInvocationConflict):
        runtime.owner.fail_invocation(job=job(), surface="knowledge", plan_fingerprint="wrong")
    assert runtime.owner.read_plan(job=job(), surface="knowledge") == result["receipt"]
    assert not runtime.executions.submitted and not runtime.executions.cancelled


def test_failure_before_prepare_persists_exact_failed_fence_without_creating_plan(runtime):
    """准备前失败持久封禁精确代次且不创建占位计划"""
    expected = {"receipt": None, "fenced": True, "fence_reason": "failed"}
    assert fail(runtime) == expected
    assert fail(runtime) == expected
    assert runtime.owner.read_plan(job=job(), surface="knowledge") is None
    with pytest.raises(ServiceInvocationConflict, match="fenced"):
        prepare(runtime)
    assert runtime.owner.cancel_invocation(job=job(), surface="knowledge")["fence_reason"] == "failed"
    assert fail(runtime) == expected
    for selected in (job(generation=2), job(actor="bob"), job(search_id="other")):
        assert not prepare(runtime, selected)["fenced"]
    # A late failure for generation one must not select/fence generation two.
    assert fail(runtime) == expected
    assert not runtime.owner.read_plan(job=job(generation=2), surface="knowledge")["fenced"]
    with pytest.raises(KeyError):
        runtime.owner.fail_invocation(job=job(search_id="missing"), surface="knowledge", plan_fingerprint="provided")
    assert not runtime.executions.submitted and not runtime.executions.cancelled


@pytest.mark.parametrize("plan_exists", [False, True])
def test_outdated_fence_schema_is_rejected_without_runtime_migration(runtime, plan_exists):
    """服务调用屏障表结构过时时启动直接报错，不在运行时迁移或改动已有数据

    :module: workflow.knowledge
    :importance: P3
    :aspects: functional
    """
    if plan_exists:
        prepare(runtime)
    try:
        store = domain._store()
        with store.database.transaction():
            pass
        with store.database.transaction() as connection:
            connection.execute("DROP TABLE service_run_fences")
            connection.execute("CREATE TABLE service_run_fences (actor TEXT, surface TEXT, search_id TEXT, generation INTEGER, PRIMARY KEY(actor,surface,search_id,generation))")
            connection.execute('INSERT INTO service_run_fences VALUES(%s,%s,%s,%s)', store.key(job(), "knowledge"))
        with store.database.read() as connection:
            before = connection.execute("SELECT * FROM service_run_fences").fetchall()
        with pytest.raises(ServiceInvocationConflict, match="does not match its schema"):
            ServiceInvocationStore()
        with store.database.read() as connection:
            assert connection.execute("SELECT * FROM service_run_fences").fetchall() == before
        with pytest.raises(ServiceInvocationConflict, match="does not match its schema"):
            ServiceInvocationStore()
        assert not runtime.executions.submitted and not runtime.executions.cancelled
    finally:
        # Put the current fence table back for the tests that follow (DDL is not truncated).
        with domain._store().database.transaction() as connection:
            connection.execute("DROP TABLE IF EXISTS service_run_fences")
        ServiceInvocationStore()


@pytest.mark.parametrize("prepare_commits_first", [False, True])
def test_empty_fingerprint_failure_and_concurrent_prepare_are_atomic(runtime, monkeypatch, prepare_commits_first):
    """空指纹失败与并发准备在单一事务边界串行化"""
    entered, release, prepare_started = threading.Event(), threading.Event(), threading.Event()
    if prepare_commits_first:
        original = ServiceInvocationStore.reserve

        def reserve(self, *args):
            result = original(self, *args)
            entered.set()
            assert release.wait(3)
            return result

        monkeypatch.setattr(ServiceInvocationStore, "reserve", reserve)
    else:
        original = ServiceInvocationStore._read

        def read(self, connection, key):
            result = original(self, connection, key)
            if not entered.is_set():
                assert connection.in_transaction
                entered.set()
                assert release.wait(3)
            return result

        monkeypatch.setattr(ServiceInvocationStore, "_read", read)

    def prepare_late():
        prepare_started.set()
        return prepare(runtime)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(prepare if prepare_commits_first else fail, runtime)
        try:
            assert entered.wait(3)
            if prepare_commits_first:
                result = fail(runtime)
                assert result["receipt"]["fence_reason"] == "failed"
            else:
                second = pool.submit(prepare_late)
                assert prepare_started.wait(3)
                assert not second.done()
        finally:
            release.set()
        first.result(timeout=3)
        if not prepare_commits_first:
            with pytest.raises(ServiceInvocationConflict):
                second.result(timeout=3)
    result = fail(runtime)
    assert result["fence_reason"] == "failed"
    assert (result["receipt"] is not None) == prepare_commits_first
    assert not runtime.executions.submitted and not runtime.executions.cancelled


def test_rpc_accepts_omitted_fingerprint_and_returns_nullable_failed_receipt(runtime, tmp_path, socket_rpc_authenticator):
    """正式RPC接受缺省指纹并返回可空失败回执"""
    class Endpoint(socketserver.UnixStreamServer):
        dispatch = server.SocietyServiceServer.dispatch
        _domain_call = server.SocietyServiceServer._domain_call

    with Endpoint(str(tmp_path / "unprepared.sock"), server._LineageRequestHandler) as endpoint:
        endpoint.rpc_auth = socket_rpc_authenticator(endpoint.server_address)
        endpoint.read_pool = SimpleNamespace(workers=1, call=Mock(side_effect=AssertionError("mutation in read pool")))
        endpoint._finish_changes = Mock()
        worker = threading.Thread(target=endpoint.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        try:
            remote = SocietyServiceClient(endpoint.server_address, timeout=2).service_invocation
            result = remote.fail_invocation(job=job(), surface="knowledge")
            assert result == {"receipt": None, "fenced": True, "fence_reason": "failed"}
            assert remote.fail_invocation(job=job(), surface="knowledge", plan_fingerprint="") == result
            runtime.access.is_active.side_effect = lambda actor: actor == "bob"
            with pytest.raises(PermissionError):
                remote.fail_invocation(job=job(), surface="knowledge")
            assert not runtime.executions.submitted and not runtime.executions.cancelled
        finally:
            endpoint.shutdown()
            worker.join(timeout=2)
            assert not worker.is_alive()
