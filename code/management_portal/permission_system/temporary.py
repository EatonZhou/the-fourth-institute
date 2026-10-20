"""Temporary credentials (decision_b820faf42218 §2, §5.6).

A principal that holds an A node on a resource may lend it to another
principal: the credential is one permit row ``(recipient, a, resource)`` of
the active scheme's instance with an issuer and an expiry, and it takes
effect at once.  Issuing checks one thing, that the issuer holds that A node
on that resource now.  The issuer's deny rows are not copied and the
credential's expiry does not depend on the issuer's own rows; losing the
right later does not touch credentials already issued.

A credential lasts for a task or for a time:

- one that travels with a message to a Resident (``message_id``) is deleted
  in the transaction that ends the Resident's input of that message
  (completed, failed or cancelled), see ``inline.py``;
- one issued for a time lasts that long, and its issuer may renew it.

Either way it ends no later than seven days after it was issued.  Expired
rows decide nothing and the daily maintenance deletes them.  The issuer takes
one back and the recipient hands it back; nobody else revokes it.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from . import inline
from .engine import Engine
from .engine.model import PERMIT, Entry
from .engine.store import Store, new_entry_id
from .scheme.compiler import CompiledScheme

DEFAULT_SECONDS = 24 * 3600
MAX_SECONDS = 7 * 24 * 3600


class LoanRefused(PermissionError):
    pass


def _seconds(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 60 <= value <= MAX_SECONDS:
        raise ValueError(f"有效期为 60 至 {MAX_SECONDS} 秒")
    return value


def issue(engine: Engine, scheme: CompiledScheme, *, issuer: str, recipient: str, a: str, resource: str,
          seconds: Any = None, message_id: str = "",
          input_open: Callable[[str, str], bool] | None = None) -> dict[str, Any]:
    """Lend ``a`` on ``resource`` to ``recipient``.

    With ``message_id`` the credential lasts while ``recipient``'s input of that
    message is open (``input_open`` answers), at most seven days; otherwise it
    lasts ``seconds`` (a day when omitted).
    """
    if a not in scheme.trees["A"].nodes:
        raise ValueError(f"{a} 不是 A 树的节点")
    if recipient == issuer:
        raise ValueError("不能给自己签发临时凭证")
    if message_id and not recipient.startswith("resident_"):
        raise ValueError("随消息签发的临时凭证只能交给收到这条消息的 Resident")
    lasting = MAX_SECONDS if message_id and seconds is None else _seconds(DEFAULT_SECONDS if seconds is None else seconds)
    _path, grants, paths = engine.grants(issuer, [resource])
    now = engine.clock()
    if not grants.decide(engine.a_path(a), paths[resource], now).allowed:
        raise LoanRefused(f"签发者对 {resource} 没有 {a}，不能签发")
    loan = Entry(new_entry_id(), PERMIT, recipient, a, resource, issued_by=issuer, issued_at=now,
                 expires_at=now + lasting, message_id=message_id)
    with engine.store.transaction() as connection:
        if message_id:
            # Issuing for a message and ending that message's input take turns, so no credential outlives its task.
            inline.lock(connection, message_id)
            if input_open is None or not input_open(recipient, message_id):
                raise LoanRefused(f"{recipient} 没有处理中的消息 {message_id}，不能随它签发")
        engine.store.add(connection, [loan])
    return credential(loan)


def credential(loan: Entry) -> dict[str, Any]:
    value = {"credential_id": loan.entry_id, "principal_id": loan.p, "a": loan.a, "resource_id": loan.r,
             "issued_by": loan.issued_by, "issued_at": loan.issued_at, "expires_at": loan.expires_at}
    if loan.message_id:
        value["message_id"] = loan.message_id
    return value


def _loan(store: Store, connection, credential_id: str) -> Entry:
    found = store.entries(connection, where="entry_id = %s AND expires_at IS NOT NULL", parameters=(credential_id,))
    if not found:
        raise KeyError(credential_id)
    return found[0]


def renew(store: Store, credential_id: str, *, actor: str, seconds: Any) -> dict[str, Any]:
    """The issuer extends a credential issued for a time, to at most seven days after it was issued."""
    lasting = _seconds(seconds)
    now = time.time()
    with store.transaction() as connection:
        loan = _loan(store, connection, credential_id)
        if actor != loan.issued_by:
            raise PermissionError("只有签发者可以续期临时凭证")
        if loan.message_id:
            raise ValueError("随消息签发的临时凭证在任务结束时失效，不能续期")
        if loan.expires_at is None or loan.expires_at <= now:
            raise ValueError("临时凭证已过期，不能续期")
        expires = min(now + lasting, loan.issued_at + MAX_SECONDS)
        row = store.execute(connection, "UPDATE {s}.scheme_entries SET expires_at = to_timestamp(%s) WHERE scheme_id = %s"
                                        " AND entry_id = %s RETURNING entry_id", (expires, store.scheme_id, credential_id)).fetchone()
        if row is None:
            raise KeyError(credential_id)
        store.bump(connection)
    return credential(Entry(loan.entry_id, loan.effect, loan.p, loan.a, loan.r, issued_by=loan.issued_by,
                            issued_at=loan.issued_at, expires_at=expires))


def revoke(store: Store, credential_id: str, *, actor: str) -> dict[str, Any]:
    """The issuer takes a credential back and the recipient hands it back; nobody else revokes it."""
    with store.transaction() as connection:
        loan = _loan(store, connection, credential_id)
        if actor not in {loan.issued_by, loan.p}:
            raise PermissionError("只有签发者或接收者可以撤销临时凭证")
        store.delete(connection, where="entry_id = %s", parameters=(credential_id,))
    return {"revoked": credential_id}


def listing(store: Store, principal: str) -> list[dict[str, Any]]:
    """Live credentials the principal issued or received."""
    with store.read() as connection:
        loans = store.entries(
            connection, where="expires_at IS NOT NULL AND (p_node = %s OR issued_by = %s) AND expires_at > now()",
            parameters=(principal, principal))
    return [{**credential(loan), "direction": "received" if loan.p == principal else "issued"} for loan in loans]


def expire(store: Store) -> int:
    """Delete credentials past their expiry in every scheme's instance; decisions already ignore them."""
    with store.transaction() as connection:
        rows = store.execute(connection, "DELETE FROM {s}.scheme_entries WHERE expires_at <= %s RETURNING entry_id",
                             (_timestamp(time.time()),)).fetchall()
        if rows:
            store.bump(connection)
    return len(rows)


def _timestamp(value: float):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value, tz=timezone.utc)
