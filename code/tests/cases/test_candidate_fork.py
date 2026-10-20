"""A Seed's source is a candidate fork the Lineage records (decision_4adcc1ff2f2c).

A candidate fork runs from a Resident to a Seed: the candidate starts from
that Resident's work, and adopting the Seed forks it.  It is a Lineage
relation like Fork inheritance, the only one that ends on a Seed, and a Seed
has at most one.  CreateCandidateFork sets or replaces it and
DeleteCandidateFork removes it; a Seed without one cannot be adopted.  It goes
with its Seed and with its Resident; dismissing the Seed keeps it.

Before this the source was a field in the Seed's config, and the dashed line
from the Resident to the Seed was drawn by the page alone: a Seed created by
hand had no way to get one.

:module: spec.design-governance
"""

from __future__ import annotations

import pytest

from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import lineage_domain
from tests.cases.isa_test_support import ACTOR, ok, run
from tests.cases.test_isa_lineage_lifecycle import (  # noqa: F401 - lineage is a fixture
    future_creation_store,
    graph,
    lineage,
    resident,
)


def _seed(lineage_id: str, title: str, **config) -> str:
    return ok("CreateComponent", {"lineage_id": lineage_id, "kind": "seed", "title": title,
                                  "config": {"seed_type": "idea", **config}}, key=f"seed-{title}")["component_id"]


def _forks(lineage_id: str, seed_id: str) -> list[dict]:
    return [edge for edge in graph(lineage_id)["edges"] if edge["type"] == "candidate_fork" and edge["target"] == seed_id]


def test_a_seed_made_by_hand_gets_its_source_through_a_candidate_fork(lineage):
    """Seed 的候选 Fork 关系"""
    first = resident(lineage, "First")
    second = resident(lineage, "Second")
    seed = _seed(lineage, "Hand")
    assert _forks(lineage, seed) == []
    refused = run("PromoteSeed", {"lineage_id": lineage, "seed_id": seed}, key="promote-without")
    assert refused.status == "failed" and "先建立候选 Fork" in refused.error["message"]

    created = ok("CreateCandidateFork", {"lineage_id": lineage, "seed_id": seed, "source_resident_id": first}, key="fork-first")
    [fork] = _forks(lineage, seed)
    assert fork["id"] == created["edge_id"] and created["replaced_edge_id"] == ""
    assert (fork["source"], fork["family"], fork["label"]) == (first, "context", "候选 Fork")
    # The same source again changes nothing.
    again = ok("CreateCandidateFork", {"lineage_id": lineage, "seed_id": seed, "source_resident_id": first}, key="fork-first-again")
    assert again == {"edge_id": fork["id"], "replaced_edge_id": ""}

    # Another source replaces it: a Seed has one candidate fork.
    replaced = ok("CreateCandidateFork", {"lineage_id": lineage, "seed_id": seed, "source_resident_id": second}, key="fork-second")
    assert replaced["replaced_edge_id"] == fork["id"]
    assert [(item["id"], item["source"]) for item in _forks(lineage, seed)] == [(replaced["edge_id"], second)]


def test_a_source_written_in_the_seed_config_becomes_its_candidate_fork(lineage):
    """Seed 的候选 Fork 关系"""
    source = resident(lineage, "Source")
    seed = _seed(lineage, "Declared", source_resident_id=source)

    assert [item["source"] for item in _forks(lineage, seed)] == [source]
    component = next(item for item in graph(lineage)["program"]["components"] if item["id"] == seed)
    assert "source_resident_id" not in component["config"]


def test_the_candidate_fork_goes_with_its_seed_and_its_resident_but_not_with_dismissal(lineage):
    """Seed 的候选 Fork 关系"""
    keeper = resident(lineage, "Keeper")
    leaving = resident(lineage, "Leaving")
    dismissed = _seed(lineage, "Dismissed", source_resident_id=keeper)
    deleted = _seed(lineage, "Deleted", source_resident_id=keeper)
    orphaned = _seed(lineage, "Orphaned", source_resident_id=leaving)

    ok("UpdateComponent", {"lineage_id": lineage, "component_id": dismissed,
                           "changes": {"config": {"seed_type": "idea", "status": "dismissed"}}}, key="dismiss")
    assert [item["source"] for item in _forks(lineage, dismissed)] == [keeper]
    ok("DeleteComponent", {"lineage_id": lineage, "component_id": deleted}, key="delete-seed")
    assert _forks(lineage, deleted) == []
    ok("DeleteResident", {"lineage_id": lineage, "resident_id": leaving}, key="delete-source")
    assert _forks(lineage, orphaned) == []
    assert orphaned in {item["id"] for item in graph(lineage)["program"]["components"]}
    # Deleting another Resident must also persist the surviving candidate fork.
    assert [item["source"] for item in _forks(lineage, dismissed)] == [keeper]

    removed = ok("DeleteCandidateFork", {"lineage_id": lineage, "seed_id": dismissed}, key="unfork")
    assert removed["edge_id"] and _forks(lineage, dismissed) == []
    missing = run("DeleteCandidateFork", {"lineage_id": lineage, "seed_id": dismissed}, key="unfork-again")
    assert missing.status == "failed" and missing.error["code"] == "instruction.not_found"


def test_only_a_candidate_fork_ends_on_a_seed_and_starts_in_the_lineage(lineage):
    """Seed 的候选 Fork 关系"""
    source = resident(lineage, "Source")
    seed = _seed(lineage, "Target")
    adopted = _seed(lineage, "Adopted", status="promoted")

    outside = run("CreateCandidateFork", {"lineage_id": lineage, "seed_id": seed, "source_resident_id": "resident_ffffffffffff"}, key="outside")
    assert outside.status == "failed" and "对象不属于声明的 Lineage" in outside.error["message"]
    closed = run("CreateCandidateFork", {"lineage_id": lineage, "seed_id": adopted, "source_resident_id": source}, key="adopted")
    assert closed.status == "failed" and "已经被采纳或抛弃" in closed.error["message"]

    def inherit_into_seed(body: dict) -> None:
        body["edges"].append({"source": source, "target": seed, "type": "inheritance"})

    with pytest.raises(ValueError, match="不存在的节点"):
        lineage_domain.commit_lineage_edit(lineage, ACTOR, inherit_into_seed, reason="test")

    def fork_into_resident(body: dict) -> None:
        body["edges"].append({"source": source, "target": source, "type": "candidate_fork"})

    lineage_domain.commit_lineage_edit(lineage, ACTOR, fork_into_resident, reason="test")
    assert not [edge for edge in graph(lineage)["edges"] if edge["type"] == "candidate_fork"]


def test_a_seed_does_not_name_its_explorer(lineage):
    """Seed 配置拒绝记录探索者 Resident

    Nothing on a Seed names a Resident (decision_5b5653c3eaa5)."""

    with pytest.raises(InstructionError, match="seed config 不接受字段"):
        _seed(lineage, "Old", producer_resident_id=resident(lineage, "Explorer"))
