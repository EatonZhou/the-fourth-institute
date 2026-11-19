"""Consume the two precise recovery reads supplied by application composition."""
from __future__ import annotations

from management_portal import portal_features


def _reader(name):
    readers = portal_features.contributions('resource.article_restore_reads')
    if len(readers) != 1 or not callable(getattr(readers[0][1], name, None)):
        raise ValueError('Article restoration requires one installed read contribution')
    return getattr(readers[0][1], name)


def read_tool_evidence(*, actor: str, resident_id: str, ordinal: int,
                       tool_index: int, tool_item_id: str, expected_hash: str) -> dict:
    return _reader('read_tool_evidence')(actor=actor, resident_id=resident_id,
        ordinal=ordinal, tool_index=tool_index, tool_item_id=tool_item_id,
        expected_hash=expected_hash)


def read_message(*, actor: str, resident_id: str, message_id: str) -> dict:
    return _reader('read_message')(actor=actor, resident_id=resident_id, message_id=message_id)
