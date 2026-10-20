"""Changes other services make to the saved instances inside their own transactions.

Both run with the permission system on or off, on the rows of every
scheme's saved instance, in plain SQL on the permission schema; nothing
here imports the PAR implementation.

- ``end_with_input``: SocietyService moves a Resident's input to completed,
  failed or cancelled, and the temporary credentials lent to that Resident
  with that message end in the same transaction (decision_b820faf42218
  §5.6).  Issuing a credential for a message takes the same lock (``lock``),
  so a credential issued while the input ends is either seen and deleted,
  or refused because the input has ended.
- ``rename_paths``: a Project's ID changes; the tree paths that name it are
  rewritten so the renamed Project keeps its place and its rows.

Either change bumps the instance version when it touched rows, so every
process drops its decision cache.
"""
from __future__ import annotations

from typing import Any

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"


def _schema() -> str:
    from management_common.portal_database import _database_schema

    return '"' + _database_schema("permission") + '"'


def _exists(connection: Any, table: str) -> bool:
    return bool(connection.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0])


def _bump(connection: Any, schema: str) -> None:
    connection.execute(f"INSERT INTO {schema}.state (singleton, version) VALUES (TRUE, 1)"
                       f" ON CONFLICT (singleton) DO UPDATE SET version = {schema}.state.version + 1")


def lock(connection: Any, message_id: str) -> None:
    connection.execute(_LOCK, ("permission-message:" + message_id,))


def end_with_input(connection: Any, resident_id: str, message_id: str) -> int:
    """Delete the credentials lent to ``resident_id`` with ``message_id``; returns how many."""
    if not message_id or not resident_id:
        return 0
    schema = _schema()
    if not _exists(connection, f"{schema}.scheme_entries"):
        return 0
    lock(connection, message_id)
    rows = connection.execute(f"DELETE FROM {schema}.scheme_entries WHERE message_id = %s AND p_node = %s"
                              " AND expires_at IS NOT NULL RETURNING entry_id", (message_id, resident_id)).fetchall()
    if rows:
        _bump(connection, schema)
    return len(rows)


def rename_paths(connection: Any, old_id: str, new_id: str, *, apply: bool) -> int:
    """Rewrite the stored paths naming ``old_id``; returns how many name it (without ``apply``, only counts)."""
    if connection is None:
        return 0
    schema = _schema()
    if not _exists(connection, f"{schema}.scheme_nodes"):
        return 0
    if not apply:
        return int(connection.execute(f"SELECT count(*) FROM {schema}.scheme_nodes WHERE path @> ARRAY[%s]::text[]",
                                      (old_id,)).fetchone()[0])
    renamed = connection.execute(f"UPDATE {schema}.scheme_nodes SET path = array_replace(path, %s, %s)"
                                 " WHERE path @> ARRAY[%s]::text[]", (old_id, new_id, old_id)).rowcount
    if renamed:
        _bump(connection, schema)
    return renamed
