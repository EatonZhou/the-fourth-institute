"""The registrant of a Workspace holds its @admin (default scheme relation rule "Workspace 登记人").

:module: platform.permissions
:design: decision_600ae27b0538
"""
from types import SimpleNamespace

import pytest

from management_common.resource_identity import get_resource_type_registry
from management_portal.permission_system import personal_ama
from management_portal.permission_system.facts import Instance, fact_from_record
from management_portal.resources.service import ResourceService
from management_portal.resources.workspace_store import get_workspace_store, registrant_fields


@pytest.fixture
def service():
    service = ResourceService.__new__(ResourceService)
    service._identity = get_resource_type_registry()
    return service


def test_bind_registration_records_the_caller_and_portal_registrations_record_none(tmp_path, service):
    """BindWorkspace 自动登记记下调用者为登记人；Portal 自己登记的 Workspace 没有登记人

    :risk: no-external-side-effects
    :profiles: local.fixture"""
    bound, portal_made = tmp_path / "bound", tmp_path / "portal"
    bound.mkdir(), portal_made.mkdir()
    wid = service.workspace_id_for_path(bound, registrant="resident_0123456789ab")
    # Asking again (another binding of the same directory) keeps the first registrant.
    assert service.workspace_id_for_path(bound, registrant="resident_ba9876543210") == wid
    plain = service.workspace_id_for_path(portal_made)
    store = get_workspace_store()
    descriptors = store.descriptors([wid, plain])
    assert descriptors[wid]["attributes"]["created_by"] == "resident_0123456789ab"
    assert "created_by" not in descriptors[plain]["attributes"]
    assert store.descriptors()[wid]["attributes"]["created_by"] == "resident_0123456789ab"
    # Services and scripts are never registrants.
    assert registrant_fields("portal") == ("", "") and registrant_fields("lineage_0123456789ab") == ("", "")


def test_register_workspace_records_its_caller_and_the_backfill_restores_it(tmp_path):
    """RegisterWorkspace 记下调用者为登记人；存量按已完成的创建记录回填，取不到的留空"""
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore

    target = tmp_path / "registered"
    target.mkdir()
    row = WorkspaceCreationStore().register(path=str(target), owner_id="", actor="bob",
                                            operation_id="op_registrant_test", title="registered")
    store = get_workspace_store()
    wid = row["workspace_id"]
    assert store.descriptors([wid])[wid]["attributes"]["created_by"] == "bob"
    store.database.write(lambda connection: connection.execute(
        f"DELETE FROM {store.registrants} WHERE workspace_id=%s", (wid,)))
    assert "created_by" not in store.descriptors([wid])[wid]["attributes"]
    store.database.write(store._backfill_registrants)
    assert store.descriptors([wid])[wid]["attributes"]["created_by"] == "bob"


def test_personal_ama_registrant_is_judged_as_its_owner(monkeypatch):
    """个人 AMA 的 Resident 登记时，其主人同为登记人"""
    monkeypatch.setattr(personal_ama, "owner_of", lambda principal: "alice" if principal == "resident_0123456789ab" else "")
    assert registrant_fields("resident_0123456789ab") == ("resident_0123456789ab", "alice")
    assert registrant_fields("resident_ba9876543210") == ("resident_ba9876543210", "")
    assert registrant_fields("bob") == ("bob", "")


def test_registrant_gets_admin_on_the_workspace_and_portal_workspaces_give_none():
    """默认方案给 Workspace 登记人该 Workspace 的 @admin；没有登记人（Portal 创建）时不产生权限"""
    from management_portal.permission_system.scheme.compiler import compile_scheme, declared_decisions
    from management_portal.permission_system.scheme.repository import builtin_documents, installed

    rules = compile_scheme(installed(builtin_documents()["default"]), instructions=declared_decisions())
    instance = Instance(rules, SimpleNamespace(scoped=lambda scheme_id: None))
    registered = fact_from_record({"resource_id": "workspace_0123456789ab", "status": "active",
                                   "attributes": {"created_by": "resident_0123456789ab", "created_for": "alice"}},
                                  "workspace")
    rows = instance.derive(registered)
    assert ("permit", "resident_0123456789ab", "@admin", "workspace_0123456789ab") in rows
    assert ("permit", "alice", "@admin", "workspace_0123456789ab") in rows
    portal = fact_from_record({"resource_id": "workspace_ba9876543210", "status": "active", "attributes": {}}, "workspace")
    assert not [row for row in instance.derive(portal) if row[2] == "@admin"]
