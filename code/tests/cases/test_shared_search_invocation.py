from __future__ import annotations
from management_portal.society_service import resident_execution_domain

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import resident_handoff, shared_search_invocation as adapter
from management_portal.society_service import service_invocation_domain as domain
from management_portal.service_invocation_contract import batch_manifest
from management_portal.society_service.server import LocalSocietyService
from management_portal.society_service.service_invocation_store import ServiceInvocationConflict
from tests.cases.society_service_test_support import publish_registered_lineage_members


LINEAGE = "lineage_0123456789ab"
RESIDENT = "resident_0123456789ab"


def job(actor="alice", generation=1, surface="knowledge", search_id="search-one"):
    return {"created_by": actor, "surface": surface, "search_id": search_id, "run_generation": generation}


def prepare_invocation(owner, *, job, surface, prompt):
    """Prepare a one-batch plan through the owner, as a single search run does."""
    return owner.prepare_batches(job=job, surface=surface, batches=batch_manifest(job, [prompt]))["batches"][0]


def submit_invocation(owner, *, job, surface, prompt):
    record = domain._store().get(domain._job(job, surface), surface)
    return owner.submit_batch(job=job, surface=surface, batch_id=record["batches"][0]["batch_id"],
                              plan_fingerprint=record["plan_fingerprint"], prompt=prompt)


