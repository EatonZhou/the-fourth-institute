"""The resource index lock reports long waits and holds with the code involved.

Change routing in ResourceService waited about half a second per Resident
Resource notification for the index lock, and step timings alone could not
tell which code held it.  The lock now prints a line for any wait or hold of
0.2 s or more, naming the waiting code and the holder.

Slow request reports also give the garbage collector's pauses inside the
request: a recurring 0.2 s in Project reads moved between steps from call to call.
"""
from __future__ import annotations

import json
import threading
import time

from management_portal.resources.timing import ReportingLock


def _reports(capsys) -> list[tuple[str, dict]]:
    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("slow ")]
    return [(line.split(": ", 1)[0], json.loads(line.split(": ", 1)[1])) for line in lines]


def hold_briefly(lock: ReportingLock) -> None:
    with lock:
        pass


def hold_long(lock: ReportingLock, seconds: float, entered: threading.Event | None = None) -> None:
    with lock:
        with lock:
            if entered is not None:
                entered.set()
            time.sleep(seconds)


def test_a_long_hold_is_reported_once_with_its_holder(capsys):
    """长时间持有资源索引锁时报告持有代码"""
    lock = ReportingLock("demo", threshold=0.05)
    hold_briefly(lock)
    assert _reports(capsys) == []
    hold_long(lock, 0.08)
    [(label, details)] = _reports(capsys)
    assert label == "slow demo lock hold"
    assert details["site"].startswith("hold_long:")
    assert details["seconds"] >= 0.05


def test_a_long_wait_names_the_waiting_code_and_the_holder(capsys):
    """长时间等待资源索引锁时报告等待方和持有方"""
    lock = ReportingLock("demo", threshold=0.05)
    entered = threading.Event()
    holder = threading.Thread(target=hold_long, args=(lock, 0.15, entered))
    holder.start()
    assert entered.wait(5)
    hold_briefly(lock)
    holder.join(5)
    reports = dict(_reports(capsys))
    wait = reports["slow demo lock wait"]
    assert wait["site"].startswith("hold_briefly:")
    assert wait["holder"].startswith("hold_long:")
    assert reports["slow demo lock hold"]["site"].startswith("hold_long:")


def test_a_slow_request_report_counts_the_collector_pauses_inside_it(capsys):
    """慢请求报告给出请求期间垃圾回收暂停的时间与完整回收次数"""
    import gc

    from management_portal.resources.timing import StepTimer

    timer = StepTimer()
    timer.report_if_slow("quiet", 0.0)
    gc.collect()
    timer.report_if_slow("collected", 0.0)
    lines = [line.split(": ", 1) for line in capsys.readouterr().err.splitlines() if line.startswith("ResourceService slow ")]
    reports = {label.removeprefix("ResourceService slow "): json.loads(details) for label, details in lines}
    assert reports["quiet"]["gc"] == {"seconds": 0.0, "full": 0}
    assert reports["collected"]["gc"]["full"] == 1 and reports["collected"]["gc"]["seconds"] >= 0
