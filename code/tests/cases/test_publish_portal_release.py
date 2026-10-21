""":design: decision_6febe7bb57f9"""
from contextlib import nullcontext
from tests.cases.test_portal_release import repo  # noqa: F401 - pytest fixture
from scripts import publish_portal_release as publish, portal_release, portal_services
from scripts.portal_dev_commit import git
import pytest


@pytest.fixture(autouse=True)
def quick_batch(monkeypatch):
    monkeypatch.setattr(publish.batch, 'QUIET_SECONDS', 0)
    monkeypatch.setattr(portal_services, 'instance_status', lambda *a, **k: {'runtime_ok': True})


def test_lineage_publication_paths_expand_to_one_browser_compatibility_unit():
    """发布 Lineage 前端文件时扩展为整组浏览器兼容文件，且组内文件都存在

    :design-point:"""
    expanded = set(publish.expand_release_unit_paths((
        'management_portal/static/lineage_workbench.js',
    )))

    assert 'management_portal/lineage.html' in expanded
    assert 'management_portal/static/portal_lineage_catalogs.js' in expanded
    assert 'management_portal/static/portal_hatg_loader.js' in expanded
    assert 'management_portal/static/hatg_panel.js' in expanded
    # A removed module must leave the unit too; otherwise every Lineage
    # publication is rejected before staging any source paths.
    assert all((publish.ROOT / member).is_file() for member in expanded)


def test_publication_returns_results_without_reports_and_retries_current_source(repo,tmp_path,monkeypatch):
    """发布直接返回结果不写发布报告，重试按当前源码重新发布，健康检查失败时 Release 保持原代码

    :design-point:"""
    root=tmp_path/'release'
    initial=git(repo,'rev-parse','HEAD').decode().strip()
    git(repo,'worktree','add','-b',portal_release.RELEASE_BRANCH,str(root),initial)
    portal_release.write_manifest(root,initial,changed=[],message='base')
    monkeypatch.setattr(publish,'ROOT',repo)
    monkeypatch.setattr(publish,'instance_root',lambda:tmp_path/'instances')
    monkeypatch.setattr(portal_release,'RELEASE_ROOT',root)
    monkeypatch.setattr(publish,'switch_lock',nullcontext)
    monkeypatch.setattr(portal_services,'operation_lock',lambda *a:nullcontext())
    monkeypatch.setattr(publish,'prepare_runtime',lambda:None)
    router_restarts = []
    monkeypatch.setattr(portal_services, 'restart_user_router', lambda: router_restarts.append(True) or {'ok': True})
    # What the running router loaded is unknown at first; later it is current.
    router_changes = [None]
    monkeypatch.setattr(portal_services, 'user_router_changes', lambda: router_changes[0])
    monkeypatch.setattr(portal_services, 'user_router_health', lambda: {'ok': True})
    import json
    cache_path = tmp_path / 'status_cache.json'
    cache_path.write_text(json.dumps({'agent_status_summary:v4:old': {}, 'disk_status:fixture': {}}))
    monkeypatch.setenv('AGENT_STATUS_CACHE_PATH', str(cache_path))

    restarts = []
    def restart(instance, *, notice_seconds, components=None, release_commit='', units_prepared=False):
        # Release Web restarts on the published commit: the release branch advances while Web is stopped.
        assert instance == 'release' and tuple(components) == ('web',) and release_commit
        restarts.append(release_commit)
        portal_release.advance(root, release_commit)
        return {'ok': True}

    monkeypatch.setattr(portal_services,'restart',restart)
    (repo/'a.txt').write_text('published through wall')
    options=publish.PublishOptions('one feature',('a.txt',),origin='wall',wall_job='wall-real',blocks=('dev#feature',))
    result=publish.publish(options)
    assert result['ok'] and result['paths']==['a.txt']
    assert result['release_event']['ok']
    from management_common.portal_release_events import after
    assert after(result['release_event']['sequence'] - 1)[0]['commit'] == result['commit']
    assert router_restarts == [True]
    assert result['router_health']['ok'] and 'shared:user-router' in result['components']
    # The restarted router runs the current code: later publications leave it and its connections alone.
    router_changes[0] = []
    assert result['blocks']==['dev#feature']
    assert (root/'a.txt').read_text()=='published through wall'
    assert result['parent_commit']==initial and result['status_cache']=={'removed_entries':1}
    assert json.loads(cache_path.read_text()) == {'disk_status:fixture': {}}
    assert publish.publish(options)['commit']==result['commit']
    assert restarts == [result['commit']]
    assert not (tmp_path/'instances/release/publications').exists()
    assert not (tmp_path/'instances/release/publication-index.json').exists()
    (repo/'new.txt').write_text('new file in full publication')
    full=publish.publish(publish.PublishOptions('full sync',all_files=True,origin='wall',wall_job='wall-full'))
    assert full['ok'] and full['all_files'] is True and full['blocks']==[]
    assert router_restarts == [True] and 'shared:user-router' not in full['components'] and full['router_health']['ok']
    assert (root/'new.txt').read_text()=='new file in full publication'

    assert full['dev_commit'] == full['release_commit'] == git(repo, 'rev-parse', 'HEAD').decode().strip()
    assert not git(repo, 'status', '--porcelain')
    assert git(root, 'rev-parse', 'HEAD').decode().strip() == full['commit']
    assert git(repo, 'rev-parse', portal_release.RELEASE_BRANCH).decode().strip() == full['commit']
    assert portal_release.load_release_manifest(root)['source_commit'] == full['commit']
    again = publish.publish(publish.PublishOptions('unchanged full', all_files=True, origin='wall', wall_job='wall-other'))
    assert again['ok'] and again['commit'] == full['commit'] and again['changed_paths'] == []
    (repo/'new.txt').write_text('edit after the first successful attempt')
    retried = publish.publish(publish.PublishOptions('retry', all_files=True, origin='wall', wall_job='wall-full'))
    assert retried['ok'] and retried['commit'] != full['commit']
    assert (root/'new.txt').read_text() == 'edit after the first successful attempt'
    monkeypatch.setattr(portal_services, 'restart', lambda *a, **k: {'ok': False})
    (repo/'a.txt').write_text('never reaches Release')
    failed = publish.publish(publish.PublishOptions('failed health', ('a.txt',)))
    assert not failed['ok'] and failed['status'] == 'failed'
    # Release stays on the code it ran before, and its manifest still describes that code.
    assert failed['active_commit'] == retried['commit'] != failed['commit']
    assert (root/'a.txt').read_text()=='published through wall'
    assert portal_release.load_release_manifest(root)['source_commit'] == retried['commit']
    assert not (tmp_path/'instances/release/publications').exists()
    assert not (tmp_path/'instances/release/publication-index.json').exists()



