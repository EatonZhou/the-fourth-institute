"""
:module: lineage.hatg
:importance: P1
:aspects: functional, behavior
"""
from __future__ import annotations

import copy
import json
import socketserver
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.society_service import server
from management_portal.society_service import service_invocation_domain as domain
from management_portal.society_service.service_invocation_store import ServiceInvocationConflict, ServiceInvocationStore
from management_portal.society_service.transport import SocietyServiceClient
from management_portal.service_invocation_contract import batch_manifest
from tests.cases.test_shared_search_invocation import job, runtime
from tests.cases.rpc_test_support import socket_rpc_authenticator


def prepare(runtime, selected=None):
    selected = selected or job()
    return runtime.owner.prepare_batches(job=selected, surface="knowledge", batches=batch_manifest(selected, ["one", "two"]))


def files(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("creator_active", [True, False])
def test_read_plan_preserves_frozen_receipts_without_observing_or_cancelling(runtime, monkeypatch, creator_active):
    """搜索诊断完整保留冻结回执且不观察或取消执行

    验证父子失败状态、绑定和指纹保持原样；原创建者停用后仍可只读诊断，prepare/submit/get/cancel 仍拒绝。诊断不检查原创建者 active、不调用写事务、绑定解析或 Execution，修改返回对象不影响持久记录。

    :risk: no-external-side-effects
    """
    plan = prepare(runtime)
    store = domain._store()
    first = plan["batches"][0]
    store.observe(job(), "knowledge", first["batch_id"], plan["plan_fingerprint"], "failed")
    expected = store.get(job(), "knowledge")
    runtime.access.is_active.side_effect = lambda actor: actor == "bob" or (actor == "alice" and creator_active)
    runtime.access.is_active.reset_mock()
    before = files(runtime.root)
    forbidden = Mock(side_effect=AssertionError("diagnostics must not write, observe or cancel"))
    monkeypatch.setattr(ServiceInvocationStore, "transaction", forbidden)
    monkeypatch.setattr(domain, "_cancel_pending", forbidden)
    monkeypatch.setattr(domain, "_observe_batch", forbidden)
    monkeypatch.setattr(domain.resident_protocol, "get_resident_input_state", forbidden)
    monkeypatch.setattr(domain, "search_binding", forbidden)
    actual = runtime.owner.read_plan(job=job(), surface="knowledge")
    assert actual == expected
    assert actual["status"] == actual["batches"][0]["status"] == "failed"
    assert actual["binding"] == plan["binding"]
    assert actual["plan_fingerprint"] == plan["plan_fingerprint"]
    actual["batches"][0]["binding"]["invoke_policy"] = "local-only"  # the reader gets its own copy
    assert runtime.owner.read_plan(job=job(), surface="knowledge") == expected
    runtime.access.is_active.assert_not_called()
    if not creator_active:
        batch = {"batch_id": first["batch_id"], "plan_fingerprint": plan["plan_fingerprint"]}
        calls = [
            ("get_invocation", {}),
            ("cancel_invocation", {}),
            ("prepare_batches", {"batches": batch_manifest(job(), ["one", "two"])}),
            ("submit_batch", {**batch, "prompt": "one"}),
            ("get_batch", batch),
        ]
        for method, parameters in calls:
            with pytest.raises(PermissionError, match="search creator is not active"):
                getattr(runtime.owner, method)(job=job(), surface="knowledge", **parameters)
        with pytest.raises(PermissionError, match="search creator is not active"):
            runtime.owner.prepare_batches(job=job(generation=2), surface="knowledge",
                                          batches=batch_manifest(job(generation=2), ["new run"]))
    assert files(runtime.root) == before
    forbidden.assert_not_called()


@pytest.mark.parametrize("changes,error", [
    ({"created_by": "bob"}, None),
    ({"search_id": "another-search"}, None),
    ({"run_generation": 2}, None),
    ({"surface": "paper"}, ValueError),
    ({"created_by": "inactive"}, None),
    ({"run_generation": True}, ValueError),
    ({"run_generation": 0}, ValueError),
])
def test_plan_reads_use_exact_authoritative_job_identity(runtime, changes, error):
    """搜索诊断使用精确业务主体和代次键

    验证不同来源用户、搜索和代次不能读到既有计划，surface 混淆或无效代次明确拒绝；来源用户停用不改变精确查询的空值语义。

    :risk: no-external-side-effects
    """
    prepare(runtime)
    before = files(runtime.root)
    selected = {**job(), **changes}
    if error:
        with pytest.raises(error):
            runtime.owner.read_plan(job=selected, surface="knowledge")
    else:
        assert runtime.owner.read_plan(job=selected, surface="knowledge") is None
    assert files(runtime.root) == before


@pytest.mark.parametrize("field,value", [("created_by", "bob"), ("surface", "paper"),
                                         ("search_id", "other"), ("run_generation", 2)])
def test_mismatched_child_ownership_is_rejected(runtime, field, value):
    """批次记录的创建者、页面、检索 ID 或代次与计划不符时拒绝读取计划，且不改动文件

    :module: platform.core
    :importance: P2
    :aspects: functional
    """
    plan = prepare(runtime)
    with domain._store().database.transaction() as connection:
        connection.execute('UPDATE service_invocations SET batches_json=jsonb_set(batches_json,%s,%s::jsonb)',
                           (['0', field], json.dumps(value)))
    before = files(runtime.root)
    with pytest.raises(ServiceInvocationConflict, match="identity"):
        runtime.owner.read_plan(job=job(), surface="knowledge")
    assert files(runtime.root) == before


@pytest.mark.parametrize("fence", ["cancel", "delete", "refresh"])
@pytest.mark.parametrize("status", ["failed", "completed"])
def test_historical_plan_read_projects_existing_fences_without_changing_receipts(runtime, fence, status):
    """搜索旧代次诊断只投影已有取消删除和刷新屏障

    验证完成与失败计划在取消、删除、刷新后可只读诊断，当前代次和屏障遵循持久事实且不重发取消。

    :risk: no-external-side-effects
    """
    plan = prepare(runtime)
    store = domain._store()
    for batch in plan["batches"]:
        store.observe(job(), "knowledge", batch["batch_id"], plan["plan_fingerprint"], status)
    if fence == "refresh":
        prepare(runtime, job(generation=2))
    else:
        store.cancel(job(), "knowledge", deleted=fence == "delete")
    expected = store.get(job(), "knowledge")
    before = files(runtime.root)
    actual = runtime.owner.read_plan(job=job(), surface="knowledge")
    assert actual == expected
    assert actual["is_current_generation"] == (fence == "cancel")
    assert actual["fenced"] == (fence != "refresh" or status != "completed")
    assert actual["plan_fingerprint"] == plan["plan_fingerprint"]
    assert actual["binding"] == plan["binding"]
    assert not runtime.executions.cancelled
    assert files(runtime.root) == before


def test_plan_reads_do_not_wait_for_a_reserved_writer(runtime):
    """搜索诊断可与未提交写事务并发读取

    验证两个诊断读取不等待持有写事务的调用，返回旧已提交事实，写提交后的新读取才见到新状态。

    :risk: no-external-side-effects
    """
    plan = prepare(runtime)
    store = domain._store()
    changed = copy.deepcopy(plan)
    changed["status"] = "failed"
    for batch in changed["batches"]:
        batch["status"] = "failed"
    with ThreadPoolExecutor(max_workers=2) as readers:
        with store.database.transaction() as connection:
            store._write(connection, ('alice', 'knowledge', plan['search_id'], plan['run_generation']), changed)
            futures = [readers.submit(runtime.owner.read_plan, job=job(), surface="knowledge") for _ in range(2)]
            assert [future.result(timeout=2) for future in futures] == [plan, plan]
    assert runtime.owner.read_plan(job=job(), surface="knowledge")["status"] == "failed"


def test_parent_and_fence_projection_share_one_committed_read_snapshot(runtime, monkeypatch):
    """搜索计划与屏障诊断来自同一读取快照

    验证父计划读取期间并发提交新代次时不会混合两次提交的计划与屏障事实。

    :risk: no-external-side-effects
    """
    plan = prepare(runtime)
    store = domain._store()
    project = ServiceInvocationStore._project

    def commit_new_head():
        # Another connection commits a newer search head while the read is in progress.
        with store.database.transaction() as writer:
            writer.execute("UPDATE service_search_heads SET generation=2")

    def publish_new_head(selected_store, connection, key, record):
        writer = threading.Thread(target=commit_new_head)
        writer.start()
        writer.join(5)
        return project(selected_store, connection, key, record)

    monkeypatch.setattr(ServiceInvocationStore, "_project", publish_new_head)
    assert runtime.owner.read_plan(job=job(), surface="knowledge") == plan
    monkeypatch.setattr(ServiceInvocationStore, "_project", project)
    current = runtime.owner.read_plan(job=job(), surface="knowledge")
    assert not current["fenced"] and not current["is_current_generation"]


def test_read_plan_reaches_rpc_read_dispatch_with_nullable_and_error_contracts(runtime, tmp_path, socket_rpc_authenticator):
    """搜索诊断本地与 RPC 只读结果和错误一致

    验证真实隔离 Unix Socket 经正式 read dispatch 返回冻结计划、空值和归属异常，不派发执行或取消。

    :risk: no-external-side-effects
    """
    plan = prepare(runtime)
    class ReadServer(socketserver.UnixStreamServer):
        dispatch = server.SocietyServiceServer.dispatch
        _domain_call = server.SocietyServiceServer._domain_call

    read = Mock(side_effect=server._execute_read_domain_call)
    with ReadServer(str(tmp_path / "read.sock"), server._LineageRequestHandler) as endpoint:
        endpoint.rpc_auth = socket_rpc_authenticator(endpoint.server_address)
        endpoint.read_pool = SimpleNamespace(workers=1, call=read)
        worker = threading.Thread(target=endpoint.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        try:
            remote = SocietyServiceClient(endpoint.server_address, timeout=2).service_invocation
            assert remote.read_plan(job=job(), surface="knowledge") == plan
            assert remote.read_plan(job=job(generation=2), surface="knowledge") is None
            with pytest.raises(ValueError, match="surface mismatch"):
                remote.read_plan(job=job(surface="paper"), surface="knowledge")
            assert remote.read_plan(job=job(actor="inactive"), surface="knowledge") is None
            with domain._store().database.transaction() as connection:
                connection.execute('UPDATE service_invocations SET batches_json=jsonb_set(batches_json,\'{0,created_by}\',\'"bob"\'::jsonb)')
            with pytest.raises(ServiceInvocationConflict, match="identity"):
                remote.read_plan(job=job(), surface="knowledge")
            assert read.call_count == 5
            assert not runtime.executions.submitted
            assert not runtime.executions.cancelled
        finally:
            endpoint.shutdown()
            worker.join(timeout=2)
            assert not worker.is_alive()
