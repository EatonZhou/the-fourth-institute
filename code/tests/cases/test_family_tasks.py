"""Persistent Family roles read editable task contracts from their executing graph.

:module: lineage.team
:design: decision_44cf587c1721
"""
import copy
import json
from pathlib import Path

import pytest

from management_portal.society_service import family_tasks, resident_execution_domain, resident_intake_domain
from management_portal.society_service.resident_execution_domain import deliver_resident_message_content
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.resident_workspace_domain import _find_resident
from management_portal.society_service.templates import familize
from tests.cases.isa_test_support import ACTOR, ok
from tests.cases.test_family_familize import lineage, charters, future_creation_store, resident, graph  # noqa: F401


@pytest.fixture
def configured_family(lineage, charters):
    result = ok("Familize", {"lineage_id": lineage, "resident_ids": [resident(lineage, "A"), resident(lineage, "B")]})
    body = graph(lineage)
    block = next(b for b in body["function_blocks"] if b["id"] == result["family_id"])
    return body, block


def test_saved_guide_prompt_and_event_survive_roster_sync(configured_family, monkeypatch):
    body, block = configured_family
    ids = {e["key"]: e["id"] for e in block["elements"]}
    route = next(w for w in body["program"]["wires"] if w["id"] == ids["route"])
    profiles = copy.deepcopy(route["config"]["invoke_profiles"])
    task = profiles["effect"]["arguments"]["family_tasks"]["value"]["route"]
    task.update(guide="自定义路由指南", prompt="定制任务 ${facts} 阅读 ${guide_path} ${guide_version}")
    ok("ApplyLineageChanges", {"lineage_id": body["lineage_id"], "operations": [
        {"op": "update", "kind": "wire", "id": route["id"], "changes": {"config.invoke_profiles": profiles}},
        {"op": "update", "kind": "component", "id": ids["periodic_review"], "changes": {"config.interval_seconds": 7200}},
    ]})
    familize.sync_generated_structure(body["lineage_id"])
    current, current_block = family_tasks.context(_find_resident(block["family"]["router"]))
    assert family_tasks.configured(current, current_block)["route"]["guide"] == "自定义路由指南"
    assert next(c for c in current["program"]["components"] if c["id"] == ids["periodic_review"])["config"]["interval_seconds"] == 7200
    target = _find_resident(block["family"]["router"])
    text = family_tasks.render(target, "route", {"message_id": "original"})
    assert text.startswith("定制任务") and "自定义路由指南" not in text
    path, _, content = family_tasks.guide(target)
    assert Path(path).read_text() == content and "自定义路由指南" in content
    ok("UpdateFamily", {"lineage_id": body["lineage_id"], "family_id": block["id"],
                        "review_interval_seconds": 3600})
    refreshed, _ = family_tasks.context(target)
    assert next(c for c in refreshed["program"]["components"] if c["id"] == ids["periodic_review"])["config"]["interval_seconds"] == 3600


def test_router_task_keeps_sender_original_media_and_request_binding(configured_family, monkeypatch):
    _, block = configured_family
    family = block["family"]
    mid = "msg_0123456789abcdef0123456789abcdef"
    message = resident_intake_domain._message("user", "本轮原文 ${facts}", "sample_admin", message_id=mid)
    message["content_parts"].append({"type": "file", "resource_id": "file_original"})
    quote = {"message_id": "msg_00000000000000000000000000000012",
             "author_id": family["facade"], "text": "已修好，引用原文 ${facts}"}
    message["quote"] = quote
    resident_conversation_stream.get_repository().accept_user_message(family["facade"], message)
    deliveries = []
    def deliver(rid, actor, text, **options):
        deliveries.append((rid, actor, text, options))
        return {"message_id": "msg_1123456789abcdef0123456789abcdef"}
    monkeypatch.setattr(resident_execution_domain, "deliver_resident_message_content", deliver)
    ok("DispatchFamilyTask", {"resident_id": family["facade"], "trigger": "route", "source_message_id": mid})
    rid, actor, text, options = deliveries[0]
    assert actor == family["facade"]
    assert rid == family["router"] and "sample_admin" in text and "file_original" in text and "本轮原文 ${facts}" in text
    assert options["business_ref"] == f"family-route/{family['facade']}/message/{mid}"
    assert json.dumps(quote, ensure_ascii=False) in text
    assert resident_conversation_stream.get_repository().message(family["facade"], mid)["quote"] == quote
    assert "完整的 Family 资料" not in text and "成员摘要" not in text and "<packet>" not in text
    assert "family-work-guide.md" in text
    assert json.dumps({"members": family["members"], "steward": family["steward"]}, ensure_ascii=False)[1:-1] in text
    assert "选择其门面" in text