def test_concurrent_publications_activate_latest_requested_commit_once(repo, tmp_path, monkeypatch):
    """并发发布合并且只切换一次 Release

    :design-point:
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    root = tmp_path / 'release'
    initial = git(repo, 'rev-parse', 'HEAD').decode().strip()
    git(repo, 'worktree', 'add', '-b', portal_release.RELEASE_BRANCH, str(root), initial)
    portal_release.write_manifest(root, initial, changed=[], message='base')
    monkeypatch.setattr(publish, 'ROOT', repo)
    monkeypatch.setattr(publish, 'instance_root', lambda: tmp_path / 'instances')
    monkeypatch.setattr(portal_release, 'RELEASE_ROOT', root)
    monkeypatch.setattr(publish, 'prepare_runtime', lambda: None)
    monkeypatch.setattr(portal_services, 'operation_lock', lambda *a: nullcontext())
    monkeypatch.setattr(portal_services, 'user_router_changes', lambda: [])
    monkeypatch.setattr(portal_services, 'user_router_health', lambda: {'ok': True})
    monkeypatch.setenv('AGENT_STATUS_CACHE_PATH', str(tmp_path / 'cache.json'))
    first, both = Event(), Event()
    calls = []
    def wait(_):
        if first.is_set():
            both.set()
        first.set()
        assert both.wait(5)
    monkeypatch.setattr(publish.batch, 'wait', wait)
    def restart(instance, **kw):
        calls.append(kw['release_commit'])
        portal_release.advance(root, kw['release_commit'])
        return {'ok': True}
    monkeypatch.setattr(portal_services, 'restart', restart)
    (repo / 'a.txt').write_text('first')
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(publish.publish, publish.PublishOptions('first', ('a.txt',)))
        assert first.wait(5)
        (repo / 'b.txt').write_text('second')
        b = pool.submit(publish.publish, publish.PublishOptions('second', ('b.txt',)))
        left, right = a.result(), b.result()
    assert left['ok'] and right['ok']
    assert calls == [right['commit']]
    assert left['release_commit'] == right['release_commit'] == right['commit']
    assert (root / 'a.txt').read_text() == 'first'
    assert (root / 'b.txt').read_text() == 'second'


def test_detached_publisher_loads_database_environment_without_copying_secrets(tmp_path, monkeypatch):
    """后台发布单元通过 EnvironmentFile 加载数据库环境，不在命令行复制密钥

    :design-point:"""
    from types import SimpleNamespace
    commands = []
    monkeypatch.setattr(publish, 'LOG_ROOT', tmp_path)
    monkeypatch.delenv('PORTAL_POSTGRES_ENV_FILE', raising=False)
    monkeypatch.setattr(publish, 'read_site_config', lambda: {
        'deployment': {'postgres_env_file': str(tmp_path / 'postgres.env')}})
    monkeypatch.setattr(publish.subprocess, 'run', lambda args, **kw: commands.append(args) or SimpleNamespace(returncode=0))
    assert publish.detached_publication(['--message', 'audit']) == 0
    assert f'EnvironmentFile=-{tmp_path / "postgres.env"}' in commands[0]
    assert not any('PORTAL_DATABASE_URL=' in arg for arg in commands[0])


def test_publication_commit_is_pushed_to_every_remote_without_force(tmp_path):
    """发布提交推送到所有远端

    发布工具创建的提交推送到每个已配置的远端；某个远端历史已分叉时推送被拒绝并记录，不强制覆盖。

    :importance: P1
    :design: decision_08bb2d9141f9
    :design-point:
    """
    import subprocess

    def run(*args, cwd):
        subprocess.run(args, cwd=cwd, check=True, capture_output=True)

    work = tmp_path / 'work'
    work.mkdir()
    run('git', 'init', '-b', 'main', cwd=work)
    run('git', 'config', 'user.email', 'test@example.com', cwd=work)
    run('git', 'config', 'user.name', 'Test', cwd=work)
    (work / 'a.txt').write_text('base')
    run('git', 'add', 'a.txt', cwd=work)
    run('git', 'commit', '-m', 'base', cwd=work)
    for name in ('origin', 'mirror'):
        run('git', 'init', '--bare', '-b', 'main', str(tmp_path / f'{name}.git'), cwd=tmp_path)
        run('git', 'remote', 'add', name, str(tmp_path / f'{name}.git'), cwd=work)
        run('git', 'push', name, 'main', cwd=work)
    # The mirror moves on independently, so a normal push must be rejected.
    other = tmp_path / 'other'
    run('git', 'clone', str(tmp_path / 'mirror.git'), str(other), cwd=tmp_path)
    run('git', 'config', 'user.email', 'test@example.com', cwd=other)
    run('git', 'config', 'user.name', 'Test', cwd=other)
    (other / 'b.txt').write_text('elsewhere')
    run('git', 'add', 'b.txt', cwd=other)
    run('git', 'commit', '-m', 'elsewhere', cwd=other)
    run('git', 'push', 'origin', 'main', cwd=other)
    (work / 'a.txt').write_text('published')
    run('git', 'commit', '-am', 'publish', cwd=work)
    commit = git(work, 'rev-parse', 'HEAD').decode().strip()

    result = publish.push_remotes(work, commit)

    assert result['branch'] == 'main' and result['commit'] == commit
    assert result['remotes']['origin'] == {'ok': True}
    assert result['remotes']['mirror']['ok'] is False
    assert result['ok'] is False
    assert git(tmp_path / 'origin.git', 'rev-parse', 'main').decode().strip() == commit
    # Nothing is forced over the mirror's own history.
    assert git(tmp_path / 'mirror.git', 'rev-parse', 'main').decode().strip() != commit
