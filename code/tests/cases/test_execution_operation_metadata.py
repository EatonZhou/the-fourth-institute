"""Durable submission semantics survive queue retention without keeping request bodies."""
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from management_common.json_columns import json_value
from management_portal.execution.agent_data_store import AgentDataStore, get_agent_data_store
from management_portal.execution import execution_inputs
from management_portal.execution.contracts import ExecutionRequest, ExecutionResult, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.service import ExecutionService, ExecutionServiceServer


ACTOR = "alice"
AGENT = "resident_aaaaaaaaaaaa"
SECRET = "private-request-body-must-not-survive"


def request(operation="turn"):
    native = operation != "turn"
    return ExecutionRequest.new(actor=ACTOR, agent_id=AGENT, prompt=SECRET,
        service_type="agent_session", provider="codex", operation=operation,
        metadata={"message_id": "msg_untrusted_metadata", "private": SECRET},
        session=SessionOptions(mode="persistent" if native else "stateless",
                               binding_id="fixture:target" if native else "",
                               context_source_binding_id="fixture:source" if operation == "fork" else ""))


def service(store):
    server = SimpleNamespace(agent_data=store)
    return ExecutionService(client=SimpleNamespace(request=lambda method, params:
        ExecutionServiceServer.dispatch(server, method, params)))


@pytest.fixture
def stack():
    store = get_agent_data_store()
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    return store, scheduler


def complete(store, scheduler, req):
    scheduler.register_worker(worker_id="fixture-worker", build_id="fixture-build", capabilities=[req.capability], state="standby")
    scheduler.activate_worker("fixture-worker")
    claimed = scheduler.claim(worker_id="fixture-worker", capabilities=[req.capability])
    assert claimed["claimed"] and claimed["execution"]["execution_id"] == req.execution_id
    token = claimed["claim_token"]
    scheduler.start(req.execution_id, worker_id="fixture-worker", claim_token=token)
    store.begin_execution(execution_id=req.execution_id, agent_id=AGENT, actor_ref=ACTOR,
                          provider=req.provider, started_at="2026-10-16T03:00:00Z")
    scheduler.finish(ExecutionResult(execution_id=req.execution_id, status="completed", output=""),
                     worker_id="fixture-worker", claim_token=token)


@pytest.mark.parametrize("operation", ["turn", "fork", "create", "compact", "release"])
def test_submission_record_survives_ack_reconstruction_and_scheduler_prune(stack, operation):
    """执行提交记录在确认清空请求体和调度器清理后仍保留操作、时间与输入，不含请求正文"""
    store, scheduler = stack
    req = request(operation)
    if operation == "turn":
        store.reserve_execution(execution_id=req.execution_id, agent_id=AGENT, actor_ref=ACTOR, provider="codex")
        execution_inputs.attach(store, req.execution_id, [{"message_id": "msg_real_input", "role": "primary"}])
        preparing = service(store).record(req.execution_id, actor=ACTOR)
        assert preparing["status"] == "preparing" and preparing["operation"] is None
        assert preparing["inputs"][0]["message_id"] == "msg_real_input"
    scheduler.submit(req)
    before = service(store).record(req.execution_id, actor=ACTOR)
    assert before["operation"] == operation and before["service_type"] == "agent_session" and before["submitted_at"]
    assert [item["message_id"] for item in before["inputs"]] == (["msg_real_input"] if operation == "turn" else [])
    assert "msg_untrusted_metadata" not in json.dumps(before)
    complete(store, scheduler, req)
    scheduler.acknowledge(req.execution_id, actor=ACTOR)
    scheduler.acknowledge(req.execution_id, actor=ACTOR)
    cleaned = scheduler.snapshot(req.execution_id)
    assert cleaned["request"] == {}
    with scheduler.database.read() as connection:
        transient = connection.execute("SELECT request_json,checkpoint_json FROM executions WHERE execution_id=%s",
                                       (req.execution_id,)).fetchone()
        assert json_value(transient["request_json"]) == {} and not json_value(transient["checkpoint_json"] or "{}")
    assert scheduler.prune_terminal(older_than_epoch=time.time() + 86401) == 1
    with pytest.raises(KeyError):
        Scheduler().snapshot(req.execution_id)
    rebuilt = AgentDataStore()
    after = service(rebuilt).record(req.execution_id, actor=ACTOR)
    assert after["status"] == "completed"
    assert {k:after[k] for k in ("service_type","operation","submitted_at","inputs")} == {
        k:before[k] for k in ("service_type","operation","submitted_at","inputs")}
    with rebuilt.database.read() as connection:
        fact = json_value(connection.execute("SELECT data_json FROM agent_operations WHERE operation_id=%s",
            ("execution_submission:" + req.execution_id,)).fetchone()["data_json"])
    assert set(fact) == {"schema","execution_id","service_type","operation","request_digest"}
    assert SECRET not in json.dumps(fact) and len(fact["request_digest"]) == 64
    with pytest.raises(KeyError):
        service(rebuilt).record(req.execution_id, actor="bob")
    with pytest.raises(ValueError):
        service(rebuilt).record(req.execution_id, actor="")
    with pytest.raises(ValueError, match="already submitted"):
        Scheduler().submit(req, prepare=lambda _: pytest.fail("retired receipt replayed preparation"))


