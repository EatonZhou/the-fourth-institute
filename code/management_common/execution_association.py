from contextlib import contextmanager
from contextvars import ContextVar


_EXECUTION = ContextVar("request_execution_association", default="")


def current_execution_id() -> str:
    return _EXECUTION.get()


@contextmanager
def execution_scope(execution_id: str = ""):
    if not isinstance(execution_id, str) or len(execution_id) > 128:
        raise ValueError("Invalid execution association")
    token = _EXECUTION.set(execution_id)
    try:
        yield
    finally:
        _EXECUTION.reset(token)
