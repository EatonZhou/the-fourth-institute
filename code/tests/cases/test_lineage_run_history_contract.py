
"""Graph execution history survives definition deletion and transient journals.

:module: lineage.hatg
:design: decision_4637ca4a69e2, decision_0f57656a2acd
"""
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from management_portal.society_service import trigger_paths as paths, trigger_path_observation as observation
from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.society_service import lineage_aggregate
from tests.cases.test_hatg_run_observation import lineage, occurrence, AT


def effect(lid):
    identity, step = paths.effect_started(lid, "activation", {
        "effect_id": "effect_deleted", "started_at": AT, "instruction_id": "instruction_origin"})
    return identity, step


@pytest.mark.parametrize("actual,expected", [("succeeded", "completed"), ("failed", "failed"), ("timed_out", "failed"), ("cancelled", "cancelled")])
def test_effect_outcome_retained_after_graph_deletion(actual, expected):
    """删除 Lineage 后仍能查看 Effect 的成功、失败、超时或取消结果及其指令调用

    :design-point:"""
    lid = lineage()
    store = get_lineage_data_store()
    store.put_lineage_definition(lid, {"lineage_id": lid, "nodes": [], "edges": []}, {})
    identity, step = effect(lid)
    calls = [{"instruction": "CreateResident", "instruction_id": "instruction_1", "status": "committed"}]
    paths.effect_settled(lid, "activation", {"status": actual, "finished_at": AT, "calls": calls,
                                           "outputs": {"resource_id": "resident_created"}})
    store.remove_lineage(lid, deleted_by="sample_admin")
    view = observation.observe(lid, {"trigger_path_id": identity})
    assert view["path"]["status"] == expected
    assert view["steps"]["items"][0]["result"]["calls"] == calls
    assert paths.effect_finished(lid, "activation")["outputs"] == {"resource_id": "resident_created"}


def test_effect_start_and_rollback_do_not_invent_completion():
    """刚开始的 Effect 使触发路径处于进行中，事务回滚后不留下运行记录

    :importance: P1"""
    lid = lineage()
    identity, step = effect(lid)
    assert observation.observe(lid, {"trigger_path_id": identity})["path"]["status"] == "active"
    other = lineage()
    with pytest.raises(RuntimeError):
        with get_lineage_data_store().database.transaction():
            effect(other)
            raise RuntimeError("rollback")
    assert observation.observe(other, {})["paths"] == []


def test_expired_delivery_survives_queue_removal():
    """投递离开等待队列后以失败结算，触发路径记为失败并保留该连线的失败步骤

    :importance: P1"""
    lid = lineage()
    identity, event = occurrence(lid)
    delivery = {"event": event, "cursor": "cursor", "queued_at": AT}
    paths.delivery_waiting(lid, "wire_deleted", delivery)
    paths.event_settled(identity, "source", event["id"])
    paths.delivery_settled(lid, "wire_deleted", delivery, status="failed")
    view = observation.observe(lid, {"trigger_path_id": identity})
    assert view["path"]["status"] == "failed"
    assert any(step["node_id"] == "wire_deleted" and step["status"] == "failed" for step in view["steps"]["items"])


def test_artifact_selector_is_scoped_and_authorized(monkeypatch, tmp_path):
    """触发路径只在有查看权且 activation 匹配时返回 Effect 产物内容，默认观察不含产物

    :design-point:"""
    from management_portal.society_service import effect_artifacts, lineage_domain, lineage_state
    lid = lineage()
    monkeypatch.setenv("PORTAL_EFFECT_ARTIFACT_ROOT", str(tmp_path))
    reference = effect_artifacts.ArtifactStore().put(lid, "activation", {"text": "artifact body"}, purpose="output")
    identity, step = effect(lid)
    paths.effect_settled(lid, "activation", {"status": "succeeded", "finished_at": AT, "outputs": {"artifact": reference}})
    assert "artifact body" not in str(observation.observe(lid, {"trigger_path_id": identity}))
    monkeypatch.setattr(lineage_aggregate, "lineage_scope", lambda _: nullcontext())
    monkeypatch.setattr(lineage_aggregate, "read_lineage", lambda *a, **kw: {})
    def deny(*_):
        raise PermissionError("denied")
    monkeypatch.setattr(lineage_state, "_ensure_visible", deny)
    payload = {"trigger_path_id": identity, "artifact_activation_key": "activation"}
    with pytest.raises(PermissionError):
        lineage_domain.observe_trigger_path(lid, payload, "visitor")
    monkeypatch.setattr(lineage_state, "_ensure_visible", lambda *_: None)
    assert lineage_domain.observe_trigger_path(lid, payload, "reader")["artifact_output"] == {"text": "artifact body"}
    with pytest.raises(KeyError):
        observation.observe(lid, {**payload, "artifact_activation_key": "other"})
    with pytest.raises(KeyError):
        observation.observe(lineage(), payload)


def test_public_path_forwards_only_scoped_selector(monkeypatch):
    """GetTriggerPath 只向领域层转发 trigger_path_id 和产物 activation 选择

    :design-point:"""
    from management_portal.society_service.isa import runs
    lid = lineage()
    arguments = {"lineage_id": lid, "trigger_path_id": "path", "artifact_activation_key": "activation"}
    seen = []
    def observe(lineage_id, payload, caller):
        seen.append((lineage_id, payload, caller))
        return {"path": {"trigger_path_id": "path"}, "artifact_output": {"real": "output"}}
    monkeypatch.setattr(runs, "domain", lambda: SimpleNamespace(observe_trigger_path=observe))
    assert runs.get_trigger_path(SimpleNamespace(argument=arguments.get, caller="reader"))["artifact_output"] == {"real": "output"}
    assert seen == [(lid, {"trigger_path_id": "path", "artifact_activation_key": "activation"}, "reader")]
