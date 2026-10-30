"""Preserved Family work enters Society recovery with its original identities.

VM constructor (isolated disposable instance only; no account credentials):
create an empty Lineage through CreateLineage, then run with that instance's
Python/environment (PYTHONPATH points to that instance's old code root) and
this file copied from the candidate source::

    python test_application_work_recovery.py --isolated-vm-confirm \
        --lineage-id <created-lineage> --actor <fixture-user>

The constructor calls the real CreateResident/Familize/intake owners. It does
not insert work rows, alter account settings, or start a Provider. Keep the
returned receipt outside the application tree and inspect the durable work
before/after service restart and migration. Current-source tests below do not
constitute a Windows/v60 migration test. The first-turn ``bootstrap_session``
flag is preserved; the installer must independently verify no durable
submission or Provider start. This helper grants no upgrade approval.

:module: execution.persistence
"""
from __future__ import annotations

import json
import uuid
import threading
from copy import deepcopy


def create_waiting_family(lineage_id, actor, *, model="", reasoning_effort=""):
    """Create two active preparations plus four queued requests through owners.

    Requires an isolated, otherwise empty instance with no available accounts.
    The Family's organization request is the steward's first input when the
    installed version generates it. It is retained and counted, alongside one
    ordinary member's work. Charter info remains in its original mailbox.
    """
    from management_portal.society_service import (
        family_quota, lineage_resident_nodes, resident_domain,
        resident_execution_work, resident_input_queue_domain, resident_delivery_work,
        resident_record_domain,
    )
    from management_portal.society_service.domain_ports import execution_port
    from management_portal.society_service.templates.familize import familize

    if execution_port.service().accounts().get("accounts"):
        raise RuntimeError("Fixture requires an isolated instance without available accounts")
    member = lineage_resident_nodes.create_lineage_resident(lineage_id, {
        "title": "Upgrade waiting member", "backend": "codex", "mode": "new_session",
    }, actor)["resident"]["resident_id"]
    settings = {role: {"provider": "codex", "settings": {"model": model,
                **({"reasoning_effort": reasoning_effort} if reasoning_effort else {})}}
                for role in ("router", "steward")} if model else None
    family = familize(lineage_id, [member], actor, title="Upgrade waiting fixture", resident_settings=settings)
    receipt = {"lineage_id": lineage_id, "family_id": family["family_id"], "residents": []}
    for rid in (member, family["steward_resident_id"]):
        record = resident_record_domain.find_resident(rid)
        resident_delivery_work.drain(rid)
        resident_input_queue_domain.commit_resident_intake_state(record, "open")
        existing = resident_execution_work.read(record)
        mids = [existing["user_message_id"]] if resident_execution_work.is_active(existing) else []
        for i in range(len(mids), 3):
            mid = "msg_" + uuid.uuid4().hex
            resident_domain.deliver_resident_message_content(
                rid, actor, f"Synthetic upgrade input {i + 1}", message_id=mid,
                activation_policy="enqueue_and_trigger",
            )
            mids.append(mid)
        resident_delivery_work.drain(rid)
        record = resident_record_domain.find_resident(rid)
        if not family_quota.ensure_role_reserve(record):
            raise RuntimeError("Fixture failed to establish account wait")
        job = resident_execution_work.read(record)
        receipt["residents"].append({"resident_id": rid, "execution_id": job.get("execution_id"),
                                     "input_ids": mids})
    for role in ("facade", "router", "steward"):
        resident_delivery_work.drain(family[role + "_resident_id"])
    return receipt


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-vm-confirm", action="store_true", required=True)
    parser.add_argument("--lineage-id", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--model", default="", help="Optional model from the instance's Codex catalog")
    parser.add_argument("--reasoning-effort", default="")
    args = parser.parse_args()
    print(json.dumps(create_waiting_family(args.lineage_id, args.actor, model=args.model,
                    reasoning_effort=args.reasoning_effort), ensure_ascii=False))
    raise SystemExit(0)


import pytest

from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import (
    family_quota, resident_conversation_stream, resident_domain, resident_delivery_work,
    resident_execution_domain as recovery, resident_execution_work as work,
    resident_input_checkpoints, resident_input_queue_domain, resident_record_domain,
)
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import team  # noqa: F401
from tests.cases.test_family_quota import _account, quota_family  # noqa: F401


def retained(rid):
    record = resident_record_domain.find_resident(rid)
    job = work.read(record)
    return deepcopy({"job": job, "checkpoints": resident_input_checkpoints.checkpoints(record),
                     "inputs": resident_conversation_stream.get_repository().input_state(rid, job["user_message_id"])})


@pytest.mark.parametrize("missing", [True, False], ids=["account-wait", "quota-wait"])
def test_repeated_formal_recovery_preserves_waiting_work(quota_family, missing):
    f = quota_family
    f.accounts[:] = [] if missing else [_account("one", 9)]
    record = resident_record_domain.find_resident(f.steward)
    resident_input_queue_domain.commit_resident_intake_state(record, "open")
    mid = "msg_" + uuid.uuid4().hex
    resident_domain.deliver_resident_message_content(f.steward, ACTOR, "Preserve this input", message_id=mid)
    record = resident_record_domain.find_resident(f.steward)
    assert family_quota.ensure_role_reserve(record)
    resident_delivery_work.drain(f.steward)
    before = retained(f.steward)
    assert before["job"]["user_message_id"] == mid
    assert before["checkpoints"]
    for _ in range(3):
        assert recovery._prepare_resident_continuation(record) is None
        assert recovery.resume_resident_execution(f.steward)["residents"] == 0
    assert retained(f.steward) == before
    assert not before["job"].get("submitted_at")
    assert not before["job"].get("continuation_id")
    with get_agent_data_store().database.read() as c:
        execution = c.execute("SELECT * FROM executions WHERE execution_id=%s", (before["job"]["execution_id"],)).fetchone()
    assert execution["status"] == "preparing" and execution["started_at"] is None


@pytest.fixture
def domain_waiting(owners, monkeypatch):
    monkeypatch.setattr(owners.execution, "accounts", lambda: {"accounts": []})
    return create_waiting_family(owners.lineage_id, ACTOR, model="gpt-6-astra", reasoning_effort="xhigh")


def test_domain_constructor_has_real_mailbox_links_and_preparing_executions(owners, domain_waiting):
    # Successful keyed creation keeps a permanent same-identity receipt.
    from management_portal.society_service import lineage_resident_nodes
    keyed = {'title': 'Completed keyed creation', 'backend': 'codex',
             'creation_key': 'upgrade-completed-creation'}
    first = lineage_resident_nodes.create_lineage_resident(owners.lineage_id, keyed, ACTOR)
    repeated = lineage_resident_nodes.create_lineage_resident(owners.lineage_id, keyed, ACTOR)
    assert repeated['resident']['resident_id'] == first['resident']['resident_id']
    receipt = domain_waiting
    repo = resident_conversation_stream.get_repository()
    for item in receipt["residents"]:
        rid = item["resident_id"]
        before = retained(rid)
        assert before["job"]["execution_id"] == item["execution_id"]
        assert before["job"]["user_message_id"] == item["input_ids"][0]
        assert [x["user_message_id"] for x in work.normalized_queue(before["job"])] == item["input_ids"][1:]
        assert [repo.input_state(rid, mid)["state"] for mid in item["input_ids"]] == ["running", "queued", "queued"]
        assert family_quota.role_migration(resident_record_domain.find_resident(rid))["account_wait"]
        assert recovery.resume_resident_execution(rid)["residents"] == 0
        assert retained(rid) == before
    with owners.scheduler.database.read() as c:
        assert c.execute("SELECT count(*) AS n FROM executions").fetchone()["n"] == 0
    with owners.data.database.read() as c:
        assert c.execute("SELECT count(*) AS n FROM executions WHERE status='preparing'").fetchone()["n"] == 2
    from management_portal.society_service.state_store import get_lineage_data_store
    with get_lineage_data_store().database.read() as c:
        counts = {row["state"]: row["n"] for row in c.execute(
            "SELECT state,count(*) AS n FROM conversation_input_states GROUP BY state").fetchall()}
        assert counts == {"queued": 4, "running": 2}
        assert c.execute("SELECT count(*) AS n FROM resident_mailbox WHERE place='queue'").fetchone()["n"] == 4
        assert c.execute("SELECT count(*) AS n FROM resident_delivery_work").fetchone()["n"] == 0
    # Expose the real isolated schemas under the installer's canonical names
    # only inside this transaction. No work rows are copied or reconstructed.
    import psycopg
    from psycopg import sql
    from scripts.tfi_application_work import inspect
    with psycopg.connect(owners.data.database.dsn) as connection:
        try:
            for database, name in ((get_lineage_data_store().database, "lineage"),
                                   (owners.data.database, "agent"), (owners.scheduler.database, "execution_runtime")):
                connection.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(
                    sql.Identifier(database.schema), sql.Identifier(name)))
            proof = inspect(None, None, connection=connection)
            assert proof["counts"] == {"queued": 4, "preparing": 2}
            assert proof['retained']['resident_creation_operations']['count'] == 1
            assert inspect(None, None, connection=connection) == proof
        finally:
            connection.rollback()


