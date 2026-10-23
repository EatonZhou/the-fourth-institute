"""Request-local, content-free timings for diagnosed conversation read stages."""
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter


STAGES = frozenset({
    "portal_conv_read_state", "portal_conv_identity", "portal_conv_cursor",
    "portal_conv_entries", "portal_conv_decorate", "portal_conv_summary",
})
_current = ContextVar("portal_http_timings", default=None)


@contextmanager
def capture_timings(timings):
    token = _current.set(timings)
    try:
        yield
    finally:
        _current.reset(token)


def timed_call(stage, callback, *args, **kwargs):
    if stage not in STAGES:
        raise ValueError("Unknown HTTP timing stage")
    timings = _current.get()
    if timings is None:
        return callback(*args, **kwargs)
    started = perf_counter()
    try:
        return callback(*args, **kwargs)
    finally:
        # A stage can include several reads and RPC/cache waits. It is elapsed
        # wall time, not database CPU time; no target IDs or bodies are stored.
        timings[stage] = timings.get(stage, 0) + max(0, perf_counter() - started) * 1000
