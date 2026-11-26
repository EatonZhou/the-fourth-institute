"""Temporary credentials (decision_b820faf42218 §2, §5.6).

A principal that holds an A node on a resource may lend it to another
principal: the credential is one permit row ``(recipient, a, resource)`` of
the active scheme's instance with an issuer and an expiry. Duration grants
and trusted accepted-input grants take effect at once. Public task grants
are disabled until canonical input acceptance commits in the same transaction.
Issuing checks that the issuer holds that A node
on that resource now.  The issuer's deny rows are not copied and the
credential's expiry does not depend on the issuer's own rows. Durable
question approval rechecks the issuer when accepting its prepared input;
losing the right later does not touch credentials already activated.

A credential lasts for a task or for a time:

- one bound to an exact prepared input (``message_id``) is deleted
  in the transaction that ends the Resident's input of that message
  (completed, failed or cancelled), see ``inline.py``;
- one issued for a time lasts that long, and its issuer may renew it.

Either way it ends no later than seven days after it was issued.  Expired
rows decide nothing and the daily maintenance deletes them.  The issuer takes
one back and the recipient hands it back; nobody else revokes it.
Prepared-task receipts survive deletion so replay never recreates a grant.

A personal AMA's Resident and its owner User are one party (§5.5).  The row
names whoever called: an AMA Resident issuing is the issuer, and a
credential lent to it is its own.  A credential lent to the owner holds for
the owner's AMA Residents too (the engine judges them as the owner), so one
is never lent between them; and either of them may renew or take back what
the other issued, or hand back what the other received.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from . import inline
from .engine import Engine, Grants
from .engine.model import DENY, PERMIT, Entry
from .engine.store import ReadOnlyStore, Store, new_entry_id
from .scheme.compiler import CompiledScheme

DEFAULT_SECONDS = 24 * 3600
MAX_SECONDS = 7 * 24 * 3600


class LoanRefused(PermissionError):
    pass


def delegated_ids(engine: Engine, *, principal: str, owners: Mapping[str, str], action: str) -> set[str]:
    """Exact resources their owners lent to this principal for this action.

    A private resource's visibility cannot come from an administrator, a
    group grant, an ancestor resource or a recipient's onward loan. Only
    temporary rows addressed to this principal (or its represented User)
    and issued by that resource's owner qualify. Current deny rows still
    apply. Reading grants checks the instance version on every call, and
    expiry is checked even when those grants are cached.
    """
    if not principal or not owners or not action:
        return set()
    from .personal_ama import owner_of, owners_of

    _path, grants, paths = engine.grants(principal, owners)
    now = engine.clock()
    recipients = {principal, owner_of(principal)} - {""}
    candidates = [entry for entry in grants.entries
                  if entry.effect == PERMIT and entry.expires_at is not None and not entry.disabled
                  and entry.live(now) and entry.issued_by and entry.p in recipients and entry.r in owners]
    if not candidates:
        return set()
    issuers = owners_of(entry.issued_by for entry in candidates)
    loans: dict[str, list[Entry]] = {}
    for entry in candidates:
        if (issuers.get(entry.issued_by) or entry.issued_by).casefold() == owners[entry.r].casefold():
            loans.setdefault(entry.r, []).append(entry)
    denies = [entry for entry in grants.entries if entry.effect == DENY and not entry.disabled]
    a_path = engine.a_path(action)
    # Even in a batch containing both parent and child, a loan for the parent
    # must not open the child. Resource ancestry is still used for deny rows.
    return {resource for resource, entries in loans.items()
            if Grants([*entries, *denies], engine.a_path)
            .decide(a_path, paths[resource], now).allowed}


def _party(principal: str) -> str:
    """Who ``principal`` acts as: the owner of a personal AMA's Resident, otherwise itself."""
    from management_portal.permission_system.personal_ama import party

    return party(principal).casefold()