def test_available_account_resumes_same_input_execution_only_once(domain_waiting, owners, monkeypatch):
    """Real preparation/thread/submit owner; only the remote endpoint is replaced."""
    from management_portal.society_service import resident_backend
    targets = domain_waiting["residents"]
    before = {item["resident_id"]: retained(item["resident_id"]) for item in targets}
    arrived = {item["execution_id"]: threading.Event() for item in targets}
    release = threading.Event()
    submissions, resumes = [], []
    accounts = []

    class Endpoint:
        def accounts(self):
            return {"accounts": accounts}

        def submit(self, request):
            submissions.append(request)
            return owners.scheduler.submit(request)

        def resume(self, execution_id, **kwargs):
            resumes.append(execution_id)
            return {"execution_id": execution_id}

        def wait(self, execution_id, **kwargs):
            arrived[execution_id].set()
            if not release.wait(15):
                raise RuntimeError("Isolated endpoint cleanup timeout")
            # End the observer before the isolated database is torn down.
            raise RuntimeError("Isolated endpoint disconnected after submission assertions")

        def acknowledge(self, *args, **kwargs):
            return {}

    monkeypatch.setattr(execution_port, "service", lambda: Endpoint())
    for item in targets:
        record = resident_record_domain.find_resident(item["resident_id"])
        route = resident_backend.execution_account(record)["backend_route_id"]
        account = _account(route, 90)
        if account not in accounts:
            accounts.append(account)
    threads = []
    try:
        for item in targets:
            rid = item["resident_id"]
            assert recovery.resume_resident_execution(rid)["residents"] == 1
            threads.append(work.thread(resident_record_domain.find_resident(rid)))
        for item in targets:
            rid, eid, mid = item["resident_id"], item["execution_id"], item["input_ids"][0]
            record = resident_record_domain.find_resident(rid)
            assert arrived[eid].wait(10), work.read(record)
            requests = [request for request in submissions if request.execution_id == eid]
            assert len(requests) == 1 and requests[0].agent_id == rid
            current = work.read(record)
            assert current["user_message_id"] == mid and current["execution_id"] == eid
            assert current["submitted_at"]
            for _ in range(3):
                assert recovery.resume_resident_execution(rid)["residents"] == 0
            # Reattachment uses the stored binding, without another submit RPC.
            from management_portal.society_service.resident_execution_engine_domain import bind_or_resume_resident_execution
            assert bind_or_resume_resident_execution(Endpoint(), requests[0], record, current) == eid
            assert resident_conversation_stream.get_repository().input_state(rid, mid)["state"] == "running"
            checkpoints = resident_input_checkpoints.checkpoints(record)
            assert checkpoints == before[rid]["checkpoints"]
            assert [(x["execution_id"], x["message_id"]) for x in checkpoints] == [(eid, mid)]
            assert [x["user_message_id"] for x in work.normalized_queue(current)] == item["input_ids"][1:]
            with get_agent_data_store().database.read() as c:
                links = c.execute("SELECT message_id FROM execution_inputs WHERE execution_id=%s", (eid,)).fetchall()
            assert [row["message_id"] for row in links] == [mid]
        assert sorted(request.execution_id for request in submissions) == sorted(arrived)
        assert sorted(resumes) == sorted(arrived)
    finally:
        accounts.clear()
        release.set()
        for thread in threads:
            thread.join(10)
            assert not thread.is_alive()
