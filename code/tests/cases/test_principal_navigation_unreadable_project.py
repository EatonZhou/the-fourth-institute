"""A Lineage the actor can read is listed even when its Project is not readable.

test-actor owns the Paper Lineage, but the Lineage sidebar skipped the whole
"AI员工" Project because test-actor cannot read that Project resource, so a
TAU run could not find the Lineage or open its HATG from the page.

:module: lineage.team
"""
from __future__ import annotations

from types import SimpleNamespace

from management_portal.society_service import principal_hierarchy_domain, resident_catalog, resource_client
from management_portal.society_service import lineage_aggregate


def test_a_readable_lineage_in_an_unreadable_project_is_listed_under_that_project(monkeypatch):
    """Lineage 侧栏列出可读的 Lineage，即使其所属 Project 不可读"""
    definitions = [
        {"lineage_id": "lineage_e1ed41b295b0", "title": "Paper", "project_id": "AI员工", "leader_resident_id": "resident_aaaaaaaaaaaa"},
        {"lineage_id": "lineage_bbbbbbbbbbbb", "title": "Hidden", "project_id": "AI员工"},
        {"lineage_id": "lineage_cccccccccccc", "title": "Mine", "project_id": "OSForAgent"},
    ]
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: SimpleNamespace(listing_records=lambda: definitions))
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: SimpleNamespace(all=lambda: []))
    monkeypatch.setattr(resource_client, "_query_all", lambda kind, actor: [{"resource_id": "OSForAgent", "title": "OSForAgent"}])

    projects = {item["project_id"]: item for item in principal_hierarchy_domain.navigation("test-actor")["projects"]}

    assert [child["lineage_id"] for child in projects["OSForAgent"]["lineages"]] == ["lineage_cccccccccccc"]
    paper_project = projects["AI员工"]
    assert paper_project["accessible"] is False and paper_project["project_readable"] is False
    assert [child["lineage_id"] for child in paper_project["lineages"]] == ["lineage_e1ed41b295b0", "lineage_bbbbbbbbbbbb"]
