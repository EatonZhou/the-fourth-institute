"""Lineage blackboard instructions (decision_ed2c1ed5c245).

The graph objects (the blackboard and its ``attach`` wires) are edited by ``ApplyLineageChanges``;
these handlers read and write the entries.  The permission system has already decided whether the
caller may read or write in the Lineage; what remains here is the Resident's ``attach`` wire, whose
``access`` says whether that Resident may read or write this board, and the author rule of retraction.
"""
from __future__ import annotations

import time
from typing import Any, Iterator

from management_common.service_shutdown import wait_for_shutdown
from management_portal.hatg.contracts import invalid, not_found
from management_portal.hatg.kernel import Invocation

from .. import blackboard_storage, lineage_blackboard_events
from .common import lineage_of_resident, store

STREAM_POLL_SECONDS = 1.0
STREAM_HEARTBEAT_SECONDS = 15.0
_READ = frozenset({"read", "read_write"})
_WRITE = frozenset({"write", "read_write"})


def _is_resident(caller: str) -> bool:
    return str(caller or "").startswith("resident_")


def _wrap(error: Exception):
    """Domain errors of the storage layer, in the instruction vocabulary."""

    if isinstance(error, KeyError):
        return not_found(str(error).strip("'\""))
    if isinstance(error, ValueError):
        return invalid(str(error))
    return error


def _require_access(connection: Any, inv: Invocation, board_id: str, allowed: frozenset[str], verb: str) -> None:
    """A Resident caller needs an ``attach`` wire to the board that allows ``verb``; users need none."""

    if not _is_resident(inv.caller):
        return
    access = blackboard_storage.attachment_access(connection, board_id, inv.caller)
    if access not in allowed:
        raise PermissionError(
            f"这个 Resident 没有可以{verb}黑板 {board_id} 的 attach 连线（需要 "
            + " 或 ".join(sorted(allowed)) + "）；连线由 Lineage 编辑者用 ApplyLineageChanges 维护")


def create_board_entry(inv: Invocation) -> dict[str, Any]:
    lineage_id, board_id = inv.argument("lineage_id"), inv.argument("board_id")

    def write(connection: Any) -> dict[str, Any]:
        blackboard_storage.board_record(connection, board_id, lineage_id)
        _require_access(connection, inv, board_id, _WRITE, "写")
        return blackboard_storage.append_entry(
            connection, board_id=board_id, lineage_id=lineage_id, author_id=inv.caller,
            kind=inv.argument("kind") or "", text=inv.argument("text"), data=inv.argument("data"),
            refs=inv.argument("refs"), supersedes=inv.argument("supersedes"), expires_at=inv.argument("expires_at"))

    try:
        result = store().database.write(write)
    except (KeyError, ValueError) as error:
        raise _wrap(error) from None
    # An Event Source observing entries should see this one at once.
    with store().database.read() as connection:
        lineage_blackboard_events.notify_written(connection, lineage_id)
    return result


def list_board_entries(inv: Invocation) -> dict[str, Any]:
    lineage_id, board_id = inv.argument("lineage_id"), inv.argument("board_id")
    try:
        with store().database.read() as connection:
            blackboard_storage.board_record(connection, board_id, lineage_id)
            _require_access(connection, inv, board_id, _READ, "读")
            return blackboard_storage.list_entries(
                connection, board_id=board_id, lineage_id=lineage_id, reader_id=inv.caller,
                cursor=inv.argument("cursor"), limit=inv.argument("limit"),
                author_id=inv.argument("author_id") or "", kind=inv.argument("kind") or "")
    except (KeyError, ValueError) as error:
        raise _wrap(error) from None


def watch_board(inv: Invocation) -> Iterator[list[dict[str, Any]]]:
    lineage_id, board_id = inv.argument("lineage_id"), inv.argument("board_id")
    kind = inv.argument("kind") or ""
    cursor = int(inv.argument("after_cursor") or 0)
    last_yield = time.monotonic()
    while True:
        try:
            with store().database.read() as connection:
                blackboard_storage.board_record(connection, board_id, lineage_id)
                _require_access(connection, inv, board_id, _READ, "读")
                page = blackboard_storage.list_entries(
                    connection, board_id=board_id, lineage_id=lineage_id, reader_id=inv.caller,
                    cursor=str(cursor), limit=200, kind=kind)
        except (KeyError, ValueError) as error:
            raise _wrap(error) from None
        entries = page["entries"]
        if cursor > page["latest_seq"]:
            # A cursor ahead of the board (a stale or foreign one) would wait forever: restart from the end.
            cursor = page["latest_seq"]
            last_yield = time.monotonic()
            yield [{"cursor": cursor, "snapshot_required": True, "entries": [], "earliest_seq": page["earliest_seq"]}]
        elif entries:
            cursor = entries[-1]["seq"]
            last_yield = time.monotonic()
            yield [{"cursor": cursor, "entries": entries, "earliest_seq": page["earliest_seq"]}]
            if page["next_cursor"]:
                continue
        elif time.monotonic() - last_yield >= STREAM_HEARTBEAT_SECONDS:
            last_yield = time.monotonic()
            yield []
        if wait_for_shutdown(STREAM_POLL_SECONDS):
            return


def set_board_read_position(inv: Invocation) -> dict[str, Any]:
    lineage_id, board_id = inv.argument("lineage_id"), inv.argument("board_id")

    def write(connection: Any) -> dict[str, Any]:
        blackboard_storage.board_record(connection, board_id, lineage_id)
        _require_access(connection, inv, board_id, _READ, "读")
        return blackboard_storage.set_read_position(
            connection, board_id=board_id, lineage_id=lineage_id, reader_id=inv.caller, seq=inv.argument("seq"))

    try:
        return store().database.write(write)
    except (KeyError, ValueError) as error:
        raise _wrap(error) from None


def delete_board_entry(inv: Invocation) -> dict[str, Any]:
    lineage_id, board_id = inv.argument("lineage_id"), inv.argument("board_id")
    member = False
    if _is_resident(inv.caller):
        try:
            member = lineage_of_resident(inv.caller) == lineage_id
        except Exception:  # a Resident that no longer exists is no member
            member = False

    def write(connection: Any) -> dict[str, Any]:
        blackboard_storage.board_record(connection, board_id, lineage_id)
        if member:
            _require_access(connection, inv, board_id, _WRITE, "写")
        return blackboard_storage.retract_entry(
            connection, board_id=board_id, lineage_id=lineage_id, entry_id=inv.argument("entry_id"),
            actor_id=inv.caller, any_author=member)

    try:
        return store().database.write(write)
    except (KeyError, ValueError) as error:
        raise _wrap(error) from None


HANDLERS = {
    "CreateBoardEntry": create_board_entry,
    "ListBoardEntries": list_board_entries,
    "SetBoardReadPosition": set_board_read_position,
    "DeleteBoardEntry": delete_board_entry,
}
STREAMS = {"WatchBoard": watch_board}
