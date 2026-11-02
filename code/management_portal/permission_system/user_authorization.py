"""Issue a Resident's interpretation of an authenticated User's current input.

Society owns the source proof and checks it under the message lock in its input
transaction. This internal API joins that transaction; it must never be exposed
as a caller-supplied issuer API. Permission owns issuance, replay and its durable
receipt. It does not interpret natural language or contact Society over RPC.
"""
from __future__ import annotations

from uuid import uuid4

from . import inline, temporary


def accept(engine, scheme, connection, *, issuer: str, resident_id: str, message_id: str,
           credentials: list[dict[str, str]], evidence: str, source_digest: str,
           bound_input_message_id: str, execution_id: str, source_resident_id: str,
           expires_in_seconds: int | None = None) -> dict:
    if (not issuer or not resident_id.startswith("resident_") or not message_id or not source_digest
            or not evidence.strip() or not bound_input_message_id or not execution_id or not source_resident_id):
        raise ValueError("需要当前用户输入的完整授权证明")
    if expires_in_seconds is not None:
        temporary._seconds(expires_in_seconds)
    if not isinstance(credentials, list) or not 1 <= len(credentials) <= 20:
        raise ValueError("每次接受 1 至 20 项明确权限")
    requests = []
    for item in credentials:
        if not isinstance(item, dict) or set(item) != {"a", "resource_id"}:
            raise ValueError("每项权限只包含 a 和 resource_id")
        if any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in item.values()):
            raise ValueError("权限动作和资源必须是非空的准确标识")
        requests.append((item["a"], item["resource_id"]))
    if len(set(requests)) != len(requests):
        raise ValueError("权限列表不可重复")

    store = engine.store
    with store.transaction(connection) as connection:
        inline.lock(connection, bound_input_message_id)
        previous = store.user_authorizations(connection, resident_id, message_id)
        if any(row["issuer"] != issuer or row["source_digest"] != source_digest or
               row["scheme_id"] != store.scheme_id or row["execution_id"] != execution_id or
               row["source_resident_id"] != source_resident_id or
               row["bound_input_message_id"] != bound_input_message_id or
               row["lifetime_seconds"] != expires_in_seconds for row in previous):
            raise temporary.LoanRefused("原授权的来源或权限方案已变化，不能重新签发")
        loans = {loan.entry_id: loan for loan in store.entries(connection,
            where="entry_id=ANY(%s)", parameters=([row["credential_id"] for row in previous],))}
        # Receipts survive revocation, expiry, task completion and regeneration.
        # Even a later superset must not recreate an earlier revoked grant.
        now = engine.clock()
        for row in previous:
            loan = loans.get(row["credential_id"])
            if (loan is None or loan.disabled or not loan.live(now) or
                    (loan.p, loan.a, loan.r, loan.issued_by) != (resident_id, row["a_node"], row["r_node"], issuer) or
                    loan.message_id != (bound_input_message_id if expires_in_seconds is None else "")):
                raise temporary.LoanRefused("原授权凭证已失效，不能凭同一条消息重新签发")
        recorded = {(row["a_node"], row["r_node"]): row for row in previous}
        authorization_id = previous[0]["authorization_id"] if previous else "authorization_" + uuid4().hex
        result = []
        replayed = True
        for a, resource in requests:
            if (a, resource) in recorded:
                result.append(temporary.credential(loans[recorded[a, resource]["credential_id"]]))
                continue
            replayed = False
            # Society already proved this exact input is active under the same
            # lock/transaction. A reverse RPC here could deadlock that owner.
            loan = temporary.issue(engine, scheme, issuer=issuer, recipient=resident_id,
                a=a, resource=resource, message_id=bound_input_message_id if expires_in_seconds is None else "",
                seconds=expires_in_seconds,
                input_open=lambda recipient, message: recipient == resident_id and message == bound_input_message_id,
                connection=connection)
            store.record_user_authorization(connection, resident_id=resident_id, message_id=message_id,
                a=a, resource_id=resource, authorization_id=authorization_id, credential_id=loan["credential_id"],
                issuer=issuer, source_digest=source_digest, evidence=evidence,
                source_resident_id=source_resident_id, bound_input_message_id=bound_input_message_id,
                execution_id=execution_id, lifetime_seconds=expires_in_seconds)
            result.append(loan)
        return {"authorization_id": authorization_id, "credentials": result, "replayed": replayed}