class Executions:
    def __init__(self):
        self.requests = {}
        self.cancelled = []
        self.submitted = []
        self.lock = threading.Lock()
        self.lose_ack = False
        self.wait_hook = None

    def submit(self, request):
        with self.lock:
            if request.metadata["user_message_id"] in self.requests:
                return {}
            self.requests[request.metadata["user_message_id"]] = request
            self.submitted.append(request)
        if self.lose_ack:
            self.lose_ack = False
            raise ConnectionError("accepted but acknowledgement lost")
        return {"message_id": request.execution_id}

    def status(self, execution_id, *, actor, include_events=False, include_result=True):
        request = self.requests[execution_id]
        if request.actor != actor:
            raise KeyError(execution_id)
        state = "cancelled" if execution_id in self.cancelled else "completed"
        return {"status": state, "result": {"message_id": execution_id, "status": state,
                "output": {"message": request.prompt}, "usage": {}}}

    def cancel(self, execution_id, *, actor, reason=""):
        self.status(execution_id, actor=actor)
        self.cancelled.append(execution_id)
        return {"status": "cancelled"}

    def wait(self, execution_id, *, actor, timeout_seconds, cancellation_requested=None):
        if self.wait_hook:
            self.wait_hook()
        if cancellation_requested and cancellation_requested():
            self.cancel(execution_id, actor=actor)
        return self.status(execution_id, actor=actor)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    facade = LocalSocietyService()
    # The holder of the Portal duty search.knowledge, as semantic_search_surfaces reports it.
    binding = {"surface": "knowledge", "resident_id": RESIDENT,
               "lineage_id": LINEAGE, "invoke_policy": "active_portal_users"}
    registry = Mock()
    registry.resolve.side_effect = lambda surface: copy.deepcopy(binding)
    access = Mock()
    access.is_active.side_effect = lambda actor: actor in {"alice", "bob"}
    lineage = {"lineage_id": LINEAGE, "revision": 7, "nodes": [{"id": RESIDENT}],
               "created_at": "2026-09-25T00:00:00Z", "updated_at": "2026-09-25T00:00:00Z"}
    descriptor = {"resource_id": LINEAGE, "attributes": {"status": "active"}}
    resources = Mock()
    resources.get.side_effect = lambda selected, *, actor: copy.deepcopy(descriptor)
    catalogue = Mock()
    executions = Executions()
    monkeypatch.setattr(domain, "search_binding", lambda surface: registry.resolve(surface))
    monkeypatch.setattr(domain, "get_portal_access_store", lambda: access)
    monkeypatch.setattr(domain, "resource_port", resources)
    monkeypatch.setattr(domain.lineage_domain, "_read_lineage_state", lambda selected: copy.deepcopy(lineage))
    monkeypatch.setattr(domain.resident_catalog, "get_catalog", lambda: catalogue)
    monkeypatch.setattr(adapter, "get_society_service", lambda: facade)
    from management_portal.society_service import resident_input_scope, resident_protocol, resident_domain, resident_workspace_domain, invariant_locks
    from management_portal.society_service.state_store import LineageDataStore
    from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository
    from management_portal.execution.agent_data_store import AgentDataStore
    from management_portal.execution.service import ExecutionWaitTimeout
    state_store = LineageDataStore()
    state_store.put_lineage_definition(LINEAGE, {"lineage_id": LINEAGE, "nodes": [], "edges": []}, {})
    catalogue.state_store = state_store
    publish_registered_lineage_members(lineage, "alice")
    configured = state_store.resident_record(RESIDENT)
    configured.update(backend="provider_api", backend_route_id="approved")
    from management_portal.society_service.resident_settings_contract import stage_runtime_settings
    stage_runtime_settings(configured, {"backend": "provider_api", "model": "approved-model",
                                       "prompt_context": {"system_prompt": "Rank supplied candidates"}})
    state_store.put_resident_record(RESIDENT, configured, commit_configuration=True)
    resident = state_store.resident_record(RESIDENT)
    catalogue.get.side_effect = lambda selected, **kwargs: copy.deepcopy(resident)
    repository = ResidentConversationRepository(state_store, AgentDataStore())
    monkeypatch.setattr(resident_input_scope, "get_lineage_data_store", lambda: state_store)
    monkeypatch.setattr(resident_input_scope, "get_portal_access_store", lambda: access)
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    monkeypatch.setattr(resident_protocol.resident_conversation_stream, "get_repository", lambda: repository)
    monkeypatch.setattr(resident_workspace_domain, "resolve_resident_runtime_target",
                        lambda rid, actor="", **kwargs: resident_domain.ResidentRuntimeTarget(rid, resident))
    monkeypatch.setattr(resident_protocol, "_resident_view", lambda target: {"resident_id": RESIDENT, "lineage_id": LINEAGE})
    monkeypatch.setattr(resident_protocol, "_conversation_controls", lambda *args: {})
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: facade)
    monkeypatch.setattr(resident_handoff, "ensure_notification_listener", lambda: None)
    monkeypatch.setattr(resident_handoff, "get_notification_hub", lambda: SimpleNamespace(sequence=0))
    original_state = resident_protocol.get_resident_input_state
    def state(rid, actor, mid):
        try:
            saved = original_state(rid, actor, mid)
            if saved['state'] in {'completed', 'failed', 'cancelled'}:
                return saved
        except KeyError:
            pass
        observed = executions.status(mid, actor=actor)
        status = observed.get("status")
        replies = []
        if status == "completed":
            text = (observed.get("result", {}).get("output") or {}).get("message", "")
            if text:
                reply_id = 'msg_' + __import__('hashlib').sha256(mid.encode()).hexdigest()[:32]
                repository.append_message(rid, {"message_id": reply_id, "role": "agent", "reply_to_message_id": mid,
                    "created_at": "2026-09-25T00:00:01Z", "content_parts": [{"type": "text", "text": text}]})
                replies = [reply_id]
        repository.set_input_state(rid, mid, status)
        return original_state(rid, actor, mid)
    monkeypatch.setattr(resident_protocol, "get_resident_input_state", state)
    def deliver(*, actor, identity, message, **kwargs):
        repository.commit_user_message(RESIDENT, {**message, "author_id": actor, "created_at": "2026-09-25T00:00:00Z"})
        mid = message["message_id"]
        request = resident_execution_domain._build_resident_execution_request(resident, {
            "actor": actor, "job_id": "job-" + mid, "user_message_id": mid,
            "raw_message": message["content_parts"][0]["text"],
        })
        executions.submit(request)
        repository.set_input_state(RESIDENT, mid, "queued")
        return {"input_state": {"state": "queued"}}
    monkeypatch.setattr(domain, "deliver_resident_message", deliver)
    def cancel(rid, actor, mid):
        result = executions.cancel(mid, actor=actor)
        repository.set_input_state(rid, mid, "cancelled")
        return result
    monkeypatch.setattr(domain, "cancel_resident_invocation", cancel)
    def wait(**kwargs):
        try:
            saved = original_state(kwargs['resident_id'], kwargs['actor'], kwargs['message_id'])
        except KeyError:
            saved = {}
        if saved.get('state') == 'completed':
            return resident_handoff._result_from_input_state(saved, kwargs, actor=kwargs['actor'])
        try:
            executions.wait(kwargs["message_id"], actor=kwargs["actor"], timeout_seconds=kwargs["timeout_seconds"],
                            cancellation_requested=kwargs.get("cancellation_requested"))
        except ExecutionWaitTimeout as exc:
            raise TimeoutError(str(exc)) from exc
        terminal = state(kwargs["resident_id"], kwargs["actor"], kwargs["message_id"])
        if terminal["state"] in {"failed", "cancelled"}:
            raise RuntimeError(terminal["state"])
        if terminal["state"] != "completed":
            raise TimeoutError("still running")
        return resident_handoff._result_from_input_state(terminal, kwargs, actor=kwargs["actor"])
    monkeypatch.setattr(adapter, "resume_and_wait", wait)
    return SimpleNamespace(owner=facade.service_invocation, binding=binding, registry=registry,
                           access=access, lineage=lineage, resident=resident, descriptor=descriptor,
                           resources=resources, catalogue=catalogue, executions=executions,
                           root=tmp_path)




