from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .principal import OperationIdentity


_CURRENT_IDENTITY: ContextVar[OperationIdentity | None] = ContextVar("operation_identity", default=None)


def current_operation_identity() -> OperationIdentity | None:
    return _CURRENT_IDENTITY.get()


@contextmanager
def operation_identity_scope(identity: OperationIdentity) -> Iterator[None]:
    token = _CURRENT_IDENTITY.set(identity)
    try:
        yield
    finally:
        _CURRENT_IDENTITY.reset(token)
