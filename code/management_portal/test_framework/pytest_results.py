"""pytest plugin that streams one JSON line per test event for the Test executor.

Loaded with ``-p management_portal.test_framework.pytest_results``; the executor names the file
in ``PORTAL_TEST_RESULTS_FILE`` and reads it while the shard is still running.
"""
from __future__ import annotations

import json
import os


_PATH = os.environ.get("PORTAL_TEST_RESULTS_FILE", "")
# A case may intercept application filesystem access. Reporting is framework
# I/O and must remain usable while those guards are installed, including when
# a fixture fails during setup.
_OPEN = open
TEXT_LIMIT = 4000


def _write(record: dict) -> None:
    if not _PATH:
        return
    with _OPEN(_PATH, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _text(report) -> str:
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])[-TEXT_LIMIT:]
    return str(longrepr or "")[-TEXT_LIMIT:]


def pytest_runtest_logstart(nodeid, location):
    _write({"event": "start", "nodeid": nodeid})


def pytest_runtest_logreport(report):
    if report.passed and report.when != "call":
        return
    _write({
        "event": "report",
        "nodeid": report.nodeid,
        "when": report.when,
        "outcome": report.outcome,
        "xfail": hasattr(report, "wasxfail"),
        "text": "" if report.passed else _text(report),
    })


def pytest_runtest_logfinish(nodeid, location):
    _write({"event": "finish", "nodeid": nodeid})


def pytest_collectreport(report):
    if report.failed:
        _write({"event": "collect-error", "nodeid": report.nodeid, "text": _text(report)})
