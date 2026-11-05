"""Bounded size counters for completed, explicitly owned JSON records only."""
from itertools import islice
import sys
import time


def json_footprint(roots, *, max_nodes=20000, seconds=.01):
    pending, seen, size, truncated = list(roots), set(), 0, False
    deadline = time.monotonic() + seconds
    while pending and len(seen) < max_nodes and time.monotonic() < deadline:
        value = pending.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        kind = type(value)
        if kind not in (dict, list, tuple, str, int, float, bool, type(None)):
            continue
        size += sys.getsizeof(value)
        budget = max(0, max_nodes - len(seen) - len(pending))
        if kind is dict:
            take = min(len(value), budget // 2)
            pending.extend(islice(value.keys(), take))
            pending.extend(islice(value.values(), take))
            truncated |= take < len(value)
        elif kind in (list, tuple):
            pending.extend(islice(value, budget))
            truncated |= len(value) > budget
    return {'owned_bytes_lower_bound': size, 'visited_nodes': len(seen),
            'walk_complete': not pending and not truncated}
