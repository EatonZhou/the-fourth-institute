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


def test_lineage_and_server_publications_stay_apart_from_the_chat():
    """发布 Lineage 页面不带上 Chat，发布服务端文件不带上任何单元"""
    lineage = expand('management_portal/static/lineage_workbench.js')
    assert 'management_portal/lineage.html' in lineage
    assert ENTRY not in lineage
    assert expand('management_portal/serve_management_portal.py') == {'management_portal/serve_management_portal.py'}
    assert expand('scripts/tfi_dev.py') == {'scripts/tfi_dev.py'}
