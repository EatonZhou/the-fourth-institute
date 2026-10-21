""":importance: P2"""
from types import SimpleNamespace

import pytest

from management_portal import wall_backend as wall, wall_residents
from tests.cases.test_wall import wall_project


@pytest.fixture
def allocation(monkeypatch):
    roles, releases = {}, []
    def release(app, actor, selected, *, retain):
        released = set(selected.values()) - retain
        releases.append((dict(selected), set(retain)))
        for name, rid in list(roles.items()):
            if selected.get(name) == rid and rid in released:
                del roles[name]
        return released
    app = {'bindings': {'main': {'residents': roles}}}
    service = SimpleNamespace(store=SimpleNamespace(builtin=lambda *_: app), release_roles=release)
    monkeypatch.setattr(wall_residents, 'get_service', lambda: service)
    return roles, releases, service


def test_reject_releases_execution_and_all_discussions_only(wall_project, allocation):
    """驳回 WALL 条目释放其执行 Resident 和全部讨论 Resident，不影响其他条目

    :design: decision_06defc47e960"""
    roles, releases, _ = allocation
    token = wall.digest('plan.md#plan')
    work = f'wall-work:portal:{token}'
    roles.update({work: 'executor', f'wall-discussion:portal:{token}:alice': 'alice-chat',
                  f'wall-discussion:portal:{token}:bob': 'bob-chat', 'wall-work:elsewhere:x': 'other'})
    wall._change(lambda data: data['discussions'].update({'alice': {'resident_id': 'alice-chat'}}))
    wall.action({'action': 'reject', 'block': 'plan.md#plan'}, 'owner')
    assert roles == {'wall-work:elsewhere:x': 'other'}
    assert not wall._read_state()['discussions']
    assert not wall._read_state()['resident_cleanup']
    assert (wall_project / 'plan.md').exists()


def test_cleanup_failure_retries_after_markdown_has_been_removed(wall_project, allocation):
    """驳回时 Resident 释放失败会留下待清理记录，Markdown 删除后再次驳回可完成清理

    :design: decision_06defc47e960"""
    roles, _, service = allocation
    role = f"wall-work:portal:{wall.digest('plan.md#plan')}"
    roles[role] = 'executor'
    original = service.release_roles
    service.release_roles = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('busy'))
    result = wall.action({'action': 'reject', 'block': 'plan.md#plan'}, 'owner')
    assert result['removed'] and result['cleanup'] == 'pending'
    assert wall._read_state()['resident_cleanup']
    service.release_roles = original
    assert wall.action({'action': 'reject', 'block': 'plan.md#plan'}, 'owner')['removed']
    assert not roles and not wall._read_state()['resident_cleanup']


def test_failed_markdown_write_does_not_delete_identity(wall_project, allocation, monkeypatch):
    """驳回时 Markdown 写入失败则不释放任何 Resident

    :design: decision_06defc47e960"""
    roles, releases, _ = allocation
    roles[f"wall-work:portal:{wall.digest('plan.md#plan')}"] = 'executor'
    monkeypatch.setattr(wall, 'write_source', lambda *a, **kw: (_ for _ in ()).throw(OSError('disk failure')))
    with pytest.raises(OSError):
        wall.action({'action': 'reject', 'block': 'plan.md#plan'}, 'owner')
    with pytest.raises(OSError):
        wall._drain_resident_cleanup('portal', 'owner')
    assert not releases and roles


def test_dev_reference_retains_executor_until_last_card_is_removed(wall_project, allocation):
    """dev 卡片仍引用执行 Resident 时驳回不释放它，最后一张卡片删除后才释放

    :design: decision_06defc47e960"""
    roles, _, _ = allocation
    rid = 'resident_123456789abc'
    roles[f"wall-work:portal:{wall.digest('plan.md#plan')}"] = rid
    dev = wall_project / '.wall' / 'dev.md'
    dev.parent.mkdir()
    dev.write_text('# Dev\n<!-- wall: {"key":"result","kind":"improvement","status":"dev","resident_id":"'+rid+'"} -->\n- **Result**. Done.\n')
    wall._ensure_local_index(wall_project, '.wall/dev.md', 'direction')
    wall.action({'action': 'reject', 'block': 'plan.md#plan'}, 'owner')
    assert rid in roles.values()
    wall._remove_point(wall.project('portal', 'owner'), '.wall/dev.md#result', 'owner')
    wall._drain_resident_cleanup('portal', 'owner')
    assert not roles