def _seconds(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 60 <= value <= MAX_SECONDS:
        raise ValueError(f"有效期为 60 至 {MAX_SECONDS} 秒")
    return value


def _new_loan(engine, scheme, *, issuer, recipient, a, resource, seconds, message_id):
    """Validate the same identity, capability and lifetime for both issuance paths."""
    if a not in scheme.trees["A"].nodes:
        raise ValueError(f"{a} 不是 A 树的节点")
    if recipient == issuer:
        raise ValueError("不能给自己签发临时凭证")
    if _party(recipient) == _party(issuer):
        raise ValueError("个人 AMA 的 Resident 与其主人是同一身份，主人的权限它已经拥有，不能在两者之间签发临时凭证")
    if message_id and not recipient.startswith("resident_"):
        raise ValueError("随消息签发的临时凭证只能交给收到这条消息的 Resident")
    lasting = MAX_SECONDS if message_id and seconds is None else _seconds(DEFAULT_SECONDS if seconds is None else seconds)
    _path, grants, paths = engine.grants(issuer, [resource])
    now = engine.clock()
    if not grants.decide(engine.a_path(a), paths[resource], now).allowed:
        raise LoanRefused(f"签发者对 {resource} 没有 {a}，不能签发")
    return Entry(new_entry_id(), PERMIT, recipient, a, resource, issued_by=issuer, issued_at=now,
                 expires_at=now + lasting, message_id=message_id)


def issue(engine: Engine, scheme: CompiledScheme, *, issuer: str, recipient: str, a: str, resource: str,
          seconds: Any = None, message_id: str = "",
          input_open: Callable[[str, str], bool] | None = None, connection=None) -> dict[str, Any]:
    """Issue an immediate duration grant or a trusted, already accepted input's grant."""
    loan = _new_loan(engine, scheme, issuer=issuer, recipient=recipient, a=a, resource=resource,
                     seconds=seconds, message_id=message_id)
    with engine.store.transaction(connection) as connection:
        if message_id:
            # Issuing for a message and ending that message's input take turns, so no credential outlives its task.
            inline.lock(connection, message_id)
            if input_open is None or not input_open(recipient, message_id):
                raise LoanRefused(f"{recipient} 没有处理中的消息 {message_id}，不能随它签发")
        engine.store.add(connection, [loan])
        return credential(_loan(engine.store, connection, loan.entry_id))


def issue_prepared(engine: Engine, scheme: CompiledScheme, *, issuer: str, recipient: str,
                   a: str, resource: str, message_id: str, seconds=None,
                   input_prepared: Callable[[str, str, str], bool]) -> dict[str, Any]:
    """Stage a disabled grant; only canonical input admission may activate it.

    The durable receipt survives revocation and cleanup. A release retry can
    read its existing grant, but cannot mint another or extend its deadline.
    """
    if not message_id or not recipient.startswith("resident_"):
        raise ValueError("任务凭证需要已准备的 Resident 输入")
    if seconds is not None:
        _seconds(seconds)
    store = engine.store
    with store.transaction() as connection:
        inline.lock(connection, message_id)
        row = store.execute(connection, "SELECT * FROM {s}.prepared_task_credentials "
                            "WHERE issuer=%s AND recipient=%s AND message_id=%s AND a_node=%s AND r_node=%s",
                            (issuer, recipient, message_id, a, resource)).fetchone()
        if row is not None:
            if row["scheme_id"] != store.scheme_id or row["lifetime_seconds"] != seconds or row["state"] == "ended":
                raise LoanRefused("原任务凭证已结束或请求变化，不能重新签发")
            try:
                loan = _loan(store, connection, row["credential_id"])
            except KeyError as exc:
                raise LoanRefused("原任务凭证已被收回，不能重新签发") from exc
            if (loan.expires_at is None or loan.expires_at <= engine.clock() or
                    (loan.p, loan.a, loan.r, loan.issued_by, loan.message_id) !=
                    (recipient, a, resource, issuer, message_id) or loan.disabled != (row["state"] == "pending")):
                raise LoanRefused("原任务凭证已失效或不匹配，不能重新签发")
            return {**credential(loan), "state": row["state"]}
        if not input_prepared(recipient, message_id, issuer):
            raise LoanRefused("只能向本人准备且尚未受理的准确 Resident 输入签发任务凭证")
        store.lock_version(connection)
        active = store.execute(connection, "SELECT scheme_id FROM {s}.active_scheme").fetchone()
        if active is not None and active["scheme_id"] != store.scheme_id:
            raise LoanRefused("启用方案已变化，请重新读取权限")
        loan = replace(_new_loan(engine, scheme, issuer=issuer, recipient=recipient, a=a,
                                resource=resource, seconds=seconds, message_id=message_id), disabled=True)
        store.add(connection, [loan])
        store.execute(connection, "INSERT INTO {s}.prepared_task_credentials "
                      "(issuer,recipient,message_id,a_node,r_node,scheme_id,credential_id,lifetime_seconds,state) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending')",
                      (issuer, recipient, message_id, a, resource, store.scheme_id, loan.entry_id, seconds))
        return {**credential(_loan(store, connection, loan.entry_id)), "state": "pending"}


def activate_approved(connection, *, issuer: str, resident_id: str, message_id: str,
                      credentials: list[dict[str, str]]) -> int:
    """Validate a durable question's entire approved scope, then activate it atomically.

    Society supplies the stored issuer, final recipient, answer message and
    approval after checking the immutable question and accepting that exact
    input. It must call this inside that transaction, before wakeup. No public
    caller may supply this proof. Locks and reads use the supplied connection;
    this path never initializes or migrates a store or consults decision caches.
    """
    from .personal_ama import readonly_owners_of
    from .scheme.repository import Schemes

    if connection is None or any(not isinstance(value, str) or not value or value != value.strip()
                                 for value in (issuer, resident_id, message_id)):
        raise ValueError("批准受理需要真实事务、签发者、最终 Resident 和消息 ID")
    if not isinstance(credentials, list) or any(
            not isinstance(item, dict) or set(item) != {"a", "resource_id"} or
            any(not isinstance(value, str) or not value or value != value.strip() for value in item.values())
            for item in credentials):
        raise ValueError("批准凭证必须为准确的 {a, resource_id} 列表")
    expected = {(item["a"], item["resource_id"]) for item in credentials}
    if len(expected) != len(credentials):
        raise ValueError("批准凭证不能重复")
    inline.lock(connection, message_id)
    store = ReadOnlyStore()  # Read the installed owner tables on the admission connection; no ensure.
    receipts = store.execute(connection, "SELECT * FROM {s}.prepared_task_credentials "
                             "WHERE recipient=%s AND message_id=%s "
                             "ORDER BY issuer,a_node,r_node FOR UPDATE", (resident_id, message_id)).fetchall()
    entries = store.execute(connection, "SELECT * FROM {s}.scheme_entries "
                            "WHERE p_node=%s AND message_id=%s ORDER BY scheme_id,entry_id FOR UPDATE",
                            (resident_id, message_id)).fetchall()
    # Entry locks precede the version fence, as in revoke/delete. Holding the
    # fence before waiting for an entry deleted by a writer would invert locks.
    if store.execute(connection, "SELECT version FROM {s}.state FOR UPDATE").fetchone() is None:
        raise LoanRefused("权限实例尚未安装，不能受理批准凭证")
    schemes = Schemes(store, migrate=False)
    scheme_id = schemes.active(connection)
    if (len(receipts) != len(expected) or
            {(row["a_node"], row["r_node"]) for row in receipts} != expected or
            any(row["issuer"] != issuer or row["scheme_id"] != scheme_id for row in receipts)):
        raise LoanRefused("原任务凭证与持久问题批准的完整范围或签发者不符")
    by_id = {(row["scheme_id"], row["entry_id"]): row for row in entries}
    if set(by_id) != {(row["scheme_id"], row["credential_id"]) for row in receipts}:
        raise LoanRefused("原任务凭证已撤销、缺失或含未批准的条目")
    states = {row["state"] for row in receipts}
    if receipts and states not in ({"pending"}, {"active"}):
        raise LoanRefused("原任务凭证已结束或状态不一致")
    for receipt in receipts:
        loan = by_id[scheme_id, receipt["credential_id"]]
        if (loan["effect"] != PERMIT or loan["disabled"] != (receipt["state"] == "pending") or
                loan["expires_at"] is None or
                (loan["p_node"], loan["a_node"], loan["r_node"], loan["issued_by"], loan["message_id"]) !=
                (resident_id, receipt["a_node"], receipt["r_node"], issuer, message_id)):
            raise LoanRefused("原任务凭证已过期或与批准内容不符")
    if not expected:
        return 0
    scheme = schemes.compiled(scheme_id, connection)
    store = store.scoped(scheme_id)
    owner = readonly_owners_of([issuer]).get(issuer)
    principals = [issuer, owner] if owner else [issuer]
    stored_paths = store.paths("P", principals, connection)
    path = set(node for principal in principals for node in
               (stored_paths.get(principal) or scheme.trees["P"].element_path(principal)))
    grants = Grants(store.entries(connection, where="p_node=ANY(%s) AND NOT disabled", parameters=(list(path),)),
                    scheme.trees["A"].element_path)
    resource_paths = store.paths("R", [r for _a, r in expected if r not in scheme.trees["R"].nodes], connection)
    # Use decision time after all owner reads; a deadline can pass while an
    # owner read waits even though these credential rows remain locked.
    now = float(store.execute(connection, "SELECT extract(epoch FROM clock_timestamp()) AS now").fetchone()["now"])
    if any(loan["expires_at"].timestamp() <= now for loan in entries):
        raise LoanRefused("原任务凭证已过期，不能受理批准凭证")
    for a, resource in expected:
        if (a not in scheme.trees["A"].nodes or not grants.decide(
                scheme.trees["A"].element_path(a), resource_paths.get(resource) or
                scheme.trees["R"].element_path(resource), now).allowed):
            raise LoanRefused(f"签发者对 {resource} 已无 {a}，不能受理批准凭证")
    if states == {"active"}:
        return 0  # Validate a release replay without reissuing or extending anything.
    credential_ids = [row["credential_id"] for row in receipts]
    store.execute(connection, "UPDATE {s}.scheme_entries SET disabled=FALSE,version=version+1 "
                  "WHERE scheme_id=%s AND entry_id=ANY(%s)", (scheme_id, credential_ids))
    store.execute(connection, "UPDATE {s}.prepared_task_credentials SET state='active' "
                  "WHERE scheme_id=%s AND credential_id=ANY(%s)", (scheme_id, credential_ids))
    store.bump(connection)
    return len(credential_ids)


def message_credentials(engine: Engine, *, issuer: str, recipient: str, message_id: str,
                        requests: list[dict[str, str]], input_open, connection) -> list[dict[str, Any]]:
    """Replay an accepted delivery without reissuing revoked/expired permissions."""
    inline.lock(connection, message_id)
    if not input_open(recipient, message_id):
        raise LoanRefused("原回答输入已结束，请重新请求权限")
    loans = engine.store.entries(connection, where=(
        "p_node=%s AND issued_by=%s AND message_id=%s AND expires_at>now() AND NOT disabled"),
        parameters=(recipient, issuer, message_id))
    by_permission = {(loan.a, loan.r): loan for loan in loans}
    if set(by_permission) != {(item['a'], item['resource_id']) for item in requests}:
        raise LoanRefused("原回答的凭证已失效或与本次请求不符，请重新请求权限")
    return [credential(by_permission[item['a'], item['resource_id']]) for item in requests]


def credential(loan: Entry) -> dict[str, Any]:
    """Describe a live grant without mistaking its storage deadline for task completion.

    The engine's expires_at remains the hard cutoff for both kinds of grant.
    A task can end earlier at an unknown time; a receipt cannot promise that
    the grant will survive until that cutoff (or a later background call).
    """
    value = {"credential_id": loan.entry_id, "principal_id": loan.p, "a": loan.a, "resource_id": loan.r,
             "issued_by": loan.issued_by, "issued_at": loan.issued_at,
             "state": "inactive" if loan.disabled else "active",
             "lifetime": "task" if loan.message_id else "duration",
             "deadline_at": loan.expires_at, "expires_at": None if loan.message_id else loan.expires_at}
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
        if _party(actor) != _party(loan.issued_by):
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
        if _party(actor) not in {_party(loan.issued_by), _party(loan.p)}:
            raise PermissionError("只有签发者或接收者可以撤销临时凭证")
        store.delete(connection, where="entry_id = %s", parameters=(credential_id,))
    return {"revoked": credential_id}


def listing(store: Store, principal: str) -> list[dict[str, Any]]:
    """Live credentials the principal issued or received; a personal AMA's Resident also sees its owner's."""
    from management_portal.permission_system.personal_ama import owner_of

    names = list(dict.fromkeys(item for item in (principal, owner_of(principal)) if item))
    with store.read() as connection:
        loans = store.entries(
            connection, where="expires_at IS NOT NULL AND (p_node = ANY(%s) OR issued_by = ANY(%s)) AND expires_at > now()",
            parameters=(names, names))
        pending = {row["credential_id"] for row in store.execute(connection,
            "SELECT credential_id FROM {s}.prepared_task_credentials WHERE scheme_id=%s AND state='pending'",
            (store.scheme_id,)).fetchall()}
    return [{**credential(loan), **({"state": "pending"} if loan.entry_id in pending else {}),
             "direction": "received" if loan.p in names else "issued"} for loan in loans]


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
