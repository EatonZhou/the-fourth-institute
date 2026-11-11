"""Request-local, content-free timings for diagnosed conversation read stages."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from time import perf_counter


STAGES = frozenset({
    "portal_conv_read_state", "portal_conv_identity", "portal_conv_cursor",
    "portal_conv_entries", "portal_conv_decorate", "portal_conv_summary",
    "portal_conv_activity", "portal_conv_states", "portal_conv_notifications",
    "portal_conv_sources", "portal_mailbox_items", "portal_mailbox_pending",
})
_current = ContextVar("portal_http_timings", default=None)


@contextmanager
def log_slow_mailbox_read():
    """Include socket ISA reads in content-free slow-read diagnostics.

    HTTP reads retain the same timings in Server-Timing. Socket reads have
    their own context, so neither transport records a caller or message body.
    Nested stages overlap; they are not additive.
    """
    timings = _current.get()
    if timings is None:
        timings = {}
    started = perf_counter()
    try:
        with capture_timings(timings):
            yield
    finally:
        elapsed = (perf_counter() - started) * 1000
        if elapsed >= 1000:
            logging.getLogger(__name__).warning(
                "Mailbox read slow total_ms=%.1f stages_ms=%s", elapsed,
                {key: round(value, 1) for key, value in timings.items()
                 if key in STAGES},
            )


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