def test_invalid_template_is_rejected_before_it_can_replace_current_task(configured_family):
    body, block = configured_family
    wire_id = next(e["id"] for e in block["elements"] if e.get("key") == "route")
    wire = next(w for w in body["program"]["wires"] if w["id"] == wire_id)
    wire["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"]["route"]["prompt"] = "${unknown}"
    with pytest.raises(ValueError, match="仅支持"):
        family_tasks.validate_routes(body, block)


def test_queue_task_preserves_actual_pressure_facts_without_bulk_context(configured_family, monkeypatch):
    _, block = configured_family
    family = block["family"]
    deliveries = []
    def deliver(rid, actor, text, **options):
        deliveries.append((rid, actor, text))
        return {"message_id": "msg_1123456789abcdef0123456789abcdef"}
    monkeypatch.setattr(resident_execution_domain, "deliver_resident_message_content", deliver)
    payload = {"message_id": "msg_waiting", "queue_count": 3, "pressure_metric": "queue_length",
               "pressure_threshold": 1, "pressure_value": 3, "oldest_wait_seconds": 45}
    event = {"id": "event_queue", "type": "resident.queue_pressure_exceeded",
             "subject_resident_id": family["members"][0],
             "payload": {**payload, "member_summaries": "DO_NOT_INJECT", "input_state": "running"}}
    ok("DispatchFamilyTask", {"resident_id": family["facade"], "trigger": "queue", "event": event})
    rid, actor, text = deliveries[0]
    assert rid == family["steward"] and actor == family["facade"]
    facts = json.loads(text.splitlines()[1])
    assert facts["event"] == {**{k: event[k] for k in ("id", "type", "subject_resident_id")}, **payload}
    assert "DO_NOT_INJECT" not in text and "family-work-guide.md" in text


@pytest.mark.parametrize("trigger", ["review", "summary", "queue"])
def test_maintenance_dispatch_accepts_real_input_with_identity_only_admission(configured_family, monkeypatch, trigger):
    """周期回顾及复用维护入口使用真实受理链，提交后可读取同一输入。"""
    _, block = configured_family
    family = block["family"]
    monkeypatch.setattr(resident_execution_domain, "deliver_resident_message_content", deliver_resident_message_content)
    result = ok("DispatchFamilyTask", {"resident_id": family["facade"], "trigger": trigger})
    message = resident_conversation_stream.get_repository().message(family["steward"], result["message_id"])
    assert message["author_id"] == family["facade"]
    from management_portal.society_service import resident_execution_work
    job = resident_execution_work.read(_find_resident(family["steward"]))
    assert job["business_ref"] == "duty/family-" + trigger
    assert resident_conversation_stream.get_repository().input_state(
        family["steward"], result["message_id"],
    )["state"] in {"queued", "running"}


