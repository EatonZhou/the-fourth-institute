"""Step timings of one ResourceService request, reported only when it is slow."""

from __future__ import annotations

import functools
import json
import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, TypeVar

T = TypeVar("T")

SLOW_LOCK_SECONDS = 0.2


def _report(label: str, **details: Any) -> None:
    print(
        f"slow {label}: " + json.dumps(details, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


class StepTimer:
    """Accumulated wall time per named step of one request."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.steps: dict[str, float] = {}

    def add(self, name: str, since: float) -> float:
        now = time.perf_counter()
        self.steps[name] = self.steps.get(name, 0.0) + (now - since)
        return now

    def report_if_slow(self, label: str, threshold: float, **details: Any) -> None:
        total = time.perf_counter() - self.started
        if total < threshold:
            return
        print(
            f"ResourceService slow {label}: " + json.dumps({
                **details,
                "seconds": round(total, 3),
                "steps": {name: round(value, 3) for name, value in self.steps.items()},
            }, ensure_ascii=False, separators=(",", ":")),
            file=sys.stderr,
            flush=True,
        )


_CURRENT: ContextVar[StepTimer | None] = ContextVar("resource_step_timer", default=None)


@contextmanager
def timed_request() -> Iterator[StepTimer]:
    """Make a timer current for the request so nested steps can report into it."""

    timer = StepTimer()
    token = _CURRENT.set(timer)
    try:
        yield timer
    finally:
        _CURRENT.reset(token)


def timed(function: Callable[..., T]) -> Callable[..., T]:
    """Run ``function`` as one timed request; its body reads the timer with ``current``."""

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        with timed_request():
            return function(*args, **kwargs)

    return wrapper


def current() -> StepTimer:
    timer = _CURRENT.get()
    if timer is None:
        raise RuntimeError("no timed request is active")
    return timer


def lap(name: str, since: float) -> float:
    """Add the time since ``since`` to step ``name`` of the current request, if timed."""

    timer = _CURRENT.get()
    if timer is None:
        return time.perf_counter()
    return timer.add(name, since)


def report_slow(label: str, seconds: float, threshold: float = SLOW_LOCK_SECONDS, **details: Any) -> None:
    """Print ``label`` with ``details`` when ``seconds`` reaches ``threshold``."""

    if seconds >= threshold:
        _report(label, seconds=round(seconds, 3), **details)


def _caller_site() -> str:
    frame = sys._getframe(2)
    return f"{frame.f_code.co_name}:{frame.f_lineno}"


class ReportingLock:
    """Re-entrant lock that reports long waits and long holds.

    A report names the code that waited or held the lock, and for a wait the
    code that was holding it when the wait began.
    """

    def __init__(self, name: str, threshold: float = SLOW_LOCK_SECONDS) -> None:
        self._name = name
        self._threshold = threshold
        self._lock = threading.Lock()
        self._owner: int | None = None
        self._depth = 0
        self._since = 0.0
        self._site = ""

    def __enter__(self) -> "ReportingLock":
        me = threading.get_ident()
        if self._owner == me:
            self._depth += 1
            return self
        holder = self._site
        started = time.perf_counter()
        self._lock.acquire()
        acquired = time.perf_counter()
        self._owner, self._depth, self._since = me, 1, acquired
        self._site = _caller_site()
        if acquired - started >= self._threshold:
            _report(
                f"{self._name} lock wait",
                seconds=round(acquired - started, 3), site=self._site, holder=holder,
            )
        return self

    def __exit__(self, *_exc: object) -> bool:
        if self._depth > 1:
            self._depth -= 1
            return False
        held = time.perf_counter() - self._since
        site = self._site
        self._owner, self._depth, self._site = None, 0, ""
        self._lock.release()
        if held >= self._threshold:
            _report(f"{self._name} lock hold", seconds=round(held, 3), site=site)
        return False
