from __future__ import annotations

from management_portal.society_service import resource_client
from management_portal.society_service import resident_access
from management_portal.society_service import resident_runtime_domain


LINEAGE_ID = "lineage_111111111111"
LEADER_ID = "resident_111111111111"
CHILD_ID = "resident_222222222222"
FORMAL_RESIDENT_ID = "resident_333333333333"
PERSONAL_RESIDENT_ID = "resident_444444444444"


def _records() -> tuple[dict, dict]:
    common = {
        "lineage_id": LINEAGE_ID,
        "lineage_leader_resident_id": LEADER_ID,
        "lineage_membership": "formal",
        "folder_schema": "lineage-folder-v3",
        "workspace_path": "/tmp/lineage_111111111111",
        "registration_state": "ready",
    }
    return (
        {
            **common,
            "resident_id": LEADER_ID,
            "title": "Leader",
        },
        {
            **common,
            "resident_id": CHILD_ID,
            "title": "Child",
        },
    )


def _derived_residents_fixture() -> dict:
    return {
        "residents": [
            {
                "resident_id": FORMAL_RESIDENT_ID,
                "lineage_id": LINEAGE_ID,
                "lineage_membership": "formal",
                "label": "Formal Resident",
                "state": "active",
            },
            {
                "resident_id": PERSONAL_RESIDENT_ID,
                "lineage_id": LINEAGE_ID,
                "lineage_membership": "personal",
                "label": "Personal Resident",
                "state": "active",
                "created_by": "visitor",
            },
        ],
        "logs": [],
    }


def test_lineage_members_use_uniform_actor_access_contracts(monkeypatch, subtests, isolated_portal_case):
    """开放模式下 Lineage 成员与派生 Resident 对各执行者开放，家族只含直接派生，Leader 不可删"""
    with subtests.test(contract='test_lineage_members_use_uniform_actor_access'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            main, child = _records()
            monkeypatch.setattr(
                resident_runtime_domain,
                "_lineage_identity_record",
                lambda _record: resident_runtime_domain._compile_lineage_identity({
                    "lineage_id": LINEAGE_ID,
                    "lineage_node_ids": [LEADER_ID, CHILD_ID, FORMAL_RESIDENT_ID],
                    "leader_resident_id": LEADER_ID,
                }),
            )
            assert resident_access.resident_visible_to_actor(main, "lineage-owner") is True
            assert resident_access.resident_visible_to_actor(child, "lineage-owner") is True
            assert resident_access.resident_visible_to_actor(child, "child-record-owner") is True

            formal_resident, personal_resident = _derived_residents_fixture()["residents"]
            assert resident_access.can_manage_resident(formal_resident, "lineage-owner") is True
            assert resident_access.can_manage_resident(formal_resident, "visitor") is True
            assert resident_access.can_manage_resident(personal_resident, "visitor") is True
            assert resident_access.can_manage_resident(personal_resident, "other-user") is True
            assert resident_access.can_manage_resident(personal_resident, "lineage-owner") is True
    with subtests.test(contract='test_formal_resident_outside_the_graph_stays_manageable'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            main, _child = _records()
            formal_resident = _derived_residents_fixture()["residents"][0]
            monkeypatch.setattr(
                resident_runtime_domain,
                "_lineage_identity_record",
                lambda _record: resident_runtime_domain._compile_lineage_identity({
                    "lineage_id": LINEAGE_ID,
                    "lineage_node_ids": [LEADER_ID],
                    "leader_resident_id": LEADER_ID,
                }),
            )

            assert FORMAL_RESIDENT_ID not in resident_runtime_domain._lineage_graph_resident_ids(main)
            assert resident_access.can_manage_resident(
                formal_resident,
                "lineage-owner",
            ) is True
            assert resident_access.can_manage_resident(
                formal_resident,
                "visitor",
            ) is True
    with subtests.test(contract='test_personal_resident_read_access_is_uniform'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            main, _child = _records()
            personal_resident = {
                **_derived_residents_fixture()["residents"][1],
                "created_by": "sample_admin",
                "source_resident_id": LEADER_ID,
            }
            monkeypatch.setattr(
                resident_runtime_domain,
                "_lineage_identity_record",
                lambda _record: resident_runtime_domain._compile_lineage_identity({
                    "lineage_id": LINEAGE_ID,
                    "lineage_node_ids": [LEADER_ID, CHILD_ID],
                    "leader_resident_id": LEADER_ID,
                }),
            )

            registry = {"residents": [personal_resident], "logs": []}
            assert resident_runtime_domain._managed_derived_residents(main, registry, "sample_admin") == [personal_resident]
            assert resident_runtime_domain._managed_derived_residents(main, registry, "CASE-A") == [personal_resident]
            assert resident_runtime_domain._managed_derived_residents(
                main,
                registry,
                "lineage-owner",
            ) == [personal_resident]
    with subtests.test(contract='test_resident_family_excludes_unrelated_lineage_members'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            main, child = _records()
            direct = {
                **_derived_residents_fixture()["residents"][1],
                "created_by": "sample_admin",
                "source_resident_id": LEADER_ID,
            }
            unrelated = {
                **direct,
                "resident_id": "resident_555555555555",
                "source_resident_id": CHILD_ID,
            }
            monkeypatch.setattr(
                resident_runtime_domain,
                "_lineage_identity_record",
                lambda _record: resident_runtime_domain._compile_lineage_identity({
                    "lineage_id": LINEAGE_ID,
                    "lineage_node_ids": [LEADER_ID, CHILD_ID],
                    "leader_resident_id": LEADER_ID,
                }),
            )
            registry = {"residents": [child, direct, unrelated], "logs": []}

            assert resident_runtime_domain._managed_derived_residents(main, registry, "sample_admin") == [direct]
