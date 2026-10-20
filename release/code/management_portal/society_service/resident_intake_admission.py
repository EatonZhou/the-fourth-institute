"""Trusted in-process admission boundary for graph-originated messages.

Preparation and receipt projection do not hold the graph's execution lock.
The intake owner enters the supplied guard before each durable transition,
in the same lock order as definition changes: graph, then Resident.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, ContextManager, Iterator

from management_common.operation_errors import OperationError

_GUARDS: ContextVar[tuple] = ContextVar('resident_intake_guards', default=())


@dataclass
class Admission:
    resident_id: str
    message_id: str
    guard: Callable[[], ContextManager]
    accept: Callable[[str], None]
    accepted: bool = False

    def committed(self):
        if not self.accepted:
            try:
                self.accept(self.message_id)
            except Exception as exc:
                # The input is already durable. A bookkeeping failure must
                # never release its delivery reservation as an uncommitted send.
                raise OperationError(
                    'Resident input accepted; Lineage receipt update failed',
                    code='lineage.receipt_pending', commit_state='committed',
                    details={'domain_committed': True, 'message_id': self.message_id},
                ) from exc
            self.accepted = True


@contextmanager
def bind(resident_id: str, message_id: str, guard: Callable[[], ContextManager],
         accept: Callable[[str], None]) -> Iterator[Admission]:
    admission = Admission(resident_id, message_id, guard, accept)
    token = _GUARDS.set((*_GUARDS.get(), admission))
    try:
        yield admission
    finally:
        _GUARDS.reset(token)


@contextmanager
def scope(resident_id: str, message_id: str) -> Iterator[None]:
    from contextlib import ExitStack
    with ExitStack() as stack:
        for admission in _GUARDS.get():
            if admission.resident_id == resident_id and admission.message_id == message_id:
                stack.enter_context(admission.guard())
        yield


def committed(resident_id: str, message_id: str) -> None:
    """The intake owner calls this after acceptance, before releasing admission."""
    for admission in _GUARDS.get():
        if admission.resident_id == resident_id and admission.message_id == message_id:
            admission.committed()
