from __future__ import annotations

from typing import Any

from .contracts import ResidentTerminalFact
from .ports import LineageRuntimePorts


def _chatroom_terminal(fact: ResidentTerminalFact) -> dict[str, Any]:
    """Bring a Chatroom delivery's reply back as soon as its input ends (decision_a6bebf4ac283).

    Chatroom reads finish the same delivery when this wake-up is lost, so a
    failure here never fails the terminal fact itself.
    """

    if not str(fact.business_ref or "").startswith("chatroom_"):
        return {}
    try:
        from management_portal import chatroom_backend

        return chatroom_backend.handle_resident_terminal(
            business_ref=fact.business_ref,
            resident_id=fact.resident_id,
            message_id=fact.message_id,
        )
    except Exception:  # noqa: BLE001
        return {}


def _mailbox_terminal(fact: ResidentTerminalFact) -> list[str]:
    """A finished Mailbox group finishes its members with its state and replies (decision_b5d349190743)."""

    try:
        from .. import resident_mailbox, resident_workspace_domain

        record = resident_workspace_domain.find_resident(fact.resident_id) or {}
        if not record or not resident_mailbox.members_of(record, fact.message_id):
            return []
        return resident_mailbox.finish_group(record, fact.message_id, fact.terminal_status)
    except Exception:  # noqa: BLE001 - member reads follow the group even without this write
        return []


class LineageBackendRuntimeAdapter:
    """Bind stable runtime ports to the authoritative Lineage engine."""

    @staticmethod
    def _backend() -> Any:
        from .. import lineage_domain

        return lineage_domain

    def consume_terminal(
        self,
        fact: ResidentTerminalFact,
    ) -> dict[str, Any]:
        fact.validate()
        result = self._backend().handle_resident_message_terminal(
            fact.resident_id,
            fact.actor,
            fact.message_id,
            completed_at=fact.completed_at,
            lineage_id=fact.lineage_id,
            terminal_status=fact.terminal_status,
        )
        resident_observers = (
            self._backend().dispatch_resident_terminal_observers(
                fact.resident_id,
                fact.message_id,
                terminal_status=fact.terminal_status,
                completed_at=fact.completed_at,
                exclude_lineage_ids=(
                    {fact.lineage_id} if fact.lineage_id else None
                ),
            )
        )
        result = dict(result or {})
        result["resident_observers"] = resident_observers
        chatroom = _chatroom_terminal(fact)
        if chatroom:
            result["chatroom"] = chatroom
        members = _mailbox_terminal(fact)
        if members:
            result["mailbox_members"] = members
        return result

    def poll_all(self) -> dict[str, Any]:
        return self._backend().poll_all_lineage_system_events()


def build_backend_runtime_ports() -> LineageRuntimePorts:
    adapter = LineageBackendRuntimeAdapter()
    return LineageRuntimePorts(
        resident_terminals=adapter,
        system_events=adapter,
    )