def invoke(selected=None, prompt="private-A", **options):
    selected = selected or job()
    return resident_handoff.run_shared_search_turn(job=selected, surface=selected["surface"], prompt=prompt,
                                                     timeout_seconds=0.5, **options)


@pytest.mark.parametrize("kind", [None, "ordinary"])
def test_explicitly_registered_service_accepts_an_ordinary_lineage(runtime, kind):
    """显式登记的共享服务可使用普通 Lineage"""
    if kind is not None:
        runtime.descriptor["attributes"]["kind"] = kind
    result = invoke()
    assert result["message_id"] in runtime.executions.requests
    assert runtime.resources.get.call_args.kwargs["actor"] == "alice"


def test_admitted_service_does_not_require_legacy_invoke_policy(runtime):
    """获准的服务调用不再依赖旧 invoke_policy 字段"""
    runtime.binding.pop("invoke_policy")
    result = invoke()
    assert result["message_id"] in runtime.executions.requests
    assert runtime.resources.get.call_args.kwargs["actor"] == "alice"


@pytest.mark.parametrize("failure", ["binding_revoked", "member_removed", "resident_deleted", "resident_disabled",
                                     "lineage_deleted", "lineage_missing", "descriptor_identity", "wrong_lineage", "creator_disabled", "surface"])
def test_live_authorization_refuses_before_submit(runtime, failure):
    """当前授权或成员状态无效时在提交前拒绝调用"""
    if failure == "binding_revoked":
        runtime.registry.resolve.side_effect = PermissionError("binding revoked")
    elif failure == "member_removed":
        runtime.lineage["nodes"] = []
    elif failure == "resident_deleted":
        runtime.catalogue.get.side_effect = KeyError(RESIDENT)
    elif failure == "resident_disabled":
        runtime.resident["status"] = "disabled"
    elif failure == "lineage_deleted":
        runtime.descriptor["attributes"]["deleted_at"] = "deleted"
    elif failure == "lineage_missing":
        runtime.descriptor["exists"] = False
    elif failure == "descriptor_identity":
        runtime.descriptor["resource_id"] = "lineage_1123456789ab"
    elif failure == "wrong_lineage":
        runtime.resident["lineage_id"] = "lineage_1123456789ab"
    elif failure == "creator_disabled":
        runtime.access.is_active.side_effect = lambda actor: False
    elif failure == "surface":
        runtime.binding["surface"] = "wall"
    with pytest.raises((PermissionError, KeyError, ValueError)):
        invoke()
    assert not runtime.executions.submitted


