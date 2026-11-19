"""Application composition of the existing Society recovery read owners.

These calls remain synchronous in the calling process. In particular, final
recovery verification must not enter another service's read pool while holding
the resource/permission transaction locks. No result or authorization is cached.
"""
from __future__ import annotations


class ArticleRestoreReaders:
    @staticmethod
    def read_tool_evidence(*, actor: str, resident_id: str, ordinal: int,
                           tool_index: int, tool_item_id: str, expected_hash: str) -> dict:
        from management_portal.society_service.resident_deleted_history import read_history
        return read_history(resident_id, actor, section='tool_evidence', limit=1,
            ordinal=ordinal, tool_index=tool_index, tool_item_id=tool_item_id,
            expected_hash=expected_hash)

    @staticmethod
    def read_message(*, actor: str, resident_id: str, message_id: str) -> dict:
        from management_portal.society_service.resident_protocol import get_resident_message
        return get_resident_message(resident_id, actor, message_id)


SOURCE = ArticleRestoreReaders()
