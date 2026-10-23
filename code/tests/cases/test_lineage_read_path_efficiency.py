"""Focused regressions for read-heavy Lineage instructions."""
from __future__ import annotations

from types import SimpleNamespace
from contextlib import nullcontext
import pytest










def test_intake_transition_uses_committed_state(monkeypatch):
    """切换 Resident intake 时只读一次旧状态，结果取提交后的状态"""
    from management_portal.society_service.isa import execution

    reads = []
    monkeypatch.setattr(execution, "_intake_state", lambda *_args: reads.append("state") or "closed")
    monkeypatch.setattr(execution, "lineage_of_resident", lambda _rid: "lineage")
    monkeypatch.setattr(execution, "domain", lambda: SimpleNamespace(
        change_lineage_resident_state=lambda *_args, **_kwargs: {"intake_state": "open"},
    ))
    args = {"resident_id": "resident_123456789abc"}
    inv = SimpleNamespace(argument=args.get, caller="sample_admin", entry="web")
    result = execution._change_intake(inv, "open")
    assert result["current"] == "open" and result["changed"] is True
    assert reads == ["state"]


def _intake_fakes(monkeypatch, states):
    from management_portal.society_service.isa import execution

    transitions = []

    def change(_lineage_id, payload, _caller, **_kwargs):
        transitions.append((payload["resident_id"], payload["transition"]))
        states[payload["resident_id"]] = "open" if payload["transition"] == "open" else "closed"
        return {"intake_state": states[payload["resident_id"]]}

    monkeypatch.setattr(execution, "_intake_state", lambda resident_id, _caller: states[resident_id])
    monkeypatch.setattr(execution, "lineage_of_resident", lambda _rid: "lineage")
    monkeypatch.setattr(execution, "domain", lambda: SimpleNamespace(change_lineage_resident_state=change))
    return execution, transitions


def test_open_intake_open_false_closes_one_resident(monkeypatch):
    """OpenIntake open=false 关闭单个 Resident 的接单，open=true 再打开"""
    resident_id = "resident_123456789abc"
    execution, transitions = _intake_fakes(monkeypatch, {resident_id: "open"})
    call = lambda opening: SimpleNamespace(  # noqa: E731
        argument={"resident_id": resident_id, "open": opening}.get, caller="sample_admin", entry="web")
    closed = execution.open_intake(call(False))
    assert closed["previous"] == "open" and closed["current"] == "closed" and closed["changed"] is True
    opened = execution.open_intake(call(True))
    assert opened["current"] == "open" and opened["changed"] is True
    assert transitions == [(resident_id, "close"), (resident_id, "open")]


def test_open_intake_receipt_matches_published_schema(monkeypatch):
    """单人接单开关声明字符串状态，切换与幂等回执均可直接驱动下一次操作"""
    from management_portal.hatg.catalog import instruction_catalog

    fields = {field.name: field.schema() for field in instruction_catalog()["OpenIntake"].result}
    resident_id = "resident_123456789abc"
    execution, transitions = _intake_fakes(monkeypatch, {resident_id: "open"})
    for opening, expected in [(False, "closed"), (False, "closed"), (True, "open"), (True, "open")]:
        inv = SimpleNamespace(argument={"resident_id": resident_id, "open": opening}.get,
                              caller="sample_admin", entry="web")
        result = execution.open_intake(inv)
        for name in ("previous", "current"):
            assert fields[name]["type"] == "string"
            assert isinstance(result[name], str)
            assert result[name] in fields[name]["enum"]
        assert result["current"] == expected
        assert result["changed"] is (result["previous"] != result["current"])
        if "state" in result:
            assert fields["state"]["type"] == "object"
            assert result["state"]["intake_state"] == result["current"]
    # A manual close still records the manual policy; an already-open switch is idempotent.
    assert transitions == [(resident_id, "close"), (resident_id, "close"), (resident_id, "open")]


def test_open_intake_open_false_closes_a_resident_ids_batch(monkeypatch):
    """OpenIntake open=false 按 resident_ids 批量关闭接单并报告变化与未变化的 Resident"""
    first, second = "resident_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb"
    execution, transitions = _intake_fakes(monkeypatch, {first: "open", second: "open"})
    inv = SimpleNamespace(argument={"resident_ids": [first, second], "open": False}.get, caller="sample_admin", entry="lineage")
    result = execution.open_intake(inv)
    assert result["changed_resident_ids"] == [first, second] and result["changed_count"] == 2
    assert result["failed"] == []
    assert transitions == [(first, "close"), (second, "close")]
    # Closing again changes nothing outside a manual call.
    again = execution.open_intake(inv)
    assert again["unchanged_resident_ids"] == [first, second] and again["changed_count"] == 0




def test_watch_lineage_checks_access_before_reading_revisions(monkeypatch):
    """WatchLineage 先检查访问权限，无权时不读取 revision"""
    from management_portal.society_service.isa import definition

    accessed = []
    monkeypatch.setattr(definition, "domain", lambda: SimpleNamespace(
        lineage_access_projection=lambda lineage_id: accessed.append(lineage_id) or {"id": lineage_id},
        _ensure_visible=lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
    ))
    monkeypatch.setattr(definition, "lineage_revision", lambda *_args: (_ for _ in ()).throw(AssertionError("revision read")))
    inv = SimpleNamespace(argument={"lineage_id": "lineage_123456789abc", "after_cursor": 0}.get, caller="guest")
    with pytest.raises(PermissionError, match="denied"):
        next(definition.watch_lineage(inv))
    assert accessed == ["lineage_123456789abc"]


def test_project_rename_busy_omits_viewer_state(monkeypatch):
    """Project 改名查询 Resident 忙碌状态时调用 ListResidentStates 不带查看者状态"""
    from management_portal import project_rename

    calls = []
    monkeypatch.setattr(project_rename.Residents, "_call", lambda _self, name, args:
                        calls.append((name, args)) or {"states": [{"resident_id": "resident_123456789abc", "work_state": "running"}]})
    project_rename.Residents("system").busy(["resident_123456789abc"])
    assert calls == [("ListResidentStates", {"resident_ids": ["resident_123456789abc"], "include_viewer_state": False})]
