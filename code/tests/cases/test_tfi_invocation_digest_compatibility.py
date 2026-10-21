"""Frozen search plans survive the format namespace transition without rewriting."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import threading

import pytest

from management_portal import service_invocation_contract as contract
from management_portal import shared_search_diagnostics as diagnostics
from management_portal.society_service import service_invocation_domain as domain
from management_portal.society_service.service_invocation_store import ServiceInvocationConflict
from tests.cases.test_shared_search_invocation import job, runtime


OLD = "archive.service-invocation-plan.v1"
NEW = "tfi.service-invocation-plan.v1"


def wire_digest(identity, manifest, schema):
    payload = {"schema": schema, **identity, "batches": manifest}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def saved_rows():
    with domain._store().database.snapshot() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT xmin::text AS row_version, * FROM service_invocations ORDER BY actor,surface,search_id,generation")]


def test_canonical_reprepare_keeps_digest_and_message_identity(runtime):
    identity = job()
    manifest = contract.batch_manifest(identity, ["one", "two"])
    original = runtime.owner.prepare_batches(job=identity, surface="knowledge", batches=manifest)
    before = saved_rows()
    assert runtime.owner.prepare_batches(job=identity, surface="knowledge", batches=manifest) == original
    assert saved_rows() == before
    with pytest.raises(ServiceInvocationConflict):
        runtime.owner.prepare_batches(job=identity, surface="knowledge",
                                      batches=contract.batch_manifest(identity, ["changed", "two"]))


def test_retired_plan_digest_is_rejected(runtime, monkeypatch):
    identity = job()
    manifest = contract.batch_manifest(identity, ["one", "two"])
    monkeypatch.setattr(domain, "plan_fingerprint", lambda job, batches: wire_digest(job, batches, OLD))
    original = runtime.owner.prepare_batches(job=identity, surface="knowledge", batches=manifest)
    before = saved_rows()
    monkeypatch.setattr(domain, "plan_fingerprint", contract.plan_fingerprint)
    with pytest.raises(ServiceInvocationConflict):
        runtime.owner.prepare_batches(job=identity, surface="knowledge", batches=manifest)
    with pytest.raises(ValueError, match="fingerprint"):
        diagnostics._validate_plan(original, identity)
    assert saved_rows() == before


@pytest.mark.parametrize("schema", [NEW])
def test_diagnostics_rejects_changed_identity_manifest_or_digest(runtime, monkeypatch, schema):
    monkeypatch.setattr(contract, "PLAN_SCHEMA", schema)
    identity = job()
    manifest = contract.batch_manifest(identity, ["one", "two"])
    plan = runtime.owner.prepare_batches(job=identity, surface="knowledge", batches=manifest)
    assert diagnostics._validate_plan(plan, identity) == plan["batches"]
    before = saved_rows()
    for key, value in [("created_by", "bob"), ("surface", "paper"), ("search_id", "another"), ("run_generation", 2)]:
        with pytest.raises(ValueError):
            diagnostics._validate_plan(plan, {**identity, key: value})
    changed = deepcopy(plan)
    changed["batches"][0]["input_fingerprint"] = "1" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        diagnostics._validate_plan(changed, identity)
    for fingerprint in (None, "0" * 64, wire_digest(identity, manifest, "archive.service-invocation-plan.v2")):
        changed = deepcopy(plan)
        changed["plan_fingerprint"] = fingerprint
        with pytest.raises(ValueError, match="fingerprint"):
            diagnostics._validate_plan(changed, identity)
    changed = deepcopy(plan)
    changed["batches"][0]["plan_fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="batch identity"):
        diagnostics._validate_plan(changed, identity)
    assert saved_rows() == before


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