@pytest.mark.parametrize("custom_guide,custom_prompt", [(False, False), (True, False), (False, True), (True, True)])
def test_queue_default_migration_preserves_each_edited_field(configured_family, custom_guide, custom_prompt):
    body, block = configured_family
    wire_id = next(e["id"] for e in block["elements"] if e.get("key") == "notify_member_queue")
    wire = next(w for w in body["program"]["wires"] if w["id"] == wire_id)
    profiles = copy.deepcopy(wire["config"]["invoke_profiles"])
    task = profiles["effect"]["arguments"]["family_tasks"]["value"]["queue"]
    task["guide"] = "自定义队列处理" if custom_guide else family_tasks._OLD_QUEUE_GUIDE
    task["prompt"] = "自定义任务 ${facts}" if custom_prompt else family_tasks._OLD_QUEUE_PROMPT
    expected = {"guide": task["guide"] if custom_guide else family_tasks.TASKS["queue"][3],
                "prompt": task["prompt"] if custom_prompt else family_tasks.defaults("notify_member_queue")["queue"]["prompt"]}
    ok("ApplyLineageChanges", {"lineage_id": body["lineage_id"], "operations": [
        {"op": "update", "kind": "wire", "id": wire_id, "changes": {"config.invoke_profiles": profiles}}]})
    familize.sync_generated_structure(body["lineage_id"])
    current, current_block = family_tasks.context(_find_resident(block["family"]["steward"]))
    projected = family_tasks.configured(current, current_block)["queue"]
    stored = next(w for w in current["program"]["wires"] if w["id"] == wire_id)["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"]["queue"]
    assert {k: projected[k] for k in expected} == expected
    assert {k: stored[k] for k in expected} == expected


@pytest.mark.parametrize("custom", [False, True])
def test_recovery_default_migration_preserves_edits(configured_family, custom):
    body, block = configured_family
    wire_id = next(e["id"] for e in block["elements"] if e.get("key") == "recheck_recovery")
    wire = next(w for w in body["program"]["wires"] if w["id"] == wire_id)
    profiles = copy.deepcopy(wire["config"]["invoke_profiles"])
    task = profiles["effect"]["arguments"]["family_tasks"]["value"]["recovery"]
    original = "人工编辑恢复指南" if custom else family_tasks._OLD_RECOVERY_GUIDE
    task["guide"] = original
    ok("ApplyLineageChanges", {"lineage_id": body["lineage_id"], "operations": [
        {"op": "update", "kind": "wire", "id": wire_id, "changes": {"config.invoke_profiles": profiles}}]})
    familize.sync_generated_structure(body["lineage_id"])
    current, current_block = family_tasks.context(_find_resident(block["family"]["steward"]))
    guide = family_tasks.configured(current, current_block)["recovery"]["guide"]
    assert guide == (original if custom else family_tasks.TASKS["recovery"][3])
    assert next(w for w in current["program"]["wires"] if w["id"] == wire_id)["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"]["recovery"]["guide"] == guide


def test_first_turn_has_guide_and_later_turns_only_have_a_readable_reference(configured_family):
    _, block = configured_family
    rid = block["family"]["router"]
    first = ok("PreviewPromptInjection", {"resident_id": rid, "turn": "first"})
    later = ok("PreviewPromptInjection", {"resident_id": rid, "turn": "later"})
    assert any(p["id"] == "family_work_guide" and p["sent"] for p in first["parts"])
    assert not any(p["id"] == "family_work_guide" and p["sent"] for p in later["parts"])


def test_roster_sync_retires_generated_quota_watch_and_keeps_task_templates(configured_family):
    """旧实例的额度源、Watch 和 Route 一起退役，既有任务配置保留。"""
    from management_portal.society_service import lineage_domain
    body, block = configured_family
    catalog = lineage_domain._lineage_catalog_store()
    body = catalog.get(body["lineage_id"])
    block = next(b for b in body["function_blocks"] if b["id"] == block["id"])
    before = family_tasks.configured(body, block)
    legacy = "event_source_legacyquota"
    body["program"]["components"].append({"id": legacy, "kind": "event_source", "config": {"event_type": "resident.account_quota_low"}})
    block["elements"].append({"id": legacy, "kind": "component", "key": "role_account_quota"})
    availability = next(e["id"] for e in block["elements"] if e["key"] == "role_availability")
    for key, kind, target in [("watch_role_account_quota:" + block["family"]["router"], "watch", block["family"]["router"]),
                              ("check_role_quota", "route", availability)]:
        wid = "wire_legacy" + kind
        wire = copy.deepcopy(next(w for w in body["program"]["wires"] if w["kind"] == kind))
        wire.update(id=wid, source=legacy, target=target)
        body["program"]["wires"].append(wire)
        block["elements"].append({"id": wid, "kind": "wire", "key": key})
    # Persist the old graph, then exercise the actual startup migration and
    # commit path (including validation and obsolete runtime cleanup).
    catalog.upsert(body)
    assert familize.sync_generated_structure(body["lineage_id"])["status"] == "synced"
    body = graph(body["lineage_id"])
    block = next(b for b in body["function_blocks"] if b["id"] == block["id"])
    assert not any(c["id"] == legacy for c in body["program"]["components"])
    assert not any(w["source"] == legacy for w in body["program"]["wires"])
    assert not any("role_account_quota" in e["key"] or e["key"] == "check_role_quota" for e in block["elements"])
    assert family_tasks.configured(body, block) == before
    assert familize.sync_generated_structure(body["lineage_id"])["status"] == "unchanged"