def test_idempotent_submission_and_conflict_do_not_rewrite_facts_or_prepare(stack):
    """重复提交同一执行不重新准备也不改写提交记录，内容不同的提交被拒绝"""
    store, scheduler = stack
    req = request()
    preparations = []
    scheduler.submit(req, prepare=lambda value: preparations.append(value.execution_id) or value)
    first = service(store).record(req.execution_id, actor=ACTOR)
    scheduler.submit(req, prepare=lambda _: pytest.fail("duplicate prepared"))
    with pytest.raises(ValueError, match="different request"):
        scheduler.submit(replace(req, prompt="different"), prepare=lambda _: pytest.fail("conflict prepared"))
    assert preparations == [req.execution_id] and service(store).record(req.execution_id, actor=ACTOR) == first


def test_admission_operation_failure_rolls_back_queue_and_long_term_fact(stack, monkeypatch):
    """写入提交记录后入队失败时，队列项和长期提交记录一起回滚"""
    store, scheduler = stack
    req = request()
    original = AgentDataStore.record_execution_submission_in
    def fail(connection, value, **kwargs):
        original(connection, value, **kwargs)
        raise RuntimeError("after durable operation insert, before queue commit")
    monkeypatch.setattr(AgentDataStore, "record_execution_submission_in", staticmethod(fail))
    with pytest.raises(RuntimeError, match="before queue commit"):
        scheduler.submit(req)
    assert store.execution(req.execution_id) is None
    with pytest.raises(KeyError):
        scheduler.snapshot(req.execution_id)
    with store.database.read() as connection:
        assert not connection.execute("SELECT 1 FROM agent_operations WHERE operation_id=%s",
                                      ("execution_submission:" + req.execution_id,)).fetchone()


def test_invalid_request_and_failed_preparation_never_create_submission_fact(stack):
    """非法请求或准备失败时不生成执行提交记录"""
    store, scheduler = stack
    req = request()
    with pytest.raises(ValueError):
        scheduler.submit(replace(req, operation="invalid"))
    def fail(_):
        raise RuntimeError("preparation failed")
    with pytest.raises(RuntimeError, match="preparation failed"):
        scheduler.submit(req, prepare=fail)
    assert store.execution(req.execution_id) is None


def test_legacy_cleaned_record_remains_unknown_and_is_not_resubmitted(stack):
    """旧执行没有提交记录时操作类型保持未知，不能再次提交"""
    store, scheduler = stack
    req = request("fork")
    store.begin_execution(execution_id=req.execution_id, agent_id=AGENT, actor_ref=ACTOR,
                          provider="codex", started_at="2026-10-16T03:00:00Z")
    store.finish_execution(req.execution_id, status="completed")
    legacy = service(AgentDataStore()).record(req.execution_id, actor=ACTOR)
    assert legacy["operation"] is None and legacy["service_type"] is None and legacy["submitted_at"] is None
    assert legacy["inputs"] == [] and legacy["provider"] == "codex"
    with pytest.raises(ValueError, match="without an available admission receipt"):
        scheduler.submit(req, prepare=lambda _: pytest.fail("legacy operation inferred"))
    assert service(store).record(req.execution_id, actor=ACTOR) == legacy