def test_metadata_permission_failure_never_borrows_configuration_owner(runtime):
    """元数据读取无权时保留调用者身份且不借用配置所有者"""
    runtime.resources.get.side_effect = PermissionError("definition is not readable by this principal")
    with pytest.raises(PermissionError):
        invoke()
    assert runtime.resources.get.call_args.kwargs["actor"] == "alice"
    assert not runtime.executions.submitted


@pytest.mark.parametrize("change", ["member", "creator", "binding"])
def test_prepared_receipt_rechecks_authorization_at_first_dispatch(runtime, change):
    """预备回执首次派发前重新核实成员、创建者与绑定"""
    prepare_invocation(runtime.owner,job=job(), surface="knowledge", prompt="private-A")
    if change == "member":
        runtime.lineage["nodes"] = []
    elif change == "creator":
        runtime.access.is_active.side_effect = lambda actor: False
    elif change == "binding":
        runtime.binding["resident_id"] = "resident_1123456789ab"
    with pytest.raises((PermissionError, ServiceInvocationConflict)):
        submit_invocation(runtime.owner,job=job(), surface="knowledge", prompt="private-A")
    assert not runtime.executions.submitted


def test_formal_owner_links_service_creator_generation_execution_and_agent_data(runtime):
    """服务回执关联创建者、代次、消息与授权记录"""
    first = invoke()
    other = invoke(job("bob"), prompt="private-B")
    request = runtime.executions.requests[first["message_id"]]
    from management_portal.execution.contracts import EXECUTION_SCHEMA_VERSION
    assert request.schema_version == EXECUTION_SCHEMA_VERSION
    assert request.session.mode == "stateless" and not request.session.binding_id
    assert not request.session.context_source_binding_id and not request.cwd
    assert request.agent_id == RESIDENT and first["message_id"] != other["message_id"]
    relation = runtime.owner.get_invocation(job=job(), surface="knowledge")["receipt"]
    assert relation["created_by"] == "alice" and relation["run_generation"] == 1
    assert relation["message_id"] == first["message_id"]
    assert relation["authorization"]["membership_verified"]
    assert relation["binding"] == runtime.binding
    assert relation["status"] == "completed"
    assert "private-A" not in json.dumps(relation)
    assert runtime.owner.get_invocation(job=job("bob"), surface="knowledge")["receipt"]["message_id"] == other["message_id"]


def test_new_generation_refresh_does_not_return_prior_execution(runtime):
    """新代次刷新创建新执行且旧回执保留为历史"""
    first = invoke()
    second = invoke(job(generation=2), prompt="new authorized candidates")
    assert second["message_id"] != first["message_id"]
    old = runtime.owner.get_invocation(job=job(), surface="knowledge")
    assert old["receipt"]["status"] == "completed" and old["receipt"]["is_current_generation"] is False
    assert old["input_state"]["message_id"] == first["message_id"]
    assert bool(old["input_state"]["reply_message_ids"])
    assert len(runtime.executions.submitted) == 2


