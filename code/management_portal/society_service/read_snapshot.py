from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps


_SNAPSHOT = ContextVar("lineage_request_read_snapshot", default=None)


@contextmanager
def read_snapshot_scope():
    if _SNAPSHOT.get() is not None:
        yield
        return
    token = _SNAPSHOT.set({})
    try:
        yield
    finally:
        _SNAPSHOT.reset(token)


def request_cached(method):
    @wraps(method)
    def read(store, *args, **kwargs):
        snapshot = _SNAPSHOT.get()
        if snapshot is None:
            return method(store, *args, **kwargs)
        def freeze(value):
            if isinstance(value, (list, tuple)):
                return tuple(freeze(item) for item in value)
            return value
        key = (id(store), method.__name__, freeze(args), tuple(sorted(kwargs.items())))
        if key not in snapshot:
            snapshot[key] = method(store, *args, **kwargs)
        return deepcopy(snapshot[key])
    return read
