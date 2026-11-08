"""Authenticated permission lending accompanies input admission, never follows dispatch."""
from dataclasses import dataclass, field
from typing import Any

from management_portal import permission_system


@dataclass
class MessageLending:
    issuer: str
    credentials: list[dict[str, str]]
    recipient_id: str = ""
    issued: list[dict[str, Any]] = field(default_factory=list, init=False)

    def issue(self, connection, resident_id: str, message_id: str) -> None:
        self._check_recipient(resident_id)
        self.issued = permission_system.lend_message(
            connection, issuer=self.issuer, resident_id=resident_id, message_id=message_id,
            credentials=self.credentials,
        )

    def replay(self, resident_id: str, message_id: str) -> None:
        """Validate a replay after the input owner checked its immutable identity."""
        from .state_store import get_lineage_data_store
        self._check_recipient(resident_id)
        with get_lineage_data_store().database.transaction() as connection:
            self.issued = permission_system.lend_message(
                connection, issuer=self.issuer, resident_id=resident_id, message_id=message_id,
                credentials=self.credentials, replay=True,
            )

    def _check_recipient(self, resident_id: str) -> None:
        if self.recipient_id and resident_id != self.recipient_id:
            raise ValueError("权限只能交给原问题的提问成员")
