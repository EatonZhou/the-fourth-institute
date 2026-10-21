""":design: decision_481dc4e6292c, decision_8b510cbd9c58"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


REPO_ROOT = Path(__file__).resolve().parents[2]
NODE_PROBE = Path(__file__).with_name("javascript") / "scheduled_triggered.cjs"
INJECTION_TEXT = '<img src=x onerror="window.__monitorInjected=1">'
# Each monitor button asks for the instruction it runs (row.allows).
MONITOR_INSTRUCTIONS = ("UpdateComponent", "PauseComponent", "TriggerEvent",
                        "CancelScriptRun", "UpdateResidentSettings", "CancelInput")


def _monitor(case_name: str) -> dict:
    resident = {
        "resident_id": "resident_100000000009",
        "title": "Valid Resident",
        "lineage_id": "lineage_100000000009",
        "lineage_title": "Task Lineage",
        "allows": dict.fromkeys(MONITOR_INSTRUCTIONS, True),
        "health": "ok",
        "resident": {"provider": "codex", "account": "zero"},
        "settings": {"model": "gpt-fixture", "reasoning_effort": "medium"},
        "state": {"work_state": "idle", "queued_count": 0},
        "sources": [{
            "component_id": "event_source_daily",
            "title": "Daily Event",
            "event_type": "time.reached",
            "config": {"event_type": "time.reached", "schedule_mode": "recurring",
                       "recurrence_kind": "interval", "interval_seconds": 60,
                       "jitter_seconds": 10, "max_fires": None},
            # Due time = scheduled time + this fire's random delay.
            "runtime": {"next_scheduled_at": "2026-09-29T00:00:00Z", "next_due_at": "2026-09-29T00:00:07Z"},
            "paused": False,
        }],
        "manual_sources": [{"component_id": "event_source_manual", "title": "Run now"}],
        "tasks": [{"wire_id": "wire_daily", "text": "Summarize yesterday."}],
        "warnings": [],
    }
    if case_name == "read_only_lineage":
        resident["allows"] = dict.fromkeys(MONITOR_INSTRUCTIONS, False)
    if case_name == "event_schedule_and_identity":
        resident["sources"].append({
            "component_id": "event_source_evening", "title": "Evening Event", "event_type": "time.reached",
            "config": {"event_type": "time.reached", "schedule_mode": "recurring",
                       "recurrence_kind": "interval", "interval_seconds": 120},
            # A paused source keeps its old plan; it no longer applies.
            "runtime": {"next_scheduled_at": "2026-09-28T23:00:00Z"}, "paused": True,
        })
    if case_name == "resident_without_timer":
        resident["sources"] = []
        resident["manual_sources"] = []
        resident["state"] = {"work_state": "running", "queued_count": 1}
    if case_name == "resume_visible_monitor":
        resident["state"] = {"work_state": "queued", "queued_count": 1}
    if case_name == "script_effect":
        resident.pop("resident_id")
        resident.update(kind="effect", effect_id="effect_100000000009", row_id="lineage_100000000009/effect_100000000009",
                        title="脚本维护", script_paths=["/jobs/维护.py"],
                        last_run={"status": "succeeded", "path": "/jobs/维护.py", "started_at": "2026-09-29T00:00:00Z"})
    return {
        "schema": "tfi.scheduled_triggered_monitor.v1",
        "status": "error" if case_name == "lineage_errors_visible_with_valid_neighbor" else "ok",
        "loaded": True,
        "items": [resident],
        "lineage_errors": ([{"lineage_id": "lineage_200000000009", "title": INJECTION_TEXT,
                            "error": "Lineage read failed"}]
                           if case_name == "lineage_errors_visible_with_valid_neighbor" else []),
    }


def test_scheduled_triggered_browser_contract(tmp_path):
    """定时与自动触发面板显示下次触发、错误和只读状态，并通过 ISA 修改、暂停、恢复和立即触发

    Production renderer, ISA client and panel lifecycle in Node DOM; no browser.
    The historical function name preserves the stable case ID.

    :design-point:"""
    for case_name in ("lineage_errors_visible_with_valid_neighbor", "event_schedule_and_identity", "read_only_lineage", "resident_without_timer", "script_effect", "resume_visible_monitor", "cold_loading"):
        payload_file = tmp_path / f"{case_name}.json"
        payload_file.write_text(json.dumps(_monitor(case_name), ensure_ascii=False), encoding="utf-8")
        route_file = tmp_path / f"{case_name}-routes.js"
        route_file.write_bytes(portal_route_script())
        result_file = tmp_path / f"{case_name}-result.json"
        result = subprocess.run(
            ["node", str(NODE_PROBE), case_name, str(payload_file), str(route_file), str(result_file)],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        record = json.loads(result_file.read_text(encoding="utf-8"))
        assert record["status"] == "passed", record
        assert record["monitor_reads"] >= 1
        assert not record["page_errors"] and not record["unexpected_requests"]
