"""Reviewer creation uses real lifecycle/roster facts in isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.society_service import family_quality_reviewers as owner
from management_portal.society_service.resident_record_domain import find_resident
from tests.cases.test_family_familize import lineage, charters, future_creation_store, resident  # noqa: F401
from tests.cases.isa_test_support import ACTOR, ok
from tests.cases.test_isa_lineage_lifecycle import graph
from tests.cases.test_personal_assistant import personal  # noqa: F401


def test_nonfamily_real_lifecycle_concurrency_and_idempotency(lineage, charters):
    responsible = resident(lineage, "Author")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: owner.ensure(responsible, ACTOR, kind="page"), range(2)))
    assert results[0] == results[1]
    result = results[0]
    assert result["reviewer_id"] == result["reviewers"]["page"]
    assert len(set(result["reviewers"].values())) == 2
    block = next(b for b in graph(lineage)["function_blocks"] if b["id"] == result["family_id"])
    assert set(block["family"]["members"]) == {responsible, *result["reviewers"].values()}
    for kind, rid in result["reviewers"].items():
        record = find_resident(rid)
        assert record["family"]["role"] == "member"
        assert kind in owner.instructions(record)
    assert owner.instructions(find_resident(responsible)) == ""


def test_two_families_are_isolated_and_external_explicit_reviewer_rejected(lineage, charters):
    a, b = resident(lineage, "Author A"), resident(lineage, "Author B")
    first = owner.ensure(a, ACTOR, kind="page")
    second = owner.ensure(b, ACTOR, kind="video")
    assert first["family_id"] != second["family_id"]
    assert not set(first["reviewers"].values()) & set(second["reviewers"].values())
    before = graph(lineage)
    with pytest.raises(ValueError, match="direct Family"):
        owner.ensure(a, ACTOR, kind="video", reviewer_id=second["reviewer_id"])
    with pytest.raises(ValueError, match="independent"):
        owner.ensure(a, ACTOR, kind="page", reviewer_id=a)
    assert graph(lineage) == before


def test_creation_denied_is_local_and_existing_roles_need_no_new_creation_grant(lineage, charters, monkeypatch):
    a = resident(lineage, "Permission Author")
    original = owner.permission_system.allows
    monkeypatch.setattr(owner.permission_system, "allows", lambda actor, name, args: False if name == "CreateResident" else original(actor, name, args))
    before = graph(lineage)
    with pytest.raises(PermissionError, match="CreateResident"):
        owner.ensure(a, ACTOR, kind="page")
    assert graph(lineage) == before
    monkeypatch.setattr(owner.permission_system, "allows", original)
    result = owner.ensure(a, ACTOR, kind="page")
    monkeypatch.setattr(owner.permission_system, "allows", lambda actor, name, args: False if name in {"CreateResident", "UpdateFamily", "Familize"} else original(actor, name, args))
    assert owner.ensure(a, ACTOR, kind="page") == result


def test_private_family_uses_real_owner_and_denies_outsiders(personal):
    ama, facade = personal
    responsible = ama["resident_id"]
    result = owner.ensure(responsible, "newcomer", kind="page")
    for rid in result["reviewers"].values():
        record = find_resident(rid)
        assert record["personal_owner_id"] == "newcomer"
        assert record["family"]["family_id"] == result["family_id"]
    assert owner.ensure(facade["resident_id"], "newcomer", kind="video")["reviewers"] == result["reviewers"]
    with pytest.raises(PermissionError):
        owner.ensure(responsible, "other-user", kind="page")


def test_invalid_kind_rejected_before_any_lookup():
    with pytest.raises(ValueError, match="kind"):
        owner.ensure("missing", ACTOR, kind="other")


def test_existing_roster_roles_reused_and_wrong_kind_or_ambiguity_rejected(lineage, charters):
    a = resident(lineage, "Existing Role Author")
    family = ok("Familize", {"lineage_id":lineage, "resident_ids":[a]})
    ids = {kind:ok("CreateResident", {"lineage_id":lineage, "family_id":family["family_id"],
              "title":title})["resident_id"] for kind, title in owner.KINDS.items()}
    before = graph(lineage)
    assert owner.ensure(a, ACTOR, kind="page")["reviewers"] == ids
    assert graph(lineage) == before
    with pytest.raises(ValueError, match="corresponding"):
        owner.ensure(a, ACTOR, kind="page", reviewer_id=ids["video"])
    ok("CreateResident", {"lineage_id":lineage, "family_id":family["family_id"], "title":owner.KINDS["page"]}, key="duplicate-review-role")
    with pytest.raises(ValueError, match="Ambiguous"):
        owner.ensure(a, ACTOR, kind="page")


def test_actor_in_another_family_does_not_capture_target_reviewers(lineage, charters):
    a, caller = resident(lineage, "Target Author"), resident(lineage, "Other Family Caller")
    other = ok("Familize", {"lineage_id":lineage, "resident_ids":[caller]})
    target = ok("Familize", {"lineage_id":lineage, "resident_ids":[a]})
    result = owner.ensure(a, caller, kind="page")
    assert result["family_id"] == target["family_id"]
    blocks = {b["id"]:b for b in graph(lineage)["function_blocks"]}
    assert set(blocks[other["family_id"]]["family"]["members"]) == {caller}
    assert set(blocks[target["family_id"]]["family"]["members"]) == {a, *result["reviewers"].values()}
    from pathlib import Path
    for kind, rid in result["reviewers"].items():
        folder = owner.resident_record_domain.resident_folder_path(find_resident(rid))
        text = Path(folder, "resident.md").read_text()
        assert kind in text and "export" in text and "passed" in text and "实际" in text


def test_nonfamily_update_permission_denial_precedes_familize(lineage, charters, monkeypatch):
    a = resident(lineage, "Preflight Author")
    original = owner.permission_system.allows
    monkeypatch.setattr(owner.permission_system, "allows", lambda actor, name, args: False if name == "UpdateFamily" else original(actor, name, args))
    before = graph(lineage)
    with pytest.raises(PermissionError, match="UpdateFamily"):
        owner.ensure(a, ACTOR, kind="page")
    assert graph(lineage) == before