def test_historical_receipt_is_observable_but_cannot_be_invoked_again(runtime):
    """历史检索回执可观察但禁止再次发起调用"""
    first = invoke()
    invoke(job(generation=2), prompt="new candidates")
    runtime.registry.resolve.side_effect = PermissionError("new invocation grant withdrawn")
    with pytest.raises(ServiceInvocationConflict, match="historical"):
        invoke()
    with pytest.raises(ServiceInvocationConflict, match="historical"):
        submit_invocation(runtime.owner,job=job(), surface="knowledge", prompt="private-A")
    old = runtime.owner.get_invocation(job=job(), surface="knowledge")
    assert old["input_state"]["message_id"] == first["message_id"]
    history = [runtime.owner.get_invocation(job=job(generation=generation), surface="knowledge")["receipt"]
               for generation in (2, 1)]
    assert [row["status"] for row in history] == ["completed", "completed"]
    assert [row["is_current_generation"] for row in history] == [True, False]
    with pytest.raises(KeyError):
        runtime.owner.get_invocation(job=job("bob"), surface="knowledge")
    runtime.owner.cancel_invocation(job=job("bob"), surface="knowledge")
    assert runtime.owner.get_invocation(job=job(), surface="knowledge")["receipt"]["status"] == "completed"
    assert len(runtime.executions.submitted) == 2


def test_completed_receipt_becoming_history_during_wait_is_not_a_current_result(runtime):
    """等待期间变为历史的回执不能作为当前检索结果"""
    runtime.executions.wait_hook = lambda: prepare_invocation(runtime.owner,job=job(generation=2), surface="knowledge", prompt="next run")
    with pytest.raises(adapter.SharedSearchInvocationFailed):
        invoke()
    old = runtime.owner.get_invocation(job=job(), surface="knowledge")
    assert old["receipt"]["status"] == "superseded" and old["receipt"]["is_current_generation"] is False
    assert old["input_state"] == {}
    assert runtime.owner.get_invocation(job=job(generation=2), surface="knowledge")["receipt"]["status"] == "prepared"
    assert len(runtime.executions.submitted) == 1


def test_completed_history_does_not_regress_on_a_late_status_observation(runtime):
    """迟到的运行状态观察不使已完成历史回执倒退"""
    receipt = invoke()["receipt"]
    prepare_invocation(runtime.owner,job=job(generation=2), surface="knowledge", prompt="next run")
    observed = domain._store().observe(job(), "knowledge", receipt["batch_id"], receipt["plan_fingerprint"], "running")
    assert observed["status"] == "completed" and observed["is_current_generation"] is False
    assert runtime.owner.get_invocation(job=job(), surface="knowledge")["input_state"]["state"] == "completed"


@pytest.mark.parametrize("deleted", [False, True])
def test_history_read_access_does_not_bypass_explicit_cancel_or_delete(runtime, deleted):
    """历史读取不能绕过显式取消或删除封锁"""
    invoke()
    invoke(job(generation=2), prompt="new candidates")
    runtime.owner.cancel_invocation(job=job(), surface="knowledge", deleted=deleted)
    old = runtime.owner.get_invocation(job=job(), surface="knowledge")
    assert old["receipt"]["fenced"] and old["input_state"] == {}
    current = runtime.owner.get_invocation(job=job(generation=2), surface="knowledge")
    assert current["receipt"]["fenced"] is deleted


def test_same_generation_concurrency_has_one_dispatch(runtime):
    """同代次并发调用只派发一次并复用消息标识"""
    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(lambda attempt: invoke(), range(8)))
    assert len({result["message_id"] for result in results}) == 1
    assert len(runtime.executions.submitted) == 1


def test_ack_loss_and_rebinding_resume_original_receipt_without_new_invoke(runtime):
    """确认丢失和绑定撤销后恢复原回执且不重复调用"""
    runtime.executions.lose_ack = True
    with pytest.raises(ConnectionError):
        invoke()
    first_id = runtime.executions.submitted[0].metadata["user_message_id"]
    runtime.registry.resolve.side_effect = PermissionError("registration withdrawn")
    result = invoke()
    assert result["message_id"] == first_id
    assert result["status"] == "completed"
    cancelled = runtime.owner.cancel_invocation(job=job(), surface="knowledge")
    assert cancelled["fenced"] and cancelled["fence_reason"] == "cancelled"
    assert cancelled["receipt"]["batches"][0]["status"] == "completed"
    assert runtime.executions.status(first_id, actor="alice")["status"] == "completed"
    assert runtime.executions.cancelled == []
    with pytest.raises(adapter.SharedSearchInvocationFailed) as raised:
        invoke()
    assert raised.value.receipt["fenced"]
    assert raised.value.receipt["message_id"] == first_id
    assert len(runtime.executions.submitted) == 1


