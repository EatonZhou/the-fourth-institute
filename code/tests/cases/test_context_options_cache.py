from management_portal import agent_context_selection as owner
import pytest


def test_context_options_use_host_paths_and_cache_scan(monkeypatch, tmp_path):
    """上下文选项使用主机路径并在缓存过期后重新扫描"""
    skills = tmp_path / 'Skills'
    marker = skills / 'example' / 'SKILL.md'
    marker.parent.mkdir(parents=True)
    marker.write_text('# Example\nUseful skill')
    monkeypatch.setattr(owner, 'HOST_SKILLS_ROOT', skills)
    monkeypatch.setattr(owner, 'HOST_AGENTS_ROOT', tmp_path / 'Agents')
    monkeypatch.setattr(owner, 'THIRD_PARTY_SKILLS_ROOT', skills / 'third-party')
    now = [120.0]
    monkeypatch.setattr(owner.time, 'monotonic', lambda: now[0])
    owner._cached_options.cache_clear()
    first = owner.list_options()
    assert first['skills'][0]['path'] == str(marker.parent)
    first['skills'][0]['name'] = 'mutated'
    marker.write_text('# Changed\nUpdated skill')
    assert owner.list_options()['skills'][0]['name'] == 'Example'
    now[0] += 60
    assert owner.list_options()['skills'][0]['name'] == 'Changed'


def test_message_selection_never_discovers_options(monkeypatch, tmp_path):
    """解析消息选择只读取所选上下文且不枚举可选项"""
    skills = tmp_path / 'Skills'
    marker = skills / 'third-party' / 'example' / 'SKILL.md'
    marker.parent.mkdir(parents=True)
    marker.write_text('# Selected\nUseful skill')
    monkeypatch.setattr(owner, 'HOST_SKILLS_ROOT', skills)
    monkeypatch.setattr(owner, 'list_options', lambda: pytest.fail('message enumerated options'))
    monkeypatch.setattr(owner, '_personal_folders', lambda: pytest.fail('unselected Agents queried'))
    captured = []
    monkeypatch.setattr(owner, 'resolve_resource_references',
                        lambda refs, **kw: captured.append((refs, kw)) or [])
    assert owner.resolve({}, actor='sender', content='resource_0123456789ab')['skills'] == []
    assert captured[-1][1] == {'actor': 'sender', 'content': 'resource_0123456789ab'}
    selected = owner.resolve({'skills': ['third-party/example', {'path': str(marker.parent)}]})
    assert [item['id'] for item in selected['skills']] == ['third-party/example']
    assert selected['skills'][0]['name'] == 'Selected'
    marker.write_text('# Updated\nChanged')
    assert owner.resolve({'skills': ['third-party/example']})['skills'][0]['name'] == 'Updated'
    with pytest.raises(ValueError, match='无效'):
        owner.resolve({'skills': ['missing']})


def test_selected_context_rejects_private_and_outside_paths(monkeypatch, tmp_path):
    """选定上下文拒绝私有目录及越界路径"""
    agents = tmp_path / 'Agents'
    agents.mkdir()
    private = agents / 'personal'
    private.mkdir()
    (private / 'README.md').write_text('# Private')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'README.md').write_text('# Outside')
    (agents / 'escape').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(owner, 'HOST_AGENTS_ROOT', agents)
    monkeypatch.setattr(owner, '_personal_folders', lambda: [(str(private), 'lineage_private', 'owner')])
    monkeypatch.setattr(owner, 'resolve_resource_references', lambda *a, **kw: [])
    for item in ['personal', 'escape', '../outside']:
        with pytest.raises(ValueError, match='无效'):
            owner.resolve({'agents': [item]})
