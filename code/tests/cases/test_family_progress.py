"""Family progress batches and bounded publication without member broadcasts.

:module: resident.identity
:design: decision_44cf587c1721
"""
import copy
from types import SimpleNamespace

import pytest

from management_portal.society_service import family_progress as progress, resident_execution_domain
from management_portal.society_service.resident_execution_domain import deliver_resident_message_content
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_family_supervision import team, lineage, charters, future_creation_store  # noqa: F401
from tests.cases.test_family_familize import graph
from tests.cases.isa_test_support import ok


@pytest.fixture
def reports(team, monkeypatch):
    doc = {"revision": 0, "summary": {}, "source_versions": {}}
    infos = []
    inputs = {"members": [{"resident_id": team.member, "summary": {"recent_results": ["已完成登录改进"]}}],
              "expected_revision": 0, "source_versions": {"roster": "one", team.member: "two"}}
    monkeypatch.setattr(progress.work_summary, "get_family_summary_inputs", lambda *args: copy.deepcopy(inputs))
    monkeypatch.setattr(progress.work_summary, "maintenance_view", lambda *args: copy.deepcopy(doc))
    def update(actor, rid, revision, *, summary, source_versions):
        assert actor == team.steward and rid == team.facade
        if revision != doc["revision"]:
            raise ValueError("摘要版本冲突")
        doc.update(revision=revision + 1, summary=copy.deepcopy(summary), source_versions=source_versions)
    monkeypatch.setattr(progress.work_summary, "update_resident_work_summary", update)
    def deliver(rid, actor, text, **kwargs):
        infos.append({"resident_id": rid, "actor": actor, "text": text, **kwargs})
    monkeypatch.setattr(resident_execution_domain, "deliver_resident_message_content", deliver)
    def receive(mid=None, **options):
        mid = mid or team.message(team.facade, "queued")
        event = {"type": "resident.message_received", "payload": {"resident_id": team.facade, "message_id": mid}}
        return mid, progress.check(team.lineage, team.family["family_id"], team.facade, event=event, **options)
    def publish(batch="20", **options):
        args = dict(batch_id=batch, report="已完成登录改进，继续处理权限边界。", summary={"current_focus": "登录改进已完成，处理权限边界"},
                    expected_revision=doc["revision"], source_versions=inputs["source_versions"])
        args.update(options)
        return progress.publish(team.lineage, team.family["family_id"], team.steward, **args)
    return SimpleNamespace(team=team, doc=doc, inputs=inputs, infos=infos, receive=receive, publish=publish, deliver=deliver)


def test_every_twenty_distinct_inputs_excludes_retry_and_info_and_continues_counting(reports):
    """Family 进展简报的触发、发布与设置"""
    r = reports
    for _ in range(19):
        mid, result = r.receive()
    r.receive(mid)
    r.receive("msg_missing_info")
    info = r.team.message(r.team.facade, "queued")
    progress.resident_held_infos.hold({"resident_id": r.team.facade}, r.team.repo.message(r.team.facade, info))
    r.receive(info)
    assert not r.team.sent
    _, result = r.receive()
    assert result["count"] == 20 and result["requested_batch_ids"] == ["20"]
    assert len(r.team.sent) == 1 and "1–20" in r.team.sent[0]["text"]
    assert r.team.sent[0]["task_trigger"] == "progress"
    assert r.team.sent[0]["task_facts"]["batch_id"] == "20"
    assert "已完成登录改进" not in r.team.sent[0]["text"]
    assert "不群发" in __import__("management_portal.society_service.family_tasks", fromlist=["TASKS"]).TASKS["progress"][3]
    for _ in range(20):
        r.receive()
    assert len(r.team.sent) == 2 and "21–40" in r.team.sent[1]["text"]
    with pytest.raises(ValueError, match="更早"):
        r.publish("40")


def test_report_and_summary_have_hard_500_character_limits_before_side_effects(reports):
    """Family 进展简报的触发、发布与设置"""
    r = reports
    r.receive(every_turns=1)
    for text in ["字" * 501, "字" * 500 + "\n", "🙂" * 501, " "]:
        with pytest.raises(ValueError, match="500"):
            r.publish("1", report=text)
    with pytest.raises(ValueError, match="500"):
        r.publish("1", summary={"current_focus": "字" * 300, "recent_results": ["字" * 201]})
    assert not r.infos and r.doc["revision"] == 0
    r.publish("1", report="字" * 499 + "。", summary={"current_focus": "字" * 500})
    assert not r.infos
    assert len(r.doc["summary"]["current_focus"]) == 500


def test_summary_write_crash_retries_without_broadcast_or_duplicate_summary(reports, monkeypatch):
    """摘要写入后中断可重试，且不向成员群发。"""
    r = reports
    r.receive(every_turns=1)
    update = progress.work_summary.update_resident_work_summary
    def interrupted(*args, **kwargs):
        update(*args, **kwargs)
        raise RuntimeError("crash after summary write")
    monkeypatch.setattr(progress.work_summary, "update_resident_work_summary", interrupted)
    with pytest.raises(RuntimeError, match="crash"):
        r.publish("1")
    assert not r.infos and r.doc["revision"] == 1
    monkeypatch.setattr(progress.work_summary, "update_resident_work_summary", update)
    assert r.publish("1")["status"] == "published"
    r.publish("1")
    assert not r.infos and r.doc["revision"] == 1
    with pytest.raises(ValueError, match="不能修改"):
        r.publish("1", report="不同的简报")