def test_account_policy_tracks_live_binding_for_any_resident(configured_family, monkeypatch):
    """准入使用任意 Resident 的实际 Session 账号，不依赖 Family 角色。"""
    from management_portal.society_service import resident_account_policy, resident_backend
    from tests.cases.test_family_quota import _account
    _, block = configured_family
    binding = {"backend_route_id": "one"}
    monkeypatch.setattr(resident_backend, "session_record", lambda record: binding)
    monkeypatch.setattr(resident_backend, "execution_provider", lambda record: "codex")
    accounts = [_account("one", 9), _account("two", 80)]
    for rid in [block["family"]["router"], *block["family"]["members"]]:
        record = _find_resident(rid)
        binding["backend_route_id"] = "one"
        assert resident_account_policy.resident_account(record, accounts)["account_id"] == "codex_one"
        binding["backend_route_id"] = "two"
        assert resident_account_policy.resident_account(record, accounts)["account_id"] == "codex_two"


def test_quota_recovery_only_checks_roles_with_unadmitted_input(configured_family, monkeypatch):
    from types import SimpleNamespace
    from management_portal.society_service import family_quota
    _, block = configured_family
    family = block["family"]
    records = [_find_resident(family[role]) for role in ("router", "steward", "facade")]
    checked = []
    reads = []
    monkeypatch.setattr(family_quota.execution_port, "service", lambda: SimpleNamespace(accounts=lambda: reads.append(True) or {"accounts": []}))
    monkeypatch.setattr(family_quota, "ensure_role_reserve", lambda record, accounts: checked.append(record["resident_id"]) or True)
    jobs = {}
    monkeypatch.setattr(family_quota.resident_execution_work, "read", lambda record: jobs.get(record["resident_id"], {}))
    assert family_quota.reconcile_role_reserves(records) == 0
    assert reads == [] and checked == []  # Idle roles do not query accounts.
    jobs[family["router"]] = {"execution_id": "waiting", "user_message_id": "original"}
    jobs[family["steward"]] = {"execution_id": "running", "user_message_id": "other", "submitted_at": "2026-10-19T00:00:00Z"}
    assert family_quota.reconcile_role_reserves(records) == 1
    assert len(reads) == 1 and checked == [family["router"]]


def test_stale_quota_retains_incident_and_funded_backend_resolves_it(configured_family, monkeypatch):
    from management_portal.society_service import family_quota
    _, block = configured_family
    record = _find_resident(block["family"]["router"])
    resident_intake_domain._commit_resident_intake_state(record, "open")
    with family_quota.get_lineage_data_store().family_quota_migrations.transaction(record['lineage_id'], record['resident_id']) as state:
        state.update(status="unavailable", reason="无备用后端")
    account = {}
    monkeypatch.setattr(family_quota, "resident_account", lambda record, accounts: account)
    assert family_quota.ensure_role_reserve(record, []) is True
    assert family_quota.role_availability(record)["status"] == "unavailable"
    account.update(provider="claude", account_id="paid", backend_route_id="paid",
                   quota_reserve={"fresh": True, "status": "available", "remaining": 15,
                                  "remaining_balance": 15, "currency": "CNY"})
    assert family_quota.ensure_role_reserve(record, []) is False
    assert family_quota.role_availability(record)["status"] == "available"


