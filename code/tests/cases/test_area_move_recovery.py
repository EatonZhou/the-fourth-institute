"""Area relocation retains its source prefix until Provider migration completes.

:module: operations.deployment
:design: decision_d88306d0bd17
"""
import pytest

from management_common import host_instances
from management_common.instance_manifest import load
from scripts import instance_services, tfi_move_area as moves
from tests.cases.instance_test_support import select_instance


@pytest.fixture
def area_move(monkeypatch, tmp_path):
    monkeypatch.setattr(host_instances, 'user_home', lambda: tmp_path / 'user')
    instance = select_instance(monkeypatch, tmp_path / 'instance')
    host_instances.register(instance)
    (instance.root / 'runtime').mkdir()
    source = instance.area('projects')
    source.mkdir(parents=True)
    (source / 'marker').write_text('keep')
    calls = []
    monkeypatch.setattr(instance_services, 'lifecycle', lambda action, root: calls.append(action))
    monkeypatch.setattr(moves, '_launch', lambda root, record: None)
    monkeypatch.setattr(moves.time, 'sleep', lambda _: None)
    return instance, source, tmp_path / 'moved', calls


@pytest.mark.parametrize('failure', ['postgres', 'sessions'])
def test_area_move_failure_blocks_start_and_same_target_finishes_original_sessions(area_move, monkeypatch, failure):
    from management_portal import project_rename
    instance, source, target, calls = area_move
    def database(root):
        if failure == 'postgres':
            raise RuntimeError('database unavailable')
    monkeypatch.setattr(moves, '_start_database', database)
    monkeypatch.setattr(project_rename, 'relocate_provider_sessions', lambda a,b: {'errors':['failed'], 'conflicts':[]})
    job = moves.submit(instance.name, 'projects', str(target))['operation']
    failed = moves.execute(instance.name, job['id'])
    assert failed['status'] == 'failed' and failed['area_moved']
    assert (target / 'marker').read_text() == 'keep' and not source.exists()
    assert 'Area relocation' in instance_services.start_blocker(load(instance.root))
    assert 'start' not in calls and calls[-1] == 'stop'
    with pytest.raises(ValueError, match='pending area move'):
        moves.submit(instance.name, 'skills', str(target.with_name('other')))
    received = []
    monkeypatch.setattr(moves, '_start_database', lambda root: None)
    def sessions(a,b):
        received.append((a,b))
        return {'errors':[], 'conflicts':[]}
    monkeypatch.setattr(project_rename, 'relocate_provider_sessions', sessions)
    retry = moves.submit(instance.name, 'projects', str(target))['operation']
    assert retry['source'] == str(source)
    assert moves.execute(instance.name, retry['id'])['status'] == 'completed'
    assert received == [(str(source),str(target))] and calls[-1] == 'start'
    assert not (instance.root / moves.RECOVERY_RECORD).exists()
    assert (target / 'marker').read_text() == 'keep'


def test_failure_before_manifest_change_can_restart_original_location(area_move, monkeypatch):
    instance, source, target, calls = area_move
    def fail(*args):raise OSError('copy failed')
    monkeypatch.setattr(moves, 'move_area', fail)
    job = moves.submit(instance.name, 'projects', str(target))['operation']
    result = moves.execute(instance.name, job['id'])
    assert result['status'] == 'failed' and not result['area_moved']
    assert load(instance.root).area('projects') == source
    assert not (instance.root / moves.RECOVERY_RECORD).exists()
    assert calls == ['stop','start']
