"""A Lineage node reads its Resident's current facts (decision_44cf587c1721).

A Provider switch changes the Resident record; the canvas follows it.
Deleted Residents leave the active graph while their identity remains.

:module: lineage.hatg
"""

from __future__ import annotations

from management_portal.society_service.resident_record_domain import (
    find_resident as _find_resident,
    save_resident_record as _save_resident_record,
)
from tests.cases.isa_test_support import ok
from tests.cases.test_isa_lineage_lifecycle import (  # noqa: F401 - lineage is a fixture
    future_creation_store,
    graph,
    lineage,
    resident,
)


def _node(lineage_id: str, resident_id: str) -> dict:
    return next(item for item in graph(lineage_id)["nodes"] if item["id"] == resident_id)


def test_a_deleted_resident_leaves_the_active_graph(lineage):
    """删除 Resident 后移出活动图并保留历史身份

    :module: lineage.team"""
    from management_portal.society_service.resident_catalog import get_catalog

    first, worker = (resident(lineage, title) for title in ("Auth", "Docs"))
    ok("DeleteResident", {"lineage_id": lineage, "resident_id": worker}, key="delete-worker")
    assert worker not in {item["id"] for item in graph(lineage)["nodes"]}
    assert _node(lineage, first)["id"] == first
    assert get_catalog().get_historical(worker)["deleted_at"]


def test_a_node_shows_the_provider_its_resident_has_now(lineage):
    """Lineage 节点显示 Resident 当前的 Provider

    换 Provider（RestartSession）只改 Resident 记录；GetLineage 节点的 backend 读记录，画布按 Resident 当前的 Provider 标注（CASE-A 10-07，Portal-Auth 的 Router 换到 Codex 后画布仍标 dsh）。

    A RestartSession that switches Provider changes only the record; the node follows it.

    :design: decision_44cf587c1721
    """

    worker = resident(lineage, "Router")
    assert _node(lineage, worker)["backend"] == "codex"
    record = _find_resident(worker)
    record["backend"] = "dsh"
    _save_resident_record(record, commit_configuration=True)
    assert _node(lineage, worker)["backend"] == "dsh"
