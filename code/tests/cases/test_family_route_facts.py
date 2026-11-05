"""Router input uses current, scoped facts without per-member tool round trips.

:module: lineage.team
:design: decision_44cf587c1721
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    family_route_facts, family_task_dispatches, family_tasks, lineage_graph_state,
    resident_access, resident_catalog, resident_conversation_stream,
    resident_input_queue_domain, resident_intake_domain,
)
from management_portal.society_service.resident_record_domain import find_resident
from management_portal.society_service.resident_intake_domain import deliver_resident_message_content
from management_portal.society_service.resident_context.store import ResidentContextStore
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.isa_test_support import ACTOR, ok, run
from tests.cases.test_family_tasks import (  # noqa: F401
    charters, configured_family, future_creation_store, lineage,
)


@pytest.fixture
def route_world(configured_family, monkeypatch):
    body, block = configured_family
    family = block["family"]
    store = ResidentContextStore()
    records = resident_catalog.get_catalog().get_identity_records([*family["members"], family["steward"]])
    store.upsert_catalog([{**record, "global_summary_managed": False} for record in records])
    for index, rid in enumerate(family["members"]):
        store.write_work_summary(rid, rid,
            {"primary_ownership": [f"职责-{index}"], "route_when": [f"任务-{index}"],
             "avoid_when": ["无关请求"], "current_focus": "正在处理分配任务"}, [], 0, {})
    monkeypatch.setattr(family_route_facts, "resident_context", lambda: SimpleNamespace(store=store))
    return body, block, store


def _request(family, mid="msg_10000000000000000000000000000001"):
    message = resident_input_queue_domain.build_message("user", "核对执行调度问题 ${facts}", ACTOR, message_id=mid)
    resident_conversation_stream.get_repository().accept_user_message(family["facade"], message)
    return {"resident_id": family["facade"], "trigger": "route", "source_message_id": mid,
            "message_id": "msg_20000000000000000000000000000001"}


def _intent(family, mid):
    with get_lineage_data_store().database.read() as connection:
        row = connection.execute("SELECT intent_json FROM family_task_dispatches WHERE message_id=%s", (mid,)).fetchone()
    return json.loads(row["intent_json"]) if isinstance(row["intent_json"], str) else row["intent_json"]


def test_current_router_facts_batch_only_direct_allowed_scope(route_world, monkeypatch):
    _, block, store = route_world
    family = block["family"]
    a, b = family["members"]
    from management_portal.society_service import family_chat_preferences
    family_chat_preferences.change(family["facade"], ACTOR, b, "mute", True)
    calls = {"identity": [], "documents": [], "states": []}
    catalog = resident_catalog.get_catalog()
    repository = resident_conversation_stream.get_repository()
    def counted(owner, name, key):
        original = getattr(owner, name)
        def read(ids, *args, **kwargs):
            calls[key].append(list(ids))
            return original(ids, *args, **kwargs)
        monkeypatch.setattr(owner, name, read)
    counted(catalog, "get_identity_records", "identity")
    counted(store, "documents", "documents")
    counted(repository, "states", "states")
    args = _request(family)
    ok("DispatchFamilyTask", args)
    intent = _intent(family, args["message_id"])
    facts = intent["rendered_facts"]
    candidates = facts["routing"]["candidates"]
    assert [c["resident_id"] for c in candidates] == [a, family["steward"]]
    assert candidates[0]["summary"]["primary_ownership"] == ["职责-0"]
    assert candidates[0]["summary_revision"] == 1
    assert candidates[0]["work_state"] == "idle"
    assert candidates[0]["accepts_images"] is True
    assert candidates[1]["summary_stale"] is True and candidates[1]["summary"] == {}
    assert calls["documents"] == [[a, family["steward"]]]
    assert calls["states"] == [[a, family["steward"]]]
    assert calls["identity"].count([a, family["steward"]]) == 1
    assert facts["content"][0]["text"] == "核对执行调度问题 ${facts}"
    assert "candidates" not in intent["request"]["facts"]["routing"]
    assert family_tasks.ROUTER_FACTS_CONTRACT in intent["text"]


def test_scoped_projection_keeps_unknown_and_does_not_query_hidden_members(route_world, monkeypatch):
    _, block, store = route_world
    family = block["family"]
    a, b = family["members"]
    original = resident_access.visible_resident_records
    actors = []
    def visible(ids, actor, service_actor):
        actors.append(actor)
        return [record for record in original(ids, actor, service_actor) if record["resident_id"] != b]
    monkeypatch.setattr(resident_access, "visible_resident_records", visible)
    documents = store.documents
    selected = []
    monkeypatch.setattr(store, "documents", lambda ids: selected.append(list(ids)) or documents(ids))
    args = _request(family)
    ok("DispatchFamilyTask", args)
    facts = _intent(family, args["message_id"])["rendered_facts"]["routing"]
    assert actors == [family["router"]]
    assert b not in selected[0]
    assert facts["candidates"][1] == {"resident_id": b, "role": "member", "availability": "unavailable"}


def test_retry_freezes_summary_and_busy_facts_and_does_not_resample(route_world, monkeypatch):
    _, block, _ = route_world
    family = block["family"]
    monkeypatch.setattr(resident_intake_domain, "deliver_resident_message_content", deliver_resident_message_content)
    args = _request(family)
    ok("DispatchFamilyTask", args)
    before = _intent(family, args["message_id"])
    monkeypatch.setattr(family_route_facts, "snapshot", lambda *a: pytest.fail("replay resampled volatile facts"))
    ok("DispatchFamilyTask", args, actor=family["steward"])
    assert _intent(family, args["message_id"]) == before
    failed = run("DispatchFamilyTask", {**args, "facts": {"purpose": "changed"}})
    assert failed.status == "failed"
    assert _intent(family, args["message_id"]) == before


def test_concurrent_route_preparation_samples_once(route_world, monkeypatch):
    _, block, _ = route_world
    family = block["family"]
    monkeypatch.setattr(resident_intake_domain, "deliver_resident_message_content", deliver_resident_message_content)
    args = _request(family)
    original = family_route_facts.snapshot
    reads = []
    monkeypatch.setattr(family_route_facts, "snapshot", lambda *a: reads.append(1) or original(*a))
    barrier = Barrier(2)
    def dispatch(actor):
        barrier.wait(timeout=10)
        return ok("DispatchFamilyTask", args, actor=actor)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(dispatch, actor) for actor in (family["router"], ACTOR)]
        assert all(f.result(timeout=30)["message_id"] == args["message_id"] for f in futures)
    assert reads == [1]


def test_fact_budget_and_correction_excerpt_are_explicit(route_world):
    body, block, store = route_world
    family = block["family"]
    a = family["members"][0]
    store.write_work_summary(a, a, {"primary_ownership": ["责任" * 500], "route_when": ["触发" * 500]}, [], 1, {})
    lineage_graph_state.update_cell(body["lineage_id"], "family_corrections_" + family["facade"], [
        {"from": {"resident_id": a}, "to": {"resident_id": family["members"][1]},
         "request": "更正" * 200, "reason": "理由" * 200} for _ in range(6)])
    args = _request(family)
    ok("DispatchFamilyTask", args)
    facts = _intent(family, args["message_id"])["rendered_facts"]["routing"]
    assert facts["candidates"][0]["summary_truncated"] is True
    assert len(facts["corrections"]) == 4 and facts["corrections_truncated"] is True
    assert len(facts["corrections"][0]["reason"]) == 240


def test_enriched_route_still_uses_custom_prompt_and_guide(route_world):
    body, block, _ = route_world
    wire_id = next(e["id"] for e in block["elements"] if e["key"] == "route")
    wire = next(w for w in body["program"]["wires"] if w["id"] == wire_id)
    profiles = copy.deepcopy(wire["config"]["invoke_profiles"])
    route = profiles["effect"]["arguments"]["family_tasks"]["value"]["route"]
    route.update(guide="保留特定职责边界", prompt="自定义选择 ${facts} 见 ${guide_path}")
    ok("ApplyLineageChanges", {"lineage_id": body["lineage_id"], "operations": [
        {"op": "update", "kind": "wire", "id": wire_id, "changes": {"config.invoke_profiles": profiles}}]})
    args = _request(block["family"])
    ok("DispatchFamilyTask", args)
    text = _intent(block["family"], args["message_id"])["text"]
    assert text.startswith("自定义选择") and "primary_ownership" in text
    assert "保留特定职责边界" in family_tasks.guide(find_resident(block["family"]["router"]))[2]


def test_nested_family_exposes_only_direct_facade_not_inner_duties(route_world, monkeypatch):
    body, block, store = route_world
    family = block["family"]
    inner_member = family["members"][0]
    nested = ok("Familize", {"lineage_id": body["lineage_id"], "resident_ids": [inner_member]})
    records = resident_catalog.get_catalog().get_identity_records([nested["facade_resident_id"]])
    store.upsert_catalog([{**record, "global_summary_managed": False} for record in records])
    store.write_work_summary(nested["facade_resident_id"], "portal",
                             {"primary_ownership": ["子团队公开职责"]}, [], 0, {})
    documents = store.documents
    queried = []
    monkeypatch.setattr(store, "documents", lambda ids: queried.extend(ids) or documents(ids))
    args = _request(family)
    ok("DispatchFamilyTask", args)
    facts = _intent(family, args["message_id"])["rendered_facts"]["routing"]
    assert nested["facade_resident_id"] in queried and inner_member not in queried
    assert inner_member not in [row["resident_id"] for row in facts["candidates"]]
    facade = next(row for row in facts["candidates"] if row["resident_id"] == nested["facade_resident_id"])
    assert facade["summary"]["primary_ownership"] == ["子团队公开职责"]


def test_optional_corrections_denial_is_unknown_not_broader_service_read(route_world, monkeypatch):
    _, block, _ = route_world
    family = block["family"]
    from management_portal.society_service import lineage_domain
    actors = []
    def deny(lineage_id, payload, actor):
        actors.append(actor)
        raise PermissionError("private cell not readable")
    monkeypatch.setattr(lineage_domain, "get_lineage_graph_state", deny)
    args = _request(family)
    ok("DispatchFamilyTask", args)
    facts = _intent(family, args["message_id"])["rendered_facts"]["routing"]
    assert facts["corrections_available"] is False and facts["corrections"] == []
    assert actors == [family["router"]]
