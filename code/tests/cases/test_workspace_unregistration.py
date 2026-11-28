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


def test_unregister_compares_the_token_in_the_service_mutation(workspace):
    """UnregisterResource 在资源变更域内、删除之前比较版本令牌，跳过内核预读时旧令牌也以 version_conflict 失败且资源保留"""
    from types import SimpleNamespace

    from management_portal.hatg.contracts import InstructionError
    from management_portal.resources import isa

    stale = workspace.resolve()['version']
    workspace.update({'title': 'Moved on', 'expected_version': stale}).raise_for_error()
    current = workspace.resolve()['version']
    assert current != stale

    def unregister(token):
        return isa.unregister_resource(SimpleNamespace(
            arguments={'resource_id': WID}, argument={'resource_id': WID}.get, caller='alice', expected_version_for=lambda _object_id: token))

    with pytest.raises(InstructionError) as raised:
        unregister(stale)
    assert raised.value.code == 'instruction.version_conflict'
    assert raised.value.details == {'expected_version': stale, 'current_version': current}
    assert workspace.store.rows([WID])
    unregister(current)
    assert not workspace.store.rows([WID])