def test_unregister_releases_project_slots_and_preserves_source(wall_project, allocation, tmp_path):
    """注销 WALL Project 释放其全部工作与讨论 Resident，保留源文件

    :design: decision_06defc47e960"""
    roles, _, _ = allocation
    root = tmp_path / 'sample'
    root.mkdir()
    (root / 'SPEC.md').write_text('# Sample')
    key = wall.register({'root': str(root), 'project_id': 'AI员工'}, 'owner')['project']['id']
    roles.update({f'wall-work:{key}:analyse': 'analysis', f'wall-discussion:{key}:hash:bob': 'chat',
                  'wall-work:portal:analyse': 'keep'})
    wall._change(lambda d: d['discussions'].update({'sample': {'resident_id': 'chat', 'project': key}}))
    wall.unregister({'project': key}, 'owner')
    assert roles == {'wall-work:portal:analyse': 'keep'}
    assert key not in wall._read_state()['projects'] and not wall._read_state()['discussions']
    assert (root / 'SPEC.md').exists()


def test_active_job_blocks_rejection_before_removing_source(wall_project, allocation):
    """WALL 条目有运行中的任务时拒绝驳回，源文件与待清理记录都不变

    :design: decision_06defc47e960"""
    wall._change(lambda d: d['jobs'].update({'active': {'project': 'portal', 'block': 'plan.md#plan', 'status': 'running'}}))
    with pytest.raises(ValueError, match='先等待'):
        wall.action({'action': 'reject', 'block': 'plan.md#plan'}, 'owner')
    assert not wall._read_state()['resident_cleanup']
    assert 'plan.md' in (wall_project / 'IMPROVE.md').read_text()


@pytest.mark.parametrize('partial_release', [False, True])
def test_unregister_failure_reports_pending_cleanup_and_resumes(wall_project, allocation, tmp_path, partial_release):
    """注销 Project 时 Resident 释放中断返回可重试的 503 待清理回执，重试后完成注销并保留源文件"""
    from management_portal.http_errors import exception_http_error

    roles, _, service = allocation
    root = tmp_path / 'pending-cleanup'
    root.mkdir()
    source = root / 'SPEC.md'
    source.write_text('# Keep source')
    key = wall.register({'root': str(root), 'project_id': 'AI员工'}, 'owner')['project']['id']
    first, second = f'wall-work:{key}:first', f'wall-work:{key}:second'
    roles.update({first: 'first', second: 'second', 'wall-work:portal:other': 'keep'})
    original = service.release_roles

    def interrupted(*args, **kwargs):
        if partial_release:
            roles.pop(first)
        raise ValueError('Object of type datetime is not JSON serializable')

    service.release_roles = interrupted
    with pytest.raises(wall.OperationError) as caught:
        wall.unregister({'project': key}, 'owner')
    status, receipt = exception_http_error(caught.value)
    assert status == 503
    assert receipt['code'] == 'wall.unregister_pending'
    assert receipt['commit_state'] == 'unknown' and receipt['retryable']
    assert receipt['details']['project'] == key
    assert receipt['details']['cleanup'] == 'pending'
    assert wall._read_state()['projects'][key]['deleting']
    assert roles[second] == 'second' and source.read_text() == '# Keep source'

    service.release_roles = original
    assert wall.unregister({'project': key}, 'owner') == {'removed': True}
    assert key not in wall._read_state()['projects']
    assert roles == {'wall-work:portal:other': 'keep'}
    assert source.read_text() == '# Keep source'