def test_callback_receipt_precedes_submit_and_failure_is_recoverable(runtime):
    """分配回调先于提交且失败后可用原回执恢复"""
    receipts = []

    def reject(receipt):
        receipts.append(copy.deepcopy(receipt))
        assert not runtime.executions.submitted
        receipt["resident_id"] = "resident_1123456789ab"
        raise RuntimeError("JobStore write failed")

    with pytest.raises(RuntimeError, match="JobStore"):
        invoke(on_assigned=reject)
    assert not runtime.executions.submitted
    result = invoke()
    assert result["message_id"] == receipts[0]["message_id"]
    assert result["resident_id"] == RESIDENT


@pytest.mark.parametrize("action", ["cancel", "delete", "refresh"])
def test_late_success_is_fenced_after_job_control_change(runtime, action, monkeypatch):
    """取消删除或刷新后迟到的成功结果仍被封锁"""
    original_status = runtime.executions.status
    completed = False

    def status(execution_id, **options):
        result = original_status(execution_id, **options)
        if not completed:
            return {"status": "running"}
        return result

    monkeypatch.setattr(runtime.executions, "status", status)

    def control():
        nonlocal completed
        if action == "refresh":
            prepare_invocation(runtime.owner,job=job(generation=2), surface="knowledge", prompt="next run")
        else:
            runtime.owner.cancel_invocation(job=job(), surface="knowledge", deleted=action == "delete")
            runtime.executions.cancelled.clear()
        completed = True

    runtime.executions.wait_hook = control
    with pytest.raises(adapter.SharedSearchInvocationFailed):
        invoke()
    observed = runtime.owner.get_invocation(job=job(), surface="knowledge")
    assert observed["receipt"]["fenced"] and observed["input_state"] == {}
    if action == "delete":
        with pytest.raises(ServiceInvocationConflict):
            prepare_invocation(runtime.owner,job=job(generation=2), surface="knowledge", prompt="new")


def test_cancel_before_prepare_and_cross_user_controls_are_isolated(runtime):
    """准备前取消封锁当前用户且不影响其他创建者"""
    runtime.owner.cancel_invocation(job=job(), surface="knowledge")
    with pytest.raises(ServiceInvocationConflict):
        invoke()
    result = invoke(job("bob"), prompt="B")
    assert runtime.owner.get_invocation(job=job("bob"), surface="knowledge")["receipt"]["message_id"] == result["message_id"]
    assert not runtime.executions.cancelled


@pytest.mark.parametrize("generation", [None, 0, -1, True, "1"])
def test_missing_or_client_shaped_generation_is_rejected(runtime, generation):
    """检索代次缺失、非正整数或类型无效时拒绝调用"""
    selected = job()
    selected["run_generation"] = generation
    with pytest.raises(ValueError):
        invoke(selected)
    assert not runtime.executions.submitted


def test_current_surface_must_match_authoritative_job(runtime):
    """调用页面类型必须与权威检索任务一致"""
    with pytest.raises(ValueError, match="surface"):
        prepare_invocation(runtime.owner,job=job(), surface="wall", prompt="wrong surface")
    assert not runtime.executions.submitted


def test_binding_cannot_override_resident_execution_configuration(runtime):
    """职责绑定不能覆盖 Resident 的执行配置"""
    runtime.binding["runtime_settings"] = {"model": "other"}
    result = invoke()
    assert runtime.executions.requests[result["message_id"]].provider == "approved"


def test_unknown_or_expired_execution_does_not_recreate_submission(runtime):
    """执行历史过期后仍复用已完成消息且不重新提交"""
    invoke()
    runtime.executions.requests.clear()
    assert invoke()["message"] == "private-A"
    assert len(runtime.executions.submitted) == 1
