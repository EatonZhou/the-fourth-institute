"""Stack samples of slow instruction handlers, logged when they finish.

py-spy cannot attach to the services (ptrace_scope=1).  Once a handler has
run for ``SAMPLE_AFTER`` seconds, a sampler thread reads its thread's stack
every ``INTERVAL`` seconds.  A handler that ends after ``LOG_AFTER`` seconds
logs how often each Portal frame was on its stack and the most frequent
innermost frames, so the log shows where the time went.
"""
from __future__ import annotations

import collections
import json
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SAMPLE_AFTER = 0.5
INTERVAL = 0.05
LOG_AFTER = 2.0
_PORTAL = Path(__file__).resolve().parents[2].as_posix() + "/"

_guard = threading.Lock()
_running: dict[int, dict[str, Any]] = {}
_wake = threading.Event()
_sampler: threading.Thread | None = None


def _sample_calls(due: list[dict[str, Any]]) -> None:
    # A frame keeps its request locals alive. Confine the snapshot to this
    # call so the long-lived sampler never holds completed requests while idle.
    frames = sys._current_frames()
    try:
        for call in due:
            frame = frames.get(call['thread_id'])
            if frame is None:
                continue
            try:
                stack = traceback.StackSummary.extract(traceback.walk_stack(frame), lookup_lines=False)
                stack.reverse()
            finally:
                del frame
            portal = [_frame(entry) for entry in stack
                      if _PORTAL in entry.filename.replace('\\', '/')
                      and '/site-packages/' not in entry.filename.replace('\\', '/')]
            call['samples'] += 1
            call['inclusive'].update(set(portal))
            call['innermost'][' < '.join([_frame(stack[-1]), *reversed(portal[-3:])])] += 1
    finally:
        frames.clear()


def _frame(entry: traceback.FrameSummary) -> str:
    path = entry.filename.replace('\\', '/')
    if "/site-packages/" in path:
        path = path.split("/site-packages/", 1)[1]
    elif _PORTAL in path:
        path = path.split(_PORTAL, 1)[1]
    else:
        path = path.rsplit("/", 1)[-1]
    return f"{path}:{entry.lineno} {entry.name}"


def _sample() -> None:
    while True:
        _wake.wait()
        time.sleep(INTERVAL)
        now = time.monotonic()
        with _guard:
            due = [call for call in _running.values() if now - call["started"] >= SAMPLE_AFTER]
            if not _running:
                _wake.clear()
        if not due:
            continue
        _sample_calls(due)


@contextmanager
def track(owner: str, instruction: str) -> Iterator[None]:
    global _sampler
    token = object()
    call = {
        "thread_id": threading.get_ident(), "started": time.monotonic(), "samples": 0,
        "inclusive": collections.Counter(), "innermost": collections.Counter(),
    }
    with _guard:
        if _sampler is None:
            _sampler = threading.Thread(target=_sample, name="slow-call-profile", daemon=True)
            _sampler.start()
        _running[id(token)] = call
        _wake.set()
    try:
        yield
    finally:
        with _guard:
            _running.pop(id(token), None)
        seconds = time.monotonic() - call["started"]
        if seconds >= LOG_AFTER and call["samples"]:
            print("HATG slow instruction profile: " + json.dumps({
                "owner": owner, "instruction": instruction, "seconds": round(seconds, 3),
                "samples": call["samples"], "interval": INTERVAL, "sampled_after": SAMPLE_AFTER,
                "frames": call["inclusive"].most_common(16),
                "innermost": call["innermost"].most_common(6),
            }, ensure_ascii=False, separators=(",", ":")), file=sys.stderr, flush=True)
