"""浏览器兼容单元：一起到达 Release 的文件组。

:module: operations.deployment
:design: decision_08bb2d9141f9
:importance: P1
"""
from pathlib import Path

from scripts import portal_release_units as units

ROOT = Path(__file__).resolve().parents[2]
ENTRY = 'management_portal/static/portal_chat_workspace.js'
SEARCH = 'management_portal/static/chat_workspace_search.js'


def current_text(name):
    path = ROOT / name
    return path.read_text(encoding='utf-8') if path.is_file() else None


def expand(*paths, released_text=current_text):
    return set(units.expand_release_unit_paths(paths, ROOT, released_text))


def test_chat_entry_and_modules_publish_together():
    """选中 Chat 入口脚本或任一 Chat 模块时，入口脚本与全部模块一起发布；页面的脚本清单与 Release 相同时页面不随行"""
    modules = {path.relative_to(ROOT).as_posix()
               for path in (ROOT / 'management_portal/static').glob('chat_workspace_*.js')}
    assert SEARCH in modules
    assert expand(SEARCH) == {ENTRY, *modules}
    assert expand(ENTRY) == {ENTRY, *modules}


def test_changed_chat_script_list_brings_its_pages_and_their_units():
    """页面或服务端公开静态文件的 Chat 脚本清单与 Release 不同时，它们随 Chat 发布，Lineage 页面再带上整个 Lineage 单元"""
    changed = ('management_portal/lineage.html', 'management_portal/serve_management_portal.py')

    def released_text(name):
        text = current_text(name)
        return text.replace('/static/chat_workspace_search.js', '') if name in changed else text

    expanded = expand(SEARCH, released_text=released_text)
    assert set(changed) <= expanded
    assert 'management_portal/static/hatg_panel.js' in expanded
    assert 'management_portal/wall.html' not in expanded
    # Selecting the page alone brings the Chat it now lists differently.
    assert ENTRY in expand('management_portal/lineage.html', released_text=released_text)


def test_page_absent_from_release_joins_when_it_loads_the_chat():
    """Release 中还没有的 Chat 页面随 Chat 发布"""
    def released_text(name):
        return None if name == 'management_portal/wall.html' else current_text(name)

    assert 'management_portal/wall.html' in expand(ENTRY, released_text=released_text)


def test_unchanged_chat_list_keeps_lineage_and_server_publications_apart():
    """Chat 脚本清单不变时，发布 Lineage 页面不带上 Chat，发布服务端文件不带上任何单元"""
    lineage = expand('management_portal/static/lineage_workbench.js')
    assert 'management_portal/lineage.html' in lineage
    assert ENTRY not in lineage
    assert expand('management_portal/serve_management_portal.py') == {'management_portal/serve_management_portal.py'}
    assert expand('scripts/tfi_dev.py') == {'scripts/tfi_dev.py'}


def test_without_released_content_no_page_joins():
    """不提供 Release 内容时只按入口脚本与模块扩展"""
    expanded = expand(SEARCH, released_text=None)
    assert ENTRY in expanded
    assert not any(path.endswith(('.html', '.py')) for path in expanded)
