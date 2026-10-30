"""The Adapter of the hypothetical Provider (``hypothetical_provider``).

A Provider whose agent speaks Portal's native agent socket needs no more
than its name: ``NativeSocketBackend`` runs the turn, its Portal tools, its
input during the turn and its recovery.  Its client comes from its runtime.
"""
from __future__ import annotations

from management_portal.execution.native_socket_backend import NativeSocketBackend
from tests.cases.hypothetical_provider import PROVIDER


class QwenCodeBackend(NativeSocketBackend):
    provider_id = PROVIDER
    marks_answer_phases = False

    @staticmethod
    def client_factory():
        raise RuntimeError("Qwen Code is not installed here; tests give the Adapter a client")
