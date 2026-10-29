from __future__ import annotations

from management_portal.society_service import lineage_domain
from management_portal.society_service import resident_access


LINEAGE_ID = "lineage_111111111111"
LEADER_ID = "resident_111111111111"
MEMBER_ID = "resident_222222222222"


from management_portal.society_service import resident_runtime_domain
from management_portal.society_service import lineage_aggregate

class _Catalog:
    def snapshot(self):
        return [{
            "lineage_id": LINEAGE_ID,
            "lineage_membership": "formal",
            "project_id": "project_test",
            "title": "Fixture Lineage",
            "leader_resident_id": LEADER_ID,
            "nodes": [
                {"id": LEADER_ID, "title": "Leader"},
                {"id": MEMBER_ID, "title": "Member"},
            ],
            "edges": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        }]

def test_lineage_access_is_uniform_for_valid_actors_contracts(monkeypatch, subtests, isolated_portal_case):
    """开放模式下 Lineage 访问对每个有效主体一致且包含全部成员"""
    with subtests.test(contract='test_lineage_access_contains_all_members'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: _Catalog())

            for actor in ("alice", "bob"):
                visible = lineage_domain.actor_lineage_access_records(actor)
                assert len(visible) == 1
                assert visible[0]["lineage_node_ids"] == [LEADER_ID, MEMBER_ID]
                assert "navigation_resident_ids" not in visible[0]
    with subtests.test(contract='test_lineage_manager_check_accepts_valid_actors'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:

            lineage_aggregate.ensure_manager(
                {"lineage_id": LINEAGE_ID},
                "alice",
            )
    with subtests.test(contract='test_resident_authority_accepts_valid_actors'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record = {
                "resident_id": LEADER_ID,
                "lineage_id": LINEAGE_ID,
            }
            monkeypatch.setattr(
                resident_runtime_domain,
                "lineage_identity_record",
                lambda _record: resident_runtime_domain._compile_lineage_identity({
                    "lineage_id": LINEAGE_ID,
                    "lineage_node_ids": [LEADER_ID],
                }),
            )

            for actor in ("alice", "bob"):
                assert resident_access.can_manage_resident(record, actor) is True
                assert resident_access.resident_visible_to_actor(record, actor) is True
