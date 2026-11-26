"""Changes other services make to the saved instances inside their own transactions.

All of them run with the permission system on or off, on the rows of every
scheme's saved instance, in plain SQL on the permission schema; nothing
here imports the PAR implementation.

- ``end_with_input``: SocietyService moves a Resident's input to completed,
  failed or cancelled, and the temporary credentials lent to that Resident
  with that message end in the same transaction (decision_b820faf42218
  §5.6).  Issuing a credential for a message takes the same lock (``lock``),
  so a credential issued while the input ends is either seen and deleted,
  or refused because the input has ended.
- ``activate_prepared``: SocietyService admits a Resident's input, and the
  pending task credentials prepared for that message are enabled in the
  admission transaction.
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
    if _exists(connection, f"{schema}.prepared_task_credentials"):
        connection.execute(f"UPDATE {schema}.prepared_task_credentials SET state='ended' "
                           "WHERE recipient=%s AND message_id=%s AND state<>'ended'", (resident_id, message_id))
    if rows:
        _bump(connection, schema)
    return len(rows)


def activate_prepared(connection: Any, resident_id: str, message_id: str) -> int:
    """Trusted Society admission hook, joining its input transaction without initialization.

    Only the recorded pending entries are enabled. Revoked/expired grants
    become terminal receipts; unrelated disabled rows are never enabled.
    """
    if connection is None or not resident_id or not message_id:
        raise ValueError("受理启用需要真实输入事务、Resident 和消息 ID")
    schema = _schema()
    lock(connection, message_id)
    if not _exists(connection, f"{schema}.prepared_task_credentials"):
        return 0
    rows = connection.execute(f"SELECT * FROM {schema}.prepared_task_credentials "
                              "WHERE recipient=%s AND message_id=%s AND state='pending' FOR UPDATE",
                              (resident_id, message_id)).fetchall()
    count = 0
    for receipt in rows:
        identity = (receipt["scheme_id"], receipt["credential_id"])
        loan = connection.execute(f"SELECT *, expires_at>clock_timestamp() AS live FROM {schema}.scheme_entries "
                                  "WHERE scheme_id=%s AND entry_id=%s FOR UPDATE", identity).fetchone()
        state = "ended"
        if loan is not None and loan["live"]:
            if (not loan["disabled"] or loan["effect"] != "permit" or
                    (loan["p_node"], loan["message_id"], loan["a_node"], loan["r_node"], loan["issued_by"]) !=
                    (resident_id, message_id, receipt["a_node"], receipt["r_node"], receipt["issuer"])):
                raise ValueError("待启用凭证与原任务回执不匹配，拒绝受理")
            connection.execute(f"UPDATE {schema}.scheme_entries SET disabled=FALSE,version=version+1 "
                               "WHERE scheme_id=%s AND entry_id=%s", identity)
            state = "active"
            count += 1
        connection.execute(f"UPDATE {schema}.prepared_task_credentials SET state=%s "
                           "WHERE scheme_id=%s AND credential_id=%s", (state, *identity))
    if count:
        _bump(connection, schema)
    return count


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
