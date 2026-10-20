"""Publication and service-control failure boundaries, without live services.

:module: spec.design-governance
:importance: P2
"""
import json
import subprocess

import pytest

from scripts import portal_dev_restart_requests as requests
from scripts import portal_dev_source_reloader as reloader
from scripts import portal_services as services
from scripts import publish_portal_release as publisher


@pytest.mark.parametrize('submitted', [((), ('web',)), (('web',), ())])
def test_automatic_deployment_survives_merged_explicit_requests(tmp_path, submitted):
    """自动部署请求与显式重启请求合并后仍保留自动标记，组件取并集"""
    kwargs = {'path': tmp_path / 'request.json', 'lock_path': tmp_path / 'request.lock'}
    for components in submitted:
        requests.submit_restart_request(components, **kwargs)
    request = requests.read_restart_request(kwargs['path'])
    assert request['auto'] is True
    assert request['components'] == ['web']


def test_merged_request_deploys_detected_and_requested_components(monkeypatch):
    """合并后的重启请求同时部署检测到变化的组件和显式请求的组件"""
    baseline = {name: () for name in reloader.ALL_COMPONENTS}
    changed = {**baseline, 'lineage': (('lineage.py', 2, 1),)}
    monkeypatch.setattr(reloader, 'read_restart_request', lambda: {
        'request_id': 'merged', 'revision': 2, 'components': ['web'], 'auto': True})
    monkeypatch.setattr(reloader, 'source_state', lambda: ())
    monkeypatch.setattr(reloader, 'component_source_states', lambda _: changed)
    monkeypatch.setattr(reloader, 'read_deployed_source_states', lambda _: baseline)
    monkeypatch.setattr(reloader, 'write_deployed_source_states', lambda _: None)
    monkeypatch.setattr(reloader, 'acknowledge_restart_request', lambda *args: True)
    called = []
    monkeypatch.setattr(reloader, 'restart_dev', lambda components: called.append(components) or reloader.RESTART_OK)
    reloader.process_restart_request(baseline, '')
    assert called == [{'web', 'lineage'}]


@pytest.mark.parametrize('instance', ['dev', 'release'])
def test_maintenance_blocks_restart_before_any_systemctl(tmp_path, monkeypatch, instance):
    """维护期间 Dev 或 Release 的重启在调用任何 systemctl 之前被拒绝"""
    monkeypatch.setattr(services, 'INSTANCE_ROOT', tmp_path)
    fence = tmp_path / instance / 'maintenance.json'
    fence.parent.mkdir()
    fence.write_text('{}')
    monkeypatch.setattr(services, '_run', lambda *a, **kw: pytest.fail('service action during maintenance'))
    monkeypatch.setattr(services, '_prepare_units', lambda *a: pytest.fail('install during maintenance'))
    with pytest.raises(RuntimeError, match='Standalone TFI'):
        services.restart(instance, notice_seconds=5, components=('web',))
    if instance == 'dev':
        with pytest.raises(RuntimeError, match='Standalone TFI'):
            services.request_restart('dev')




def test_removed_implicit_release_member_does_not_block_existing_paths(tmp_path, monkeypatch):
    """发布单元中已删除的隐含成员不阻止发布仍存在的路径"""
    monkeypatch.setattr(publisher, 'ROOT', tmp_path)
    monkeypatch.setattr(publisher, 'RELEASE_PATH_UNITS', (('page.js', 'removed.js'),))
    (tmp_path / 'page.js').write_text('current')
    assert publisher.expand_release_unit_paths(('page.js',)) == ('page.js',)
    assert publisher.expand_release_unit_paths(('removed.js',)) == ('page.js', 'removed.js')


def test_detached_publication_keeps_json_stdout_separate_from_diagnostics(tmp_path, monkeypatch, capsys):
    """后台发布经 systemd-run 运行，JSON 结果写到 stdout，诊断信息写到 stderr

    test_abandoned_publications_become_failed_without_touching_completed"""
    monkeypatch.setattr(publisher, 'LOG_ROOT', tmp_path / 'publication-logs')
    def launch(command, **kwargs):
        from pathlib import Path
        assert command[:4] == ['systemd-run', '--user', '--wait', '--collect']
        for option in command:
            if option.startswith('StandardOutput=append:'):
                Path(option.split('append:', 1)[1]).write_text('{"ok": true}\n')
            if option.startswith('StandardError=append:'):
                Path(option.split('append:', 1)[1]).write_text('runtime preparation\n')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(publisher.subprocess, 'run', launch)
    assert publisher.detached_publication(['--message', 'test', '--path', 'page.js']) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {'ok': True}
    assert captured.err == 'runtime preparation\n'


def test_reloader_announces_ready_only_after_installing_wake_handler(tmp_path, monkeypatch):
    """源码重载服务先安装唤醒信号处理器，再向 systemd 报告就绪

    :module: platform.core"""
    import socket
    installed = []
    path = tmp_path / 'notify.sock'
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(str(path))
        receiver.settimeout(1)
        monkeypatch.setenv('NOTIFY_SOCKET', str(path))
        monkeypatch.setattr(reloader.signal, 'signal', lambda signum, handler: installed.append((signum, handler)))
        def initial_scan():
            assert installed == [(reloader.signal.SIGUSR1, reloader._wake_for_restart)]
            assert receiver.recv(64) == b'READY=1'
            raise RuntimeError('end isolated readiness probe')
        monkeypatch.setattr(reloader, 'source_state', initial_scan)
        with pytest.raises(RuntimeError, match='end isolated readiness probe'):
            reloader.watch()
