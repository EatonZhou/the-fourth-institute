"""Timer- and file-triggered work runs as model turns in a fresh context, and its failures reach people.

* Work started by time and file Events runs in a fresh context instead of
  the Resident's session, and does not overwrite that session's state.
  Every delivery is a model turn: there is no command-only shortcut.
* A manual trigger waits for a busy target instead of being dropped.
* The first failure of triggered work on a day reaches active Lineage admins' Inbox.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import os

import pytest

from management_portal.society_service import lineage_event_claims, lineage_primitives
from management_portal.society_service.lineage_event_dispatch_rules import _program_event_delivery_profile

# A task text written as one shell command.  It is still an ordinary task for the model.
TASK = (
    "后台计划 knowledge-archive-agent-health.timer 到期。请以当前 Resident 身份执行一次既有任务：\n"
    "cd /share/Agents/KnowledgeArchiveAgent && exec /usr/bin/python3 bin/knowledge-archive health\n"
    "核对命令退出码及结果，并报告成功、失败或阻塞原因。相同 Event ID 已执行过时不要再次运行。"
)


def _route(prompt: str, **config: object) -> dict:
    return {"wire_id": "wire_1", "invocation": {"arguments": {"prompt": {"value": prompt}}}, "config": dict(config)}


def test_every_delivery_is_a_model_turn_and_triggered_work_runs_fresh() -> None:
    """定时触发的任务默认在独立会话中运行，连线可改为沿用 Resident 会话"""
    timer = lineage_primitives.make_event("time.reached", payload={})
    manual = lineage_primitives.make_event("manual.triggered", payload={})

    # Even the old one-command text is an ordinary task: no command profile.
    assert _program_event_delivery_profile(timer, _route(TASK)) == {"isolated": True}
    assert _program_event_delivery_profile(manual, _route(TASK)) == {}
    # A wire can keep time and file work in the Resident's session.
    assert _program_event_delivery_profile(timer, _route(TASK, context_policy="session")) == {}


def test_a_manual_trigger_waits_for_a_busy_target(monkeypatch) -> None:
    """目标 Resident 忙碌时手动触发排队等待，定时触发和配置为 drop 的连线直接丢弃"""
    calls = []
    monkeypatch.setattr(lineage_event_claims.lineage_program_runtime, "defer_event_for_wire",
                        lambda *args, **kwargs: calls.append(kwargs["policy"]) or {})
    for event_type in ("manual.triggered", "time.reached"):
        lineage_event_claims.defer_program_event_route(
            "lineage_cccccccccccc", "event_source_1", {"wire_id": "wire_1", "config": {}},
            lineage_primitives.make_event(event_type, payload={}), "cursor", reason="busy",
        )
    lineage_event_claims.defer_program_event_route(
        "lineage_cccccccccccc", "event_source_1", {"wire_id": "wire_1", "config": {"unavailable_policy": "drop"}},
        lineage_primitives.make_event("manual.triggered", payload={}), "cursor", reason="busy",
    )
    assert calls == ["queue", "drop", "drop"]


def test_the_first_triggered_work_failure_of_a_day_reaches_lineage_admins(monkeypatch) -> None:
    """自动触发任务每天首次失败时通知 Lineage 管理员，成功不通知

    :module: execution.service
    :importance: P2
    """
    from management_portal.society_service import lineage_failure_notifications as failures, lineage_human_notifications

    created = []
    monkeypatch.setattr(failures.permission_system, "users_permitted", lambda *_: ["admin-user"])
    monkeypatch.setattr(lineage_human_notifications, "create_notification",
                        lambda lineage_id, **kwargs: created.append((lineage_id, kwargs)) or {})
    background = {"run_id": "run_1", "source_wire_id": "wire_1", "status": "failed",
                  "completed_at": "2026-10-12T00:00:00Z", "event": {"type": "time.reached"}}
    for record in (background, {**background, "status": "succeeded"}):
        failures.notify("lineage_cccccccccccc", "run_1", record,
                        subject="background-failure:wire_1", title="自动触发的任务失败：Archive", message="工作失败")

    assert len(created) == 1
    lineage_id, notice = created[0]
    assert lineage_id == "lineage_cccccccccccc"
    assert notice["recipients"] == ["admin-user"]
    assert notice["severity"] == "error"
    assert notice["notification_id"] == "background-failure:wire_1:2026-10-12"
    assert os.path.basename(notice["title"]).startswith("自动触发的任务失败")


def test_timers_that_will_not_run_again_or_keep_failing_ask_for_their_owners() -> None:
    """次数耗尽、一次性已触发或长期失败的定时器提示所有者清理"""
    from datetime import timedelta

    from management_portal import scheduled_triggered_monitor as monitor

    now = monitor._now()

    def item(sources: list[dict], failures: int = 0, last_success: str = "") -> dict:
        return {"sources": sources, "consecutive_failures": failures, "last_success_at": last_success}

    exhausted = {"config": {"schedule_mode": "recurring"}, "runtime": {"state": "exhausted", "fire_count": 3}}
    fired_once = {"event_type": "time.reached", "config": {"schedule_mode": "at"}, "runtime": {"state": "listening", "fire_count": 1}}
    running = {"config": {"schedule_mode": "recurring"}, "runtime": {"state": "listening", "fire_count": 9}}
    old = monitor._iso(now - timedelta(days=9))
    assert "最大触发次数" in monitor._cleanup_hints(item([exhausted]), now)[0]
    assert "一次性定时" in monitor._cleanup_hints(item([fired_once]), now)[0]
    assert "长期失败" in monitor._cleanup_hints(item([running], failures=6, last_success=old), now)[0]
    assert monitor._cleanup_hints(item([running, exhausted]), now) == []
    assert monitor._cleanup_hints(item([running], failures=6, last_success=monitor._iso(now)), now) == []
