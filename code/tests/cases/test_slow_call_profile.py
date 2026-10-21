from __future__ import annotations

import json
import time

from management_portal.hatg import slow_call_profile


def _wait_for_storage(seconds: float) -> None:
    time.sleep(seconds)


def test_slow_handler_logs_where_its_time_went(monkeypatch, capsys):
    """超过阈值的慢指令记录采样调用栈，快指令不记录"""
    monkeypatch.setattr(slow_call_profile, "SAMPLE_AFTER", 0.05)
    monkeypatch.setattr(slow_call_profile, "LOG_AFTER", 0.3)
    with slow_call_profile.track("lineage", "DeliverMessage"):
        _wait_for_storage(0.5)
    with slow_call_profile.track("lineage", "GetResident"):
        _wait_for_storage(0.01)
    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("HATG slow instruction profile: ")]
    assert len(lines) == 1
    profile = json.loads(lines[0].split(": ", 1)[1])
    assert (profile["owner"], profile["instruction"]) == ("lineage", "DeliverMessage")
    assert profile["samples"] >= 3
    frames = dict(profile["frames"])
    waiting = next(frame for frame in frames if frame.endswith(" _wait_for_storage"))
    assert frames[waiting] == profile["samples"]
    assert profile["innermost"][0][0].startswith("tests/cases/test_slow_call_profile.py:")
