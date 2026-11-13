"""定时工作模板按当地钟点触发，施加结果保持独立。"""

from copy import deepcopy
from datetime import datetime
import os
import time

import pytest

from tests.cases.isa_test_support import ACTOR
from tests.cases.test_isa_lineage_lifecycle import (
    future_creation_store as future_creation_store,
    graph,
    lineage as lineage,
    resident,
)


def _template():
    from management_portal.society_service import templates

    return next(t for t in templates.list_templates() if t["builtin_key"] == "scheduled_work")


@pytest.fixture
def system_clock(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("This system-local clock fixture requires time.tzset")
    previous = os.environ.get("TZ")

    def change(zone):
        monkeypatch.setenv("TZ", zone)
        time.tzset()

    yield change
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


@pytest.mark.parametrize(
    "system_zone,parameters,after,expected",
    [
        ("Asia/Hong_Kong", {}, "2026-11-13T00:00:00+00:00", "2026-11-13T01:00:00+00:00"),
        ("Asia/Hong_Kong", {"calendar_time": "21:30"},
         "2026-11-13T13:30:00+00:00", "2026-11-14T13:30:00+00:00"),
        ("America/New_York", {"calendar_time": "09:15"},
         "2026-11-13T13:00:00+00:00", "2026-11-13T14:15:00+00:00"),
    ],
)
def test_apply_clock_time(lineage, system_clock, system_zone, parameters, after, expected):
    from management_portal.society_service import lineage_recurring_schedule, templates

    system_clock(system_zone)
    target = resident(lineage, "Target")
    template = _template()
    fields = {p["name"]: p for p in template["application_parameters"]}
    assert set(fields) == {"task", "calendar_time"}
    assert fields["calendar_time"]["multiline"] is False
    assert "每天" in template["behavior"]["steps"][0]
    assert "{{param.calendar_time}}" in template["behavior"]["steps"][0]
    assert "系统本地时间" in template["behavior"]["steps"][0]
    result = templates.apply(
        lineage, template["template_id"], {"kind": "resident", "resident_ids": [target]},
        ACTOR, parameters={"task": "Check the daily result", **parameters},
    )
    view = graph(lineage)
    components = [c for c in view["program"]["components"] if c["id"] in result["created"]["component_ids"]]
    timer = next(c for c in components if c["config"]["event_type"] == "time.reached")
    config = timer["config"]
    assert config["recurrence_kind"] == "calendar"
    assert config["calendar_frequency"] == "daily"
    assert config["timezone"] == "local"
    assert config["jitter_seconds"] == 0
    assert "interval_seconds" not in config
    assert lineage_recurring_schedule.first_after(config, datetime.fromisoformat(after)) == datetime.fromisoformat(expected)
    assert config["misfire_policy"] == "coalesce_latest"
    assert config["overlap_policy"] == "drop"
    assert len(components) == len(result["created"]["wire_ids"]) == 2
    assert any(c["config"]["event_type"] == "manual.triggered" for c in components)
    routes = [w for w in view["program"]["wires"] if w["id"] in result["created"]["wire_ids"]]
    assert all(w["target"] == target for w in routes)


def test_system_clock_dst_and_explicit_timezone(system_clock):
    from management_portal.society_service import lineage_recurring_schedule as schedule

    system_clock("America/New_York")
    config = schedule.normalize_config({
        "recurrence_kind": "calendar", "calendar_time": "09:00", "timezone": "local",
    })

    def next_at(after, expected, **changes):
        assert schedule.first_after({**config, **changes}, datetime.fromisoformat(after)) == datetime.fromisoformat(expected)

    # The OS clock's future DST offset is used, rather than today's fixed offset.
    next_at("2026-03-07T15:00:00+00:00", "2026-03-08T13:00:00+00:00")
    # Missing wall times are skipped; a repeated wall time fires only once.
    next_at("2026-03-08T05:00:00+00:00", "2026-03-09T06:30:00+00:00", calendar_time="02:30")
    next_at("2026-11-01T04:00:00+00:00", "2026-11-01T05:30:00+00:00", calendar_time="01:30")
    next_at("2026-11-01T05:30:00+00:00", "2026-11-02T06:30:00+00:00", calendar_time="01:30")
    # Existing explicit-zone schedules remain independent of the host clock.
    next_at("2026-11-13T00:00:00+00:00", "2026-11-13T01:00:00+00:00", timezone="Asia/Shanghai")
    with pytest.raises(ValueError, match="IANA"):
        schedule.normalize_config({**config, "timezone": "invalid/zone"})


@pytest.mark.parametrize("parameters", [{"calendar_time": "25:00"}, {"timezone": "invalid/zone"}])
def test_invalid_schedule_is_atomic(lineage, parameters):
    from management_portal.society_service import templates

    target = resident(lineage, "Target")
    template = _template()
    before = graph(lineage)
    with pytest.raises(ValueError):
        templates.apply(
            lineage, template["template_id"], {"kind": "resident", "resident_ids": [target]},
            ACTOR, parameters={"task": "Daily check", **parameters},
        )
    assert graph(lineage)["program"] == before["program"]
    assert graph(lineage)["function_blocks"] == before["function_blocks"]


def test_refresh_does_not_migrate_applied_interval(lineage, monkeypatch):
    from management_portal.society_service import templates
    from management_portal.society_service.templates import builtins

    current = deepcopy(builtins.definitions())
    previous = deepcopy(current)
    old = next(t for t in previous if t["builtin_key"] == "scheduled_work")
    old["parameters"] = [{"name": "task", "type": "text", "required": True}]
    old["body"]["components"][0]["config"] = {
        "event_type": "time.reached", "schedule_mode": "recurring",
        "recurrence_kind": "interval", "interval_seconds": 3600,
    }
    monkeypatch.setattr(builtins, "definitions", lambda: previous)
    template = _template()
    target = resident(lineage, "Target")
    templates.apply(
        lineage, template["template_id"], {"kind": "resident", "resident_ids": [target]},
        ACTOR, parameters={"task": "Existing hourly task"},
    )
    before = graph(lineage)["program"]
    monkeypatch.setattr(builtins, "definitions", lambda: current)
    refreshed = _template()
    assert refreshed["template_id"] == template["template_id"]
    assert refreshed["version"] == template["version"] + 1
    assert refreshed["body"]["components"][0]["config"]["recurrence_kind"] == "calendar"
    assert graph(lineage)["program"] == before
