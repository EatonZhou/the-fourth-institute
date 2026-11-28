"""RunDeviceCommand is judged by the permission scheme (decision_600ae27b0538, decision_d397f2d99f89).

The default scheme lets every Resident use every device through the "使用设备"
A node; a deny entry withdraws it, and Users hold it only by grant or as admin.
"""
from unittest.mock import Mock

import pytest

from management_portal import device_identity_backend as backend, permission_system
from management_portal.resources.service import ResourceService
from tests.cases.permission_test_support import permissions, Fact  # noqa: F401

DEVICE = 'device_0123456789ab'
OTHER_DEVICE = 'device_ba9876543210'
ACTOR = 'resident_0123456789ab'
LINEAGE = 'lineage_0123456789ab'


@pytest.fixture
def run(monkeypatch, permissions):
    permissions.place(Fact('project', 'Other'), Fact('lineage', LINEAGE, {'project': 'Other'}),
                      Fact('resident', ACTOR, {'lineage': LINEAGE}), Fact('device', DEVICE), Fact('device', OTHER_DEVICE))
    monkeypatch.setattr(backend, 'effective_ssh_access', lambda rid: {'via_resource_id': rid})
    run = Mock(return_value={'exit_code': 0, 'output': '', 'truncated': False})
    monkeypatch.setattr(backend, 'run_device_ssh', run)
    return run


def test_scheme_declares_use_device_node_and_resident_rule(permissions):
    from management_portal.permission_system.scheme.repository import builtin_documents

    document = builtin_documents()['default']
    nodes = {node['id']: node for node in document['trees']['A']['nodes']}
    assert nodes['@use_device']['parent'] == '@contributor'
    assert document['instructions']['RunDeviceCommand'] == {'a': '@use_device'}
    assert document['instructions']['CheckDeviceSsh'] == {'a': '@viewer'}
    assert document['instructions']['UpdateDeviceSshCredential'] == {'a': '@admin'}
    assert document['instructions']['DeleteDeviceSshCredential'] == {'a': '@admin'}
    rule = next(entry for entry in document['entries'] if entry['a'] == '@use_device')
    assert (rule['effect'], rule['p'], rule['r']) == ('permit', '@residents', '@shared')


def test_every_resident_uses_every_device_by_default(permissions, run):
    service = ResourceService()
    for device in (DEVICE, OTHER_DEVICE):
        assert service.run_device_command(device, 'true', actor=ACTOR)['exit_code'] == 0
        assert permission_system.allows(ACTOR, 'RunDeviceCommand', {'resource_id': device})
    assert run.call_count == 2
    for instruction in ('UpdateDeviceSshCredential', 'DeleteDeviceSshCredential'):
        assert not permission_system.allows(ACTOR, instruction, {'resource_id': DEVICE})


def test_deny_withdraws_one_resident_or_one_device(permissions, run):
    service = ResourceService()
    permissions.deny(ACTOR, DEVICE, '@use_device')
    with pytest.raises(PermissionError):
        service.run_device_command(DEVICE, 'true', actor=ACTOR)
    assert service.run_device_command(OTHER_DEVICE, 'true', actor=ACTOR)['exit_code'] == 0
    assert run.call_count == 1


def test_unauthorized_member_is_refused_until_granted(permissions, run):
    permissions.users('member_user')
    service = ResourceService()
    with pytest.raises(PermissionError):
        service.run_device_command(DEVICE, 'true', actor='member_user')
    permissions.grant('member_user', DEVICE, '@use_device')
    assert service.run_device_command(DEVICE, 'true', actor='member_user')['exit_code'] == 0
    assert run.call_count == 1


def test_administrator_may_run_commands(permissions, run):
    permissions.administrator('device_admin')
    assert ResourceService().run_device_command(DEVICE, 'true', actor='device_admin')['exit_code'] == 0
    assert run.call_count == 1
