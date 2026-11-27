"""Retired public actions and the script's retained device-domain permission."""
import sys
from types import SimpleNamespace

import pytest

from management_portal import google_cloud_devices as cloud, permission_system
from management_portal.permission_system.engine.model import Entry
from management_portal.permission_system.engine.store import Store
from management_portal.permission_system.par import PARPermissions
from management_portal.resources import isa
from management_portal.resources.providers import device
from management_portal.resources.transport import ResourceServiceClient
from scripts import colab_session

DEVICE = 'device_ca437b806200'


def test_generic_resource_actions_are_gone_so_colab_start_stop_cannot_be_dispatched():
    """没有通用的资源动作入口：colab.start 与 colab.stop 不再有任何公开路径"""
    from management_portal.hatg.catalog import instruction_catalog

    assert 'RunResourceAction' not in instruction_catalog()
    assert not hasattr(isa, 'run_resource_action')
    assert not hasattr(ResourceServiceClient, 'action')


def test_provider_has_no_colab_bypass_and_rpc_has_no_allocation_wait():
    from management_portal.hatg.catalog import instruction_catalog

    names = set(instruction_catalog())
    # The device's SSH operations are named instructions; none starts or stops a Colab session.
    assert {'CheckDeviceSsh', 'RunDeviceCommand', 'UpdateDeviceSshCredential', 'DeleteDeviceSshCredential'} <= names
    assert not {name for name in names if 'colab' in name.lower()}
    assert not hasattr(device, 'install_action_provider')


@pytest.fixture
def domain(monkeypatch, tmp_path):
    store = Store()
    owner = PARPermissions(store)
    monkeypatch.setattr(permission_system, 'active', lambda: owner)
    monkeypatch.setattr(permission_system, '_off', lambda: False)
    with store.transaction() as con:
        store.add(con, [Entry('colab-contributor', 'permit', 'alice', '@contributor', DEVICE),
                        Entry('colab-viewer', 'permit', 'bob', '@viewer', DEVICE),
                        Entry('other-device', 'permit', 'carol', '@contributor', 'device_aaaaaaaaaaaa')])
        store.bump(con)
    # No public action decision is needed after retirement; the real PAR role check is decisive.
    monkeypatch.setattr(permission_system, 'allows', lambda *a, **k: pytest.fail('Retired action authorization used'))
    (tmp_path / 'colab-cli').mkdir()
    monkeypatch.setattr(cloud, 'portal_path', lambda _: tmp_path)
    monkeypatch.setattr(cloud, 'settings', lambda: {'compute_device_id': DEVICE})
    monkeypatch.setattr(cloud, '_colab_command', lambda _: ['fixture-colab'])
    state = SimpleNamespace(store=store, calls=[], running=False, outputs=[], caller='alice', arguments={})
    def read(argv, **kwargs):
        state.calls.append((argv, kwargs))
        if argv[1] == 'status':
            return ('[portal-colab] endpoint | Hardware: A100 | Status: IDLE' if state.running
                    else "[colab] Session 'portal-colab' not found.")
        if argv[1] == 'new':
            assert kwargs['timeout'] == cloud.COLAB_ALLOCATION_TIMEOUT
            state.running = True
            return '[colab] Session READY.'
        assert argv[1] == 'stop' and kwargs['timeout'] == 90
        state.running = False
        return '[colab] Session terminated.'
    monkeypatch.setattr(cloud, '_read', read)
    # Trusted caller establishment is tested by the RunScript owner, not simulated as proof here.
    monkeypatch.setattr(colab_session, 'script_context', lambda operation: {
        'caller_id': state.caller, 'arguments': state.arguments})
    def resolve(name, **args):
        assert name == 'ResolveResource' and args == {'address': DEVICE}
        return {'target': {'resource_type': 'device'}, 'resource': {'resource_id': DEVICE}}
    monkeypatch.setitem(sys.modules, 'portal_script', SimpleNamespace(isa=resolve, set_output=state.outputs.append))
    return state


@pytest.mark.parametrize('running', [True, False])
def test_script_uses_device_role_and_rechecks_deny_and_revocation(domain, running):
    domain.running = not running
    domain.arguments = {'resource_id': DEVICE, 'running': running, **({'hardware': 'A100'} if running else {})}
    for actor in ['bob', 'carol']:
        domain.caller = actor
        with pytest.raises(PermissionError):
            colab_session.main()
    assert not domain.calls and not domain.outputs
    domain.caller = 'alice'
    assert colab_session.main() == 0
    assert domain.outputs == [{'session': 'portal-colab', 'running': running, 'changed': True}]
    n = len(domain.calls)
    with domain.store.transaction() as con:
        domain.store.add(con, [Entry('deny-colab', 'deny', 'alice', '@contributor', DEVICE)])
        domain.store.bump(con)
    with pytest.raises(PermissionError):
        colab_session.main()
    with domain.store.transaction() as con:
        domain.store.delete(con, where="entry_id IN ('deny-colab','colab-contributor')")
        domain.store.bump(con)
    with pytest.raises(PermissionError):
        colab_session.main()
    assert len(domain.calls) == n and len(domain.outputs) == 1


def test_domain_keeps_binding_hardware_and_cli_failure_closed(domain, monkeypatch):
    with domain.store.transaction() as con:
        domain.store.add(con, [Entry('other-grant', 'permit', 'alice', '@contributor', 'device_aaaaaaaaaaaa')])
        domain.store.bump(con)
    with pytest.raises(ValueError, match='configured Colab device'):
        cloud.change_colab_session('device_aaaaaaaaaaaa', 'colab.start', {'hardware': 'A100'}, actor='alice')
    with pytest.raises(ValueError, match='supported Colab'):
        cloud.change_colab_session(DEVICE, 'colab.start', {'hardware': 'invalid'}, actor='alice')
    assert not domain.calls
    def denied(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout='')
    monkeypatch.setattr(cloud.subprocess, 'run', denied)
    # Exercise the real external-command error translator without making an external request.
    monkeypatch.setattr(cloud, '_read', REAL_READ)
    with pytest.raises(ValueError, match='authorization, availability and quota'):
        cloud.change_colab_session(DEVICE, 'colab.start', {'hardware': 'A100'}, actor='alice')


REAL_READ = cloud._read
