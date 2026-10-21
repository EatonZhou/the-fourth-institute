"""Real ISA admission and application contracts; no live model or graph writes."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal import isa_client, system_application_flows as flows
from management_portal import notice_board_classifier as classifier
from management_portal import resident_recommendation_backend as recommendation
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service.isa import build_kernel
from management_portal.lineage_workflows import WorkflowBindings, WorkflowPending, WorkflowFailed
from tests.cases import society_service_test_support as support
from tests.cases.test_isa_audit_g4 import make_lineage, registered_members
from tests.cases.test_lineage_generic_primitives import primitive_store, ACTOR, WORK_A


def call(actor, instruction, arguments=None, **options):
    return build_kernel().execute(InstructionCall.from_payload({
        "instruction": instruction, "arguments": arguments or {},
        **({"idempotency_key": options["key"]} if options.get("key") else {}),
    }), InstructionContext(caller=actor, entry=options.get("entry", "web"))).raise_for_error()


@pytest.fixture
def application_graph(primitive_store, monkeypatch):
    lineage_id = make_lineage()["lineage_id"]
    target = {"lineage_id": lineage_id, "resident_id": WORK_A}
    for application in flows.APPLICATIONS:
        call(ACTOR, "ApplyLineageChanges", {"lineage_id": lineage_id,
            "operations": flows.migration_operations(application, WORK_A)})
    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(flows.application_targets, "resident", lambda _: target)
    sent = []
    def send(node, prompt, actor, **kwargs):
        message_id = f"msg_{len(sent) + 1:032x}"
        sent.append({"resident_id": node["id"], "prompt": prompt, "actor": actor, "message_id": message_id, **kwargs})
        return {"message_id": message_id}
    # The sink stands in for Resident admission, so GetInputState answers for the inputs it took.
    stored_state = support.resident_protocol.get_resident_input_state
    def input_state(resident_id, actor, message_id, **options):
        if any(item["resident_id"] == resident_id and item["message_id"] == message_id for item in sent):
            return {"message_id": message_id, "state": "queued"}
        return stored_state(resident_id, actor, message_id, **options)
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(support.resident_protocol, "get_resident_input_state", input_state)
    return target, sent


@pytest.mark.parametrize("application", flows.APPLICATIONS)
def test_real_event_effect_admission_and_replay(application_graph, application):
    """各应用流程经事件源与 Effect 只投递一次，重复请求不重投，组件暂停时拒绝新请求"""
    target, sent = application_graph
    flow = flows.binding(application, actor=ACTOR)
    parts = [{"type": "context_reference", "context": {"resident_routing": {
        "query": "a task", "request_id": "request1", "limit": 1,
        "candidates": [{"resident_id": WORK_A}],
    }}}]
    first = flows.submit(application, actor=ACTOR, prompt="request", request_id="request1", content_parts=parts)
    assert first["message_id"] and first["trigger_path_id"] and first["event_id"]
    assert len(sent) == 1 and "request" in sent[0]["prompt"]
    assert sent[0]["actor"] == ACTOR
    # Duplicate observation must reuse the same Resident input, including after
    # the original TriggerEvent acknowledgement has become a reference receipt.
    repeated = flows.submit(application, actor=ACTOR, prompt="request", request_id="request1", content_parts=parts)
    assert repeated["message_id"] == first["message_id"] and len(sent) == 1
    for role in ("event_source_id", "effect_id"):
        call(ACTOR, "PauseComponent", {"lineage_id": target["lineage_id"], "component_id": flow[role], "paused": True})
        with pytest.raises(flows.ApplicationFlowUnavailable):
            flows.submit(application, actor=ACTOR, prompt="disabled", request_id="new-" + role)
        assert len(sent) == 1
        call(ACTOR, "PauseComponent", {"lineage_id": target["lineage_id"], "component_id": flow[role], "paused": False})
    flows.submit(application, actor=ACTOR, prompt="open", request_id="request2")
    assert len(sent) == 2


def test_effect_preserves_candidate_context_and_authenticated_service(application_graph, monkeypatch):
    """Effect 投递保留候选 Resident 上下文，system 入口以 portal 身份投递"""
    target, sent = application_graph
    received = []
    original = support.resident_protocol.deliver_resident_input
    def capture(payload, actor, **kwargs):
        received.append((payload, actor))
        return original(payload, actor, **kwargs)
    monkeypatch.setattr(support.resident_protocol, "deliver_resident_input", capture)
    context = {"type": "context_reference", "context": {"resident_routing": {
        "request_id": "stable", "query": "only this", "limit": 1,
        "candidates": [{"resident_id": WORK_A, "title": "allowed"}],
    }}}
    flows.submit("resident_discovery", actor=ACTOR, prompt="route", request_id="candidate-context", content_parts=[context])
    assert context in received[0][0]["message"]["content_parts"]
    flows.submit("notice_classifier", actor="portal", entry="system", prompt="classify", request_id="service")
    assert str(received[1][1]) == "portal"


def test_binding_fails_closed_on_retarget_disconnect_and_survives_rename(application_graph):
    """应用流程绑定在组件改名后不变，目标 Resident 变更或连线断开时拒绝使用"""
    target, sent = application_graph
    graph = call(ACTOR, "GetLineage", {"lineage_id": target["lineage_id"]})["lineage"]
    original = flows.component_binding("notice_classifier", target, graph)
    for item in graph["program"]["components"]:
        item["title"] = "用户改名"
    assert flows.component_binding("notice_classifier", target, graph) == original
    with pytest.raises(flows.ApplicationFlowUnavailable):
        flows.component_binding("notice_classifier", {**target, "resident_id": "resident_bbbbbbbbbbbb"}, graph)
    broken = deepcopy(graph)
    broken["program"]["wires"] = [w for w in broken["program"]["wires"] if w["source"] != original["event_source_id"]]
    with pytest.raises(flows.ApplicationFlowUnavailable):
        flows.component_binding("notice_classifier", target, broken)
    assert not sent


def test_classifier_pause_retains_budget_and_service_identity(monkeypatch):
    """流程关闭时公告分类保持待处理、不计尝试次数，并沿用原请求 ID 和 portal 身份"""
    source = {"post_id": "notice_test", "fingerprint": "unchanged", "category": "other", "title": "title", "content": "body"}
    worker = classifier.NoticeBoardClassifier(SimpleNamespace(classification_source=lambda _: source))
    updates, submitted = [], []
    monkeypatch.setattr(worker, "_update_item", lambda *args, **kwargs: updates.append(kwargs))
    def submit(*args, **kwargs):
        submitted.append(kwargs)
        raise flows.ApplicationFlowUnavailable("closed")
    monkeypatch.setattr(flows, "submit", submit)
    item = {"post_id": source["post_id"], "source_fingerprint": source["fingerprint"], "attempts": 4}
    worker._submit(item)
    worker._submit(item)
    assert all(u["status"] == "pending" and "attempts" not in u for u in updates)
    assert all(s["actor"] == "portal" and s["entry"] == "system" for s in submitted)
    assert submitted[0]["request_id"] == submitted[1]["request_id"]


def test_recommendation_paused_graph_uses_authorized_directory_without_invocation(application_graph, monkeypatch):
    """推荐流程暂停时按用户可见的 Resident 目录直接推荐，不调用 Resident"""
    target, sent = application_graph
    monkeypatch.setattr(recommendation, "_recommendation_resident_binding", lambda actor: target)
    monkeypatch.setattr(recommendation, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(
        get_resident_conversation_snapshot=lambda *_: {"state": {"intake_state": "open"}})))
    monkeypatch.setattr(recommendation, "get_portal_ui_state_store", lambda: SimpleNamespace(put=lambda *args: None, get=lambda *args: None))
    flow = flows.binding("resident_discovery", actor=ACTOR)
    call(ACTOR, "PauseComponent", {"lineage_id": target["lineage_id"], "component_id": flow["effect_id"], "paused": True})
    class Directory:
        def routing_candidates(self, **kwargs):
            assert kwargs["actor"] == ACTOR
            return {"results": [{"resident_id": WORK_A, "title": "allowed"}]}
    response = recommendation.start_recommendation(actor=ACTOR, query="a task", client=Directory())
    assert response["status"] == "completed" and response["result"]["fallback"]
    assert response["result"]["recommendations"][0]["resident_id"] == WORK_A
    assert not sent


def test_close_after_read_keeps_one_event_and_recovery_observes_only(application_graph, monkeypatch):
    """读取绑定后 Effect 被暂停时只产生一个事件，恢复组件后才投递一次"""
    from management_portal.society_service import lineage_domain
    target, sent = application_graph
    flow = flows.binding("notice_classifier", actor=ACTOR)
    event_ids = []
    def close_before_trigger(actor, instruction, arguments=None, **options):
        if instruction == "TriggerEvent":
            call(ACTOR, "PauseComponent", {"lineage_id": target["lineage_id"], "component_id": flow["effect_id"], "paused": True})
        result = call(actor, instruction, arguments, **options)
        if instruction == "TriggerEvent":
            event_ids.append(result["event_id"])
        return result
    monkeypatch.setattr(isa_client, "call", close_before_trigger)
    with pytest.raises(WorkflowPending) as pending:
        flows.submit("notice_classifier", actor=ACTOR, prompt="one request", request_id="race")
    handle = pending.value.handle
    for _ in range(2):
        with pytest.raises(WorkflowPending):
            WorkflowBindings().observe(handle, actor=ACTOR)
        lineage_domain.reconcile_lineage_runtime(target["lineage_id"], ACTOR, recovery_only=True)
    assert not sent and event_ids == [handle["event_id"]]
    monkeypatch.setattr(isa_client, "call", call)
    call(ACTOR, "PauseComponent", {"lineage_id": target["lineage_id"], "component_id": flow["effect_id"], "paused": False})
    lineage_domain.reconcile_lineage_runtime(target["lineage_id"], ACTOR, recovery_only=True)
    recovered = WorkflowBindings().observe(handle, actor=ACTOR)
    assert recovered["message_id"] and recovered["event_id"] == handle["event_id"]
    assert len(sent) == 1


def test_service_entry_is_preserved_on_trigger_and_observation(monkeypatch):
    """触发事件和观察触发路径时都保留 system 入口"""
    seen = []
    handle = {"lineage_id": "lineage_a", "resident_id": "resident_a", "event_source_id": "event_a", "effect_id": "effect_a"}
    def invoke(actor, instruction, args, **options):
        seen.append((actor, instruction, options["entry"]))
        if instruction == "TriggerEvent":
            return {"event_id": "event1", "trigger_path_id": "path1", "delivery": {
                "results": [{"target_id": "effect_a", "status": "paused"}]}}
        return {"steps": {"items": [], "next_offset": None}}
    monkeypatch.setattr(isa_client, "call", invoke)
    with pytest.raises(WorkflowPending) as pending:
        WorkflowBindings().trigger(handle, actor="portal", prompt="input", request_id="id", entry="system")
    assert seen == [("portal", "TriggerEvent", "system"), ("portal", "GetTriggerPath", "system")]
    with pytest.raises(WorkflowPending):
        WorkflowBindings().observe(pending.value.handle, actor="portal", entry="system")
    assert seen[2:] == [("portal", "GetTriggerPath", "system")]


def test_pending_recommendation_retry_and_refresh_reuse_original_request(monkeypatch):
    """待定的 Resident 推荐在重试和刷新时复用原始请求，不重复提交"""
    stored = {}
    class Store:
        def put(self, actor, namespace, key, value):
            stored[actor] = {"value": value}
        def get(self, actor, namespace, key):
            return stored.get(actor)
    monkeypatch.setattr(recommendation, "get_portal_ui_state_store", Store)
    monkeypatch.setattr(recommendation, "_available_recommendation_resident_binding", lambda _: {
        "resident_id": WORK_A, "lineage_id": "lineage_a", "event_source_id": "event_a", "effect_id": "effect_a"})
    submitted = []
    def submit(**kwargs):
        submitted.append(kwargs)
        raise WorkflowPending(kwargs["workflow_binding"], {"event_id": "event1", "trigger_path_id": "path1"})
    monkeypatch.setattr(recommendation, "submit_invocation", submit)
    class Directory:
        def routing_candidates(self, **kwargs):
            return {"results": [{"resident_id": WORK_A}]}
    def observe(self, handle, **kwargs):
        raise WorkflowPending(handle, handle)
    monkeypatch.setattr(WorkflowBindings, "observe", observe)
    for query in ("original", "retry"):
        with pytest.raises(recommendation.ResidentDiscoveryUnavailable, match="event1"):
            recommendation.start_recommendation(actor=ACTOR, query=query, client=Directory())
    assert len(submitted) == 1
    monkeypatch.setattr(WorkflowBindings, "observe", lambda self, handle, **kwargs: {**handle, "message_id": "msg1"})
    recovered = recommendation.latest_recommendation(actor=ACTOR)
    assert recovered["status"] == "accepted" and recovered["query"] == "original"
    assert recovered["message_id"] == "msg1" and recovered["event_id"] == "event1"


def test_failed_path_stays_pending_and_rejected_submission_is_terminal(monkeypatch):
    """Effect 失败的触发路径不能证明未投递，保持待确认；原指令明确未提交时工作流失败，公告分类记录拒绝原因"""
    binding = {"lineage_id": "lineage_a", "resident_id": WORK_A, "event_source_id": "event_a", "effect_id": "effect_a"}
    failed_path = {**binding, "event_id": "event1", "trigger_path_id": "path1"}
    def read_failed_path(actor, instruction, args, **options):
        assert instruction == "GetTriggerPath"
        return {"path": {"trigger_path_id": "path1", "lineage_id": "lineage_a", "root_kind": "event",
                         "root_node_id": "event_a", "occurrence_id": "event1"},
                "steps": {"items": [{"step_id": "step1", "kind": "effect", "node_id": "effect_a", "status": "failed"}],
                          "next_offset": None},
                "edges": {"items": [], "next_offset": None}}
    monkeypatch.setattr(isa_client, "call", read_failed_path)
    with pytest.raises(WorkflowPending, match="path1|event1"):
        WorkflowBindings().observe(failed_path, actor=ACTOR)

    rejected = {**binding, "instruction": "TriggerEvent", "instruction_id": "inst_lin_rejected"}
    seen = []
    def read_rejection(actor, instruction, args, **options):
        seen.append(instruction)
        if instruction == "GetInstructionExecution":
            return {"instruction_id": "inst_lin_rejected", "instruction": "TriggerEvent", "status": "failed",
                    "error": {"code": "component.paused", "message": "event source paused",
                              "commit_state": "not_committed"}}
        return {"paths": []}
    monkeypatch.setattr(isa_client, "call", read_rejection)
    with pytest.raises(WorkflowFailed, match="event source paused"):
        WorkflowBindings().observe(rejected, actor=ACTOR)
    worker = classifier.NoticeBoardClassifier(SimpleNamespace())
    changes = []
    monkeypatch.setattr(worker, "_update_item", lambda *args, **kwargs: changes.append(kwargs))
    worker._observe_workflow({"post_id": "notice1", "workflow_handle": rejected})
    assert changes[0]["status"] == "failed" and "event source paused" in changes[0]["last_error"]
    assert seen == ["GetInstructionExecution", "ListTriggerPaths"] * 2


def test_delivery_before_later_script_failure_remains_observable(application_graph):
    """Effect 脚本投递后再失败时，已投递的消息仍可观察且不重投"""
    target, sent = application_graph
    flow = flows.binding("resident_discovery", actor=ACTOR)
    from management_portal.society_service.lineage_effect_script import deliver_message_script
    call(ACTOR, "UpdateComponent", {"lineage_id": target["lineage_id"], "component_id": flow["effect_id"],
        "changes": {"script": deliver_message_script("") + '\nfail "later instruction failed"'}})
    receipt = flows.submit("resident_discovery", actor=ACTOR, prompt="once", request_id="partial")
    assert receipt["message_id"] and len(sent) == 1
    assert WorkflowBindings().observe(receipt, actor=ACTOR)["message_id"] == receipt["message_id"]
    assert len(sent) == 1
