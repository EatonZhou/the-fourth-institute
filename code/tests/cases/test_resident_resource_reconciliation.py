from __future__ import annotations
from management_portal.society_service import resident_catalog
from management_portal.society_service.domain_ports import resource_port

from pathlib import Path
from types import SimpleNamespace

from management_portal.society_service import resident_domain, resource_client, state_store
from management_portal.society_service.state_store import LineageDataStore


def _resident(
    resident_id: str = "resident_111111111111",
    *,
    lineage_id: str = "lineage_222222222222",
    title: str = "组件化",
) -> dict[str, str]:
    return {
        "resident_id": resident_id,
        "lineage_id": lineage_id,
        "title": title,
        "description": "Canonical Resident title projection",
    }


def test_projection_claims_handle_empty_and_targeted_polls(tmp_path):
    """资源投影队列为空时领取返回空，可按 Resident ID 定向领取"""
    store = LineageDataStore()
    first, second = _resident(), _resident("resident_333333333333")
    assert store.claim_resident_resource_projections() == []
    assert store.claim_resident_resource_projections(resident_id=first["resident_id"]) == []
    for record in (first, second):
        store.enqueue_resident_resource_projection(record["resident_id"], record)
    targeted = store.claim_resident_resource_projections(resident_id=f" {second['resident_id']} ")
    assert [item["resident_id"] for item in targeted] == [second["resident_id"]]
    remaining = store.claim_resident_resource_projections()
    assert [item["resident_id"] for item in remaining] == [first["resident_id"]]
    assert store.claim_resident_resource_projections() == []


def test_batch_projection_projects_canonical_title_and_lineage_relation(monkeypatch) -> None:
    """批量同步 Resident 资源时写入其标题和所属 Lineage 关系"""
    payloads: list[list[dict[str, object]]] = []

    class ResourceService:
        def sync_collaboration_resources(self, payload):
            payloads.append(payload)
            return {
                "resources": [item["descriptor"] for item in payload],
            }

    monkeypatch.setattr(
        resource_client,
        "get_resource_service",
        lambda: ResourceService(),
    )

    assert resource_client.sync_resident_resource_bundles([_resident()]) == 1
    descriptor = payloads[0][0]["descriptor"]
    assert set(payloads[0][0]) == {"descriptor"}
    assert descriptor["title"] == "组件化"
    assert {"kind": "lineage", "target_id": "lineage_222222222222"} in descriptor["relations"]
