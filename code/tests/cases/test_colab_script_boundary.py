"""Retired public actions and the script's retained device-domain permission."""
import sys
from types import SimpleNamespace

import pytest

from management_portal import google_cloud_devices as cloud, permission_system
from management_portal.hatg.contracts import InstructionError
from management_portal.permission_system.engine.model import Entry
from management_portal.permission_system.engine.store import Store
from management_portal.permission_system.par import PARPermissions
from management_portal.resources import isa
from management_portal.resources.providers import device
from management_portal.resources.transport import ResourceServiceClient
from scripts import colab_session

DEVICE = 'device_ca437b806200'


@pytest.mark.parametrize('action', ['colab.start', 'colab.stop'])
def test_retired_actions_rejected_before_domain_dispatch(monkeypatch, action):
    dispatched = []
    service = SimpleNamespace(get=lambda *a, **k: {'resource_type': 'device'},
                              action=lambda *a, **k: dispatched.append((a, k)))
    monkeypatch.setattr(isa, '_service', lambda: service)
    args = {'resource_id': DEVICE, 'action': action, 'arguments': {'hardware': 'A100'} if action.endswith('start') else {}}
    inv = SimpleNamespace(caller='alice', argument=args.get)
    with pytest.raises(InstructionError, match='没有声明动作') as exc:
        isa.run_resource_action(inv)
    assert exc.value.code == 'instruction.invalid_arguments'
    assert not dispatched


def test_provider_has_no_colab_bypass_and_rpc_has_no_allocation_wait(monkeypatch):
    registered = {}
    device.install_action_provider(SimpleNamespace(register_action_provider=lambda kind, actions: registered.update(actions)))
    assert {'ssh.test', 'ssh.run', 'ssh.credential.save', 'ssh.credential.delete'} <= registered.keys()
    assert not {'colab.start', 'colab.stop'} & registered.keys()
    client = object.__new__(ResourceServiceClient)
    client.timeout = 30
    requests = []
    def reject(method, payload, *, timeout):
        requests.append((method, payload, timeout))
        raise ValueError('retired action rejected by owner')
    monkeypatch.setattr(client, '_exchange', reject)
    with pytest.raises(ValueError, match='retired action'):
        client.action(DEVICE, 'colab.start', {'hardware': 'A100'}, actor='alice')
    assert requests[0][2] == 30


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
