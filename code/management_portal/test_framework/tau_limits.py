from __future__ import annotations

import os


DEFAULT_MAX_PARALLEL_MODULES = 8
MAX_PARALLEL_MODULES_CEILING = 32


def tau_max_parallel_modules() -> int:
    """Return the bounded host-side concurrency for one TAU Campaign."""
    raw = os.getenv(
        "PORTAL_TEST_TAU_MAX_PARALLEL_MODULES",
        str(DEFAULT_MAX_PARALLEL_MODULES),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_PARALLEL_MODULES
    return max(1, min(value, MAX_PARALLEL_MODULES_CEILING))
