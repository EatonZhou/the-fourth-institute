"""Explicit orphan deletion through Resource ISA, with real references and PAR.

:module: resource.service
:design: decision_8838269736d3
"""

import pytest

from management_common.instance_locations import current_instance
from management_portal.resources.workspace_store import get_workspace_store, now
from management_portal.society_service import resident_workspace_domain
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace, WID
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, resident_domain, _new_session_payload,
)
from tests.cases.permission_test_support import enable, Fact


@pytest.fixture
def workspace(resource_workspace):
    resource_workspace.root = resource_workspace.root / 'registered'
    resource_workspace.root.mkdir()
    resource_workspace.store.ensure(WID, str(resource_workspace.root), move=True)
    return resource_workspace


def call(workspace, wid=WID, actor='alice'):
    return workspace.call('UnregisterResource', {'resource_id': wid}, actor=actor)


def conflict(result, reason):
    assert result.status == 'failed', result.to_payload()
    assert result.error['code'] == 'instruction.conflict', result.to_payload()
    assert result.error['details']['reason'] == reason
    assert result.error['commit_state'] == 'not_committed'


def test_deleted_resident_does_not_keep_binding_alive(future_creation_store, workspace):
    resident = resident_domain.register_resident(_new_session_payload('Former user'), 'sample_admin')
    resident_workspace_domain.add_resident_reference_workspace(
        resident['resident_id'], str(workspace.root), 'sample_admin', workspace_id=WID)
    # State between the identity deletion and its resumable lifecycle cleanup.
    with get_lineage_data_store().database.transaction() as conn:
        conn.execute('UPDATE residents SET deleted_at=%s WHERE resident_id=%s', (now(), resident['resident_id']))
    call(workspace).raise_for_error()
    with get_lineage_data_store().database.read() as conn:
        assert not conn.execute('SELECT 1 FROM resident_workspace_bindings WHERE workspace_id=%s', (WID,)).fetchall()


def test_nonadmin_denied_by_real_permission_scheme(workspace, monkeypatch):
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob', category='intern')
    permissions.place(Fact('workspace', WID))
    permissions.grant('alice', WID, '@admin')
    permissions.grant('bob', WID, '@contributor')
    before = workspace.store.rows([WID])
    result = call(workspace, actor='bob')
    assert result.status == 'failed', result.to_payload()
    assert result.error['code'] == 'instruction.forbidden', result.to_payload()
    assert workspace.store.rows([WID]) == before
    # Also guard callers going directly through ResourceService, outside ISA.
    with pytest.raises(PermissionError):
        workspace.service.unregister(WID, actor='bob')
    call(workspace).raise_for_error()