@pytest.mark.parametrize("role", ["router", "steward"])
def test_both_roles_migrate_on_short_window_pressure_and_keep_context(configured_family, monkeypatch, role):
    from management_portal.society_service import family_quota, resident_restart_domain
    from tests.cases.test_family_quota import _account
    _, block = configured_family
    record = _find_resident(block["family"][role])
    resident_intake_domain._commit_resident_intake_state(record, "open")
    accounts = [_account("one", 90, short=8), _account("two", 90)]
    monkeypatch.setattr(family_quota, "resident_account", lambda record, accounts: accounts[0])
    calls = []
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert family_quota.ensure_role_reserve(_find_resident(record["resident_id"]), accounts)
    assert calls[0][0][2]["target_account"] == "two"
    assert calls[0][0][2]["context_strategy"] == "compact"
    assert family_quota.role_availability(record)["status"] == "migrating"


def test_sync_retires_automatic_progress_and_steward_summary_watch(configured_family):
    from management_portal.society_service import lineage_domain
    body, block = configured_family
    catalog = lineage_domain._lineage_catalog_store()
    body = catalog.get(body["lineage_id"])
    block = next(b for b in body["function_blocks"] if b["id"] == block["id"])
    ids = {e["key"]: e["id"] for e in block["elements"]}
    program = body["program"]
    for key, kind, source, target in [
        ("request_progress", "route", ids["facade_inbox"], "effect_oldprogress"),
        ("watch_member_done:" + block["family"]["steward"], "watch", ids["member_done"], block["family"]["steward"]),
    ]:
        wire = copy.deepcopy(next(w for w in program["wires"] if w["kind"] == kind))
        wire.update(id="wire_oldsummary" + kind, source=source, target=target)
        program["wires"].append(wire)
        block["elements"].append({"id": wire["id"], "kind": "wire", "key": key})
    effect = copy.deepcopy(next(c for c in program["components"] if c["id"] == ids["summary_rollup"]))
    effect["id"] = "effect_oldprogress"
    program["components"].append(effect)
    block["elements"].append({"id": effect["id"], "kind": "component", "key": "progress_report"})
    route = next(w for w in program["wires"] if w["id"] == ids["request_summary"])
    route["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"]["summary"]["guide"] = "保留用户定制摘要指南"
    before = copy.deepcopy(route)
    catalog.upsert(body)
    assert familize.sync_generated_structure(body["lineage_id"])["status"] == "synced"
    body = graph(body["lineage_id"])
    block = next(b for b in body["function_blocks"] if b["id"] == block["id"])
    assert not {"progress_report", "request_progress", "watch_member_done:" + block["family"]["steward"]} & {e["key"] for e in block["elements"]}
    assert next(w for w in body["program"]["wires"] if w["id"] == before["id"])["config"] == before["config"]
    watched = {w["target"] for w in body["program"]["wires"] if w["kind"] == "watch" and w["source"] == ids["member_done"]}
    assert watched == set(block["family"]["members"])
    assert "progress" not in family_tasks.configured(body, block)
    steward = _find_resident(block["family"]["steward"])
    assert "Steward 不维护自身工作摘要" in family_tasks.guide(steward)[2]
    # Previously queued/manual batches remain publishable after retiring the automatic source.
    assert "PublishFamilyProgress" in family_tasks.render(steward, "progress", {"batch_id": "old_batch"})
    assert familize.sync_generated_structure(body["lineage_id"])["status"] == "unchanged"


@pytest.mark.parametrize("trigger", ["queue", "review", "summary"])
@pytest.mark.parametrize("custom", [False, True])
def test_unified_summary_default_migration_preserves_custom_guides(trigger, custom):
    old = getattr(family_tasks, "_PRE_UNIFIED_" + trigger.upper() + "_GUIDE")
    saved = {"guide": "自定义" if custom else old, "prompt": "自定义任务 ${facts}"}
    result = family_tasks.saved_fields(trigger, saved)
    assert (result.get("guide") == "自定义") if custom else ("guide" not in result)
    assert result["prompt"] == saved["prompt"]
    if trigger == "summary":
        assert "prompt" not in family_tasks.saved_fields(trigger, {"prompt": family_tasks._PRE_UNIFIED_SUMMARY_PROMPT})