def test_legacy_partial_publication_does_not_resume_member_broadcast(reports):
    """旧版部分投递批次重试不继续向剩余成员群发。"""
    r = reports
    r.receive(every_turns=1)
    r.publish("1")
    repo = get_lineage_data_store().family_relays
    with repo.transaction(r.team.lineage, r.team.facade) as relay:
        batch = relay["progress"]["batches"]["1"]
        batch.update(status="publishing", recipients=[r.team.member, r.team.other], delivered=[r.team.member])
    assert r.publish("1")["status"] == "published"
    assert not r.infos and r.doc["revision"] == 1
    with repo.transaction(r.team.lineage, r.team.facade) as relay:
        batch = relay["progress"]["batches"]["1"]
        assert batch["status"] == "published"
        assert batch["delivered"] == [r.team.member]


def test_summary_conflict_and_wrong_author_do_not_deliver_and_request_failure_is_retryable(reports, monkeypatch):
    """Family 进展简报的触发、发布与设置"""
    r = reports
    from management_portal.society_service import family_facade
    original = family_facade.tell_steward
    monkeypatch.setattr(family_facade, "tell_steward", lambda *args, **kw: (_ for _ in ()).throw(RuntimeError("offline")))
    mid = r.team.message(r.team.facade, "queued")
    with pytest.raises(RuntimeError):
        r.receive(mid, every_turns=1)
    monkeypatch.setattr(family_facade, "tell_steward", original)
    r.receive(mid, every_turns=1)
    assert len(r.team.sent) == 1
    with pytest.raises(ValueError, match="版本"):
        r.publish("1", expected_revision=99)
    with pytest.raises(PermissionError):
        progress.publish(r.team.lineage, r.team.family["family_id"], r.team.member,
                         batch_id="1", report="进展", summary={}, expected_revision=0, source_versions={})
    assert not r.infos
    assert r.publish("1")["status"] == "published"


def test_hourly_summary_is_the_only_generated_summary_schedule(reports):
    lid, fid = reports.team.lineage, reports.team.family["family_id"]
    view = graph(lid)
    block = next(b for b in view["function_blocks"] if b["id"] == fid)
    elements = {e["key"]: e["id"] for e in block["elements"]}
    assert not {"request_progress", "progress_report"} & elements.keys()
    timer = next(c for c in view["program"]["components"] if c["id"] == elements["summary_timer"])
    assert timer["config"]["interval_seconds"] == 3600
    diagnostics = ok("GetLineageDiagnostics", {"lineage_id": lid})["diagnostics"]
    assert not [d for d in diagnostics if d["severity"] == "error"]


def test_concurrent_duplicate_arrivals_reserve_one_batch(reports):
    """Family 进展简报的触发、发布与设置"""
    from concurrent.futures import ThreadPoolExecutor
    r = reports
    inputs = [r.team.message(r.team.facade, "queued") for _ in range(20)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(r.receive, inputs + inputs))
    ledger = get_lineage_data_store().family_relays.get(r.team.lineage, r.team.facade)["progress"]
    assert ledger["count"] == 20 and list(ledger["batches"]) == ["20"]
    assert len({notice["message_id"] for notice in r.team.sent}) == 1


def test_publication_updates_real_summary_without_infos_and_steward_can_notify_selectively(team, monkeypatch):
    """Family 进展简报的触发、发布与设置"""
    monkeypatch.setattr(resident_execution_domain, "deliver_resident_message_content", deliver_resident_message_content)
    mid = team.message(team.facade, "queued")
    ok("CheckFamilyProgress", {"lineage_id": team.lineage, "family_id": team.family["family_id"], "every_turns": 1,
       "event": {"type": "resident.message_received", "payload": {"resident_id": team.facade, "message_id": mid}}},
       actor=team.steward)
    materials = progress.work_summary.get_family_summary_inputs(team.steward, team.facade)
    args = {"lineage_id": team.lineage, "family_id": team.family["family_id"], "batch_id": "1",
            "report": "Family 已开始接收工作，成员成果摘要尚未齐全。", "summary": {"current_focus": "接收工作，等待成员成果摘要"},
            "expected_revision": materials["expected_revision"], "source_versions": materials["source_versions"]}
    assert ok("PublishFamilyProgress", args, actor=team.steward)["status"] == "published"
    stored = progress.work_summary.maintenance_view(team.steward, team.facade)
    assert stored["summary"]["current_focus"] == args["summary"]["current_focus"]
    assert stored["source_versions"] == args["source_versions"]
    for rid in (team.member, team.other):
        assert not progress.resident_held_infos.peek({"resident_id": rid})
    deliver_resident_message_content(team.member, team.steward, "这条信息只与你的任务有关",
        activation_policy="attach_to_next")
    infos = progress.resident_held_infos.peek({"resident_id": team.member})
    assert len(infos) == 1
    assert get_lineage_data_store().mailbox_entry(team.member, infos[0]["message_id"])["place"] == "info"
    assert not progress.resident_held_infos.peek({"resident_id": team.other})
