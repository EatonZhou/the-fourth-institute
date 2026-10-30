"""浏览器兼容单元：一起到达 Release 的文件组。

:module: operations.deployment
:design: decision_08bb2d9141f9
:importance: P1
"""
import json
from pathlib import Path

from scripts import portal_release_units as units

ROOT = Path(__file__).resolve().parents[2]
GROUPS = 'management_portal/script_groups.json'
ENTRY = 'management_portal/static/portal_chat_workspace.js'
SEARCH = 'management_portal/static/chat_workspace_search.js'


def expand(*paths, root=ROOT):
    return set(units.expand_release_unit_paths(paths, root))


def test_chat_entry_modules_and_their_group_list_publish_together():
    """选中 Chat 入口脚本、任一 Chat 模块或脚本组清单时，三者整组发布，页面不随行"""
    modules = {path.relative_to(ROOT).as_posix()
               for path in (ROOT / 'management_portal/static').glob('chat_workspace_*.js')}
    assert SEARCH in modules
    for selected in (SEARCH, ENTRY, GROUPS):
        assert expand(selected) == {GROUPS, ENTRY, *modules}


def test_chat_unit_follows_the_group_list_of_the_published_tree(tmp_path):
    """Chat 单元按被发布的源码树中的脚本组清单取模块；清单中已删除的模块不阻止发布"""
    static = tmp_path / 'management_portal/static'
    static.mkdir(parents=True)
    (tmp_path / GROUPS).write_text(json.dumps({'chat-workspace': [
        '/static/chat_workspace_new.js', '/static/chat_workspace_removed.js', '/static/portal_chat_workspace.js']}))
    for name in ('chat_workspace_new.js', 'portal_chat_workspace.js', 'chat_workspace_unlisted.js'):
        (static / name).write_text('')
    assert expand(ENTRY, root=tmp_path) == {
        GROUPS, ENTRY, 'management_portal/static/chat_workspace_new.js'}


def test_workbench_root_and_parts_publish_together():
    """选中工作台根脚本或任一部件脚本时，脚本组中的全部工作台脚本整组发布，Chat 不随行"""
    listed = json.loads((ROOT / GROUPS).read_text(encoding='utf-8'))['portal-workbench']
    scripts = {f'management_portal{source}' for source in listed}
    assert 'management_portal/static/portal_workbench.js' in scripts and len(scripts) > 1
    for selected in scripts:
        assert expand(selected) == scripts


def test_workspace_surface_root_and_parts_publish_together():
    """选中工作区界面根脚本或任一部件脚本时，脚本组中的全部工作区界面脚本整组发布，Chat 不随行"""
    listed = json.loads((ROOT / GROUPS).read_text(encoding='utf-8'))['portal-workspace-surface']
    scripts = {f'management_portal{source}' for source in listed}
    files = {path.relative_to(ROOT).as_posix()
             for path in (ROOT / 'management_portal/static').glob('portal_workspace_surface*.js')}
    assert 'management_portal/static/portal_workspace_surface.js' in scripts and scripts == files
    for selected in scripts:
        assert expand(selected) == scripts


def test_page_scripts_publish_with_their_page():
    """选中 Paper、Article、Repo、首页或组件演示页的任一脚本、样式或页面本身时，页面与它按名加载的全部脚本和样式整组发布"""
    for page, prefix in units.PAGES:
        unit = set(units.page_unit(ROOT, page, prefix))
        static = {path.relative_to(ROOT).as_posix() for path in (ROOT / 'management_portal/static').glob(f'{prefix}*')
                  if path.suffix in {'.js', '.css'} and (path.stem == prefix or path.stem.startswith(f'{prefix}_'))}
        assert page in unit and f'management_portal/static/{prefix}.js' in unit
        assert static <= unit, (page, static - unit)
        for selected in unit:
            assert unit <= expand(selected), (page, selected)
    assert ENTRY not in expand('management_portal/static/repo_workspace.js')


def test_page_unit_follows_the_page_of_the_published_tree(tmp_path):
    """页面单元按被发布源码树中页面的脚本标签取文件；页面不再加载的旧模块不随行，已删除的模块不阻止发布"""
    static = tmp_path / 'management_portal/static'
    static.mkdir(parents=True)
    (tmp_path / 'management_portal/repo_workspace.html').write_text(
        '<link rel="stylesheet" href="/static/repo_workspace.css" />'
        '<script src="/static/portal_ui.js"></script>'
        '<script src="/static/repo_workspace_new.js" defer></script>'
        '<script src="/static/repo_workspace_removed.js" defer></script>'
        '<script src="/static/repo_workspace.js" defer></script>')
    for name in ('repo_workspace.css', 'portal_ui.js', 'repo_workspace_new.js', 'repo_workspace.js', 'repo_workspace_unlisted.js'):
        (static / name).write_text('')
    assert expand('management_portal/static/repo_workspace.js', root=tmp_path) == {
        'management_portal/repo_workspace.html', 'management_portal/static/repo_workspace.css',
        'management_portal/static/repo_workspace_new.js', 'management_portal/static/repo_workspace.js'}


def test_lineage_and_server_publications_stay_apart_from_the_chat():
    """发布 Lineage 页面不带上 Chat，发布服务端文件不带上任何单元"""
    lineage = expand('management_portal/static/lineage_workbench.js')
    assert 'management_portal/lineage.html' in lineage
    assert ENTRY not in lineage
    assert expand('management_portal/serve_management_portal.py') == {'management_portal/serve_management_portal.py'}
    assert expand('scripts/tfi_dev.py') == {'scripts/tfi_dev.py'}
