"""Features a Portal package may leave out (management_portal/portal_features.py, decision_15aab18c0ab3).

Core code reaches a feature's part in a shared action only through the
feature declarations; a feature whose modules are not installed contributes
nothing and the rest of the Portal goes on without it.
"""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from management_portal import agent_conversation_index_backend as index
from management_portal import mailbox_sources, portal_features

REPO = Path(__file__).resolve().parents[2]

# Runs in a fresh interpreter: every feature module is made unimportable and
# the resource types they provide are dropped, as in a package that leaves all
# features out, before Web is imported.
WEB_WITHOUT_FEATURES = """
import fnmatch, importlib.abc, json, sys
from management_portal.portal_features import FEATURES

patterns = [pattern for feature in FEATURES for pattern in feature.modules]
from tests.cases.feature_packaging import drop_resource_types
drop_resource_types(patterns)


def left_out(name):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


class LeftOut(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if left_out(name):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)


sys.meta_path.insert(0, LeftOut())
from management_portal import asgi, frontend_manifest, portal_features, web_isa
from management_portal import serve_management_portal as shell
from management_portal.http_portal_pages import home_page
from management_portal.hatg.catalog import categories, instruction_catalog
from management_portal.hatg.kernel import InstructionKernel
from management_portal.watchboards import service as watchboards

kernel = InstructionKernel(web_isa.binding(), store=None)
try:
    kernel.binding.handlers["SearchMessages"](None)
except Exception as error:
    chatroom_search = getattr(error, "code", type(error).__name__)
from management_portal import application_targets
from management_portal.society_service import state_store


def known_application(name):
    try:
        return bool(application_targets.target_field(name))
    except ValueError:
        return False


native_views = []
shell._PORTAL_PAGES_HTTP.respond = lambda _request, status, payload: native_views.append(payload)
shell._PORTAL_PAGES_HTTP.development_native_views(None, shell.urlparse("/development/native-views"), "alice")
print(json.dumps({
    "chatroom_search": chatroom_search,
    "native_views": native_views,
    "frontend": frontend_manifest.manifest(),
    "home": home_page(shell.ROOT).decode(),
    "hooks": {hook: [key for key, _ in portal_features.contributions(hook)]
              for hook in ("web.routes", "web.lifespan", "watchboard.application", "isa.instructions",
                           "web.instructions", "web.native_view", "web.home_panel", "web.navigation",
                           "workbench.builtin", "application.targets")},
    "applications": {name: known_application(name) for name in ("tau", "notice_classifier", "resident_summary")},
    "lineage_tables": sorted(state_store.POSTGRES_LINEAGE_DATA_COLUMNS),
    "paths": sorted({route.path for route in shell._portal_route_definitions()}),
    "instructions": sorted(instruction_catalog()),
    "web_handlers": sorted(kernel.binding.handlers),
    "categories": sorted(categories()),
    "loaded": sorted(name for name in sys.modules if left_out(name)),
    "watchboard_applications": len(watchboards.applications()),
}))
"""
# The instructions the installed features add (``isa.instructions``).
FEATURE_INSTRUCTIONS = {
    "ListPosts", "GetPost", "CreatePost", "UpdatePost", "DeletePost", "CreateReply", "DeleteReply", "PinPost",
    "FeaturePost", "MarkPostRead", "ListChatrooms", "GetChatroom", "CreateChatroom", "UpdateChatroom",
    "LeaveChatroom", "DeleteChatroom", "ListChatroomMessages",
    "UpdateChatroomMessage", "RetractChatroomMessage", "UpdateChatroomReaction", "CreateChatroomMessage",
    "MarkChatroomRead", "GetPulse", "PublishPulseItem", "BindTelepusContactForUser",
    "UnbindTelepusContactForUser", "GetTelepusStatus", "SetTelepusEnabled",
    "GetResearchMap", "ApplyResearchMapChanges", "ListResearchMapChanges", "UpdateResearchMapLayout",
}
from management_portal.permission_system.scheme import repository
from management_portal.permission_system.scheme.compiler import SchemeError, compile_scheme


@pytest.fixture
def features(monkeypatch):
    """Replace the declared features for one test."""
    def declare(*declared):
        monkeypatch.setattr(portal_features, "FEATURES", declared)
        portal_features.contributions.cache_clear()

    yield declare
    portal_features.contributions.cache_clear()


def test_every_declared_part_is_installed_in_the_full_package():
    """完整的 Portal 中每个功能登记的接入点都能加载

    :module: platform.core
    :design: decision_15aab18c0ab3
    """
    for feature in portal_features.FEATURES:
        for hook, reference in feature.contributions.items():
            assert portal_features._load(reference) is not None, (feature.key, hook, reference)


def test_a_feature_left_out_contributes_nothing_and_a_broken_one_fails(features, tmp_path, monkeypatch):
    """没有打包的功能不提供接入点；已打包但缺依赖的功能照常报错

    :module: platform.core
    :design: decision_15aab18c0ab3
    """
    (tmp_path / "feature_probe_broken.py").write_text("import feature_probe_missing_dependency\npart = 1\n")
    (tmp_path / "feature_probe_present.py").write_text("class Holder:\n    part = 2\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    features(
        portal_features.PortalFeature("absent", "Absent", {"probe": "management_portal.feature_probe_absent:part"}),
        portal_features.PortalFeature("present", "Present", {"probe": "feature_probe_present:Holder.part"}),
    )
    assert portal_features.contributions("probe") == (("present", 2),)
    assert portal_features.contribution("other") is None

    features(portal_features.PortalFeature("broken", "Broken", {"probe": "feature_probe_broken:part"}))
    with pytest.raises(ModuleNotFoundError, match="feature_probe_missing_dependency"):
        portal_features.contributions("probe")
    sys.modules.pop("feature_probe_broken", None)


def test_web_starts_without_any_feature():
    """不打包任何功能时 Web 照常导入：没有功能路由、指令、后台任务、应用目标和 Watchboard 应用，也不加载任何功能模块，Lineage schema 的表不变

    :module: platform.core
    :design: decision_15aab18c0ab3
    """
    result = subprocess.run([sys.executable, "-c", WEB_WITHOUT_FEATURES], cwd=REPO, capture_output=True,
                            text=True, timeout=120, env={**os.environ, "PYTHONPATH": str(REPO)})
    assert result.returncode == 0, result.stderr[-4000:]
    web = json.loads(result.stdout.strip().splitlines()[-1])
    assert web["hooks"] == {"web.routes": [], "web.lifespan": [], "watchboard.application": [],
                            "isa.instructions": [], "web.instructions": [], "web.native_view": [],
                            "web.home_panel": [], "web.navigation": [], "workbench.builtin": [],
                            "application.targets": []}
    assert web["loaded"] == [] and web["watchboard_applications"] == 0
    # Feature applications are unknown; the Lineage schema holds the same core tables either way.
    assert web["applications"] == {"tau": False, "notice_classifier": False, "resident_summary": True}
    from management_portal.society_service import state_store
    assert web["lineage_tables"] == sorted(state_store.POSTGRES_LINEAGE_DATA_COLUMNS)
    # The development projection page offers no WALL or Test view.
    assert web["native_views"] == [{"views": []}]
    # Home carries its own panels and no Notice Board.
    assert 'data-home-component="daily-news"' in web["home"]
    assert "notice-board" not in web["home"] and "notice_board_home" not in web["home"]
    # Pages list no feature in the navigation and only the core built-in workbenches.
    from management_common.distribution_features import included_features
    assert web["frontend"] == {"navigation": [], "distribution_features": included_features(), "workbenches": {
        "development": {"title": "Portal 开发投影", "path": "/development", "initialize": False},
        "studio": {"title": "投影工坊", "path": "/workbenches?panel=studio", "initialize": False}}}
    # The Web kernel builds: no feature instruction is declared, so none lacks a handler.
    assert not FEATURE_INSTRUCTIONS & (set(web["instructions"]) | set(web["web_handlers"]))
    assert {"GetMailbox", "ListUsers", "ListInstructions"} <= set(web["instructions"])
    assert not {"discussion", "pulse", "research"} & set(web["categories"])
    # SearchMessages with chatroom_id runs at Web; without Chatrooms Web refuses it.
    assert web["chatroom_search"] == "instruction.invalid_arguments"
    feature_paths = ("/paper", "/projects", "/api/articles", "/resource/article", "/api/repos", "/resource/repo",
                     "/api/resource-backup", "/resource/backup", "/api/wall/", "/static/wall.", "/static/test_",
                     "/test/api/", "/api/notice-board/")
    assert not [path for path in web["paths"] if path.startswith(feature_paths)]
    assert {"/", "/api/health", "/lineage", "/settings"} <= set(web["paths"])


def test_installed_features_serve_their_routes_before_the_shell_fallbacks():
    """完整的 Portal 中各功能的路由都在外壳兜底路由之前

    :module: platform.core
    :design: decision_15aab18c0ab3
    """
    from management_portal import serve_management_portal as shell

    routes = shell._portal_route_definitions()
    contributed = {key for key, _ in portal_features.contributions("web.routes")}
    assert contributed == {"paper", "projects", "article", "repo", "backup", "wall", "test_framework", "notice_board"}
    assert "robolab" not in contributed
    fallbacks = [index for index, route in enumerate(routes) if route.group == "portal-fallback"]
    paths = [route.path for route in routes]
    for path in ("/paper/api/library", "/api/articles", "/api/wall/", "/test/api/run",
                 "/api/resource-backup/overview", "/api/notice-board/image", "/static/test_workbench.js"):
        assert paths.index(path) < min(fallbacks), path


def test_development_page_lists_the_installed_native_views(monkeypatch):
    """开发投影页列出已安装功能登记的原生视图，并按视图编号返回各自的模板

    :module: platform.core
    :design: decision_15aab18c0ab3
    """
    from urllib.parse import urlparse
    from management_portal import serve_management_portal as shell

    pages = shell._PORTAL_PAGES_HTTP
    replies = []
    monkeypatch.setattr(pages, "respond", lambda _request, status, payload: replies.append((status, payload)))
    for query in ("", "view=wall", "view=test", "view=missing"):
        pages.development_native_views(None, urlparse("/development/native-views?" + query), "alice")
    listed, wall, test, missing = replies
    assert listed == (200, {"views": [
        {"id": "wall", "label": "WALL", "icon": "window", "title": "设计、改进与软件演化"},
        {"id": "test", "label": "Test", "icon": "shield-check", "title": "测试目录、运行、TAU 与审查"},
    ]})
    assert '<template id="wallNativeView"' in wall[1]["html"] and "testNativeView" not in wall[1]["html"]
    assert '<template id="testNativeView">' in test[1]["html"] and "wallNativeView" not in test[1]["html"]
    assert missing[0] == 404
    # The views name the chat workspace as a script group; the reply lists its scripts.
    import re
    from management_portal import script_groups

    for view, (_status, payload) in (("wall", wall), ("test", test)):
        assets = re.search(rf'id="{view}NativeViewAssets"[^>]*>(.*?)</script>', payload["html"], re.S)
        scripts = json.loads(assets[1])["scripts"]
        chat = list(script_groups.scripts("chat-workspace"))
        start = scripts.index(chat[0])
        assert scripts[start:start + len(chat)] == chat, view


def test_pages_carry_the_installed_features_navigation_and_workbenches():
    """完整的 Portal 页面随文档带上功能的导航入口和内置工作台，只有带站点导航的页面才带

    :module: platform.core
    :design: decision_15aab18c0ab3
    """
    from management_portal import frontend_manifest

    manifest = frontend_manifest.manifest()
    assert [(item["id"], item.get("order"), item.get("section")) for item in manifest["navigation"]] == [
        ("paper", 40, None), ("projects", 20, None), ("backup", None, "resource")]
    assert {name: (item["path"], item["initialize"]) for name, item in manifest["workbenches"].items()} == {
        "development": ("/development", False), "studio": ("/workbenches?panel=studio", False),
        "paper": ("/paper", True), "wall": ("/development?view=wall", True), "test": ("/development?view=test", True)}

    page = b'<html><head>\n<script src="/static/portal_nav.js"></script></head></html>'
    injected = frontend_manifest.inject(page)
    script = injected[len(b"<html><head>"):injected.index(b"</script>") + len(b"</script>")]
    assert script.startswith(b"<script>window.__PORTAL_FRONTEND__=Object.freeze(")
    assert json.loads(script[len(b"<script>window.__PORTAL_FRONTEND__=Object.freeze("):-len(b");</script>")]) == manifest
    assert frontend_manifest.inject(injected) == injected
    assert frontend_manifest.inject(b"<html><head></head><body>preview</body></html>").count(b"__PORTAL_FRONTEND__") == 0


def test_home_carries_the_installed_features_panels_before_its_script():
    """完整的 Portal 首页在首页脚本之前带上功能的面板片段（公告板的标记、样式和脚本）

    :module: platform.core
    :design: decision_15aab18c0ab3
    """
    from management_portal import serve_management_portal as shell
    from management_portal.http_portal_pages import home_page

    assert [key for key, _ in portal_features.contributions("web.home_panel")] == ["notice_board"]
    page = home_page(shell.ROOT).decode()
    home_script = page.index('<script src="/static/home.js"></script>')
    for marker in ('href="/static/notice_board_home.css"', 'data-home-component="notice-board"',
                   'id="noticeBoardComposeOverlay"', '<script src="/static/notice_board_home.js"></script>'):
        assert page.count(marker) == 1 and page.index(marker) < home_script, marker
    # The panel registers before Home composes its groups.
    assert page.index('<script src="/static/portal_home_components.js"></script>') < page.index(
        '<script src="/static/notice_board_home.js"></script>')


def test_installed_features_add_their_instructions_to_the_catalog_and_web_kernel():
    """完整的 Portal 中功能的指令进入指令目录和 Web 内核，类别随功能登记

    :module: platform.core
    :design: decision_15aab18c0ab3, decision_898b8911a2ff
    """
    from management_portal import web_isa
    from management_portal.hatg.catalog import categories, instruction_catalog

    for hook in ("isa.instructions", "web.instructions"):
        assert {key for key, _ in portal_features.contributions(hook)} == {"projects", "notice_board", "chatroom", "pulse", "telepus"}
    catalog = instruction_catalog()
    assert FEATURE_INSTRUCTIONS <= set(catalog)
    assert FEATURE_INSTRUCTIONS <= set(web_isa.binding().handlers)
    assert {catalog[name].owner for name in FEATURE_INSTRUCTIONS} == {"web"}
    assert categories()["discussion"] == "公共讨论" and categories()["pulse"] == "Pulse"
    assert categories()["research"] == "研究地图"
    category = next(parameter for parameter in catalog["ListInstructions"].parameters if parameter.name == "category")
    assert set(category.enum) == set(categories())


def test_telepus_management_access_flags_follow_feature_installation(features, monkeypatch):
    """The management switch appears for an installed Telepus and disappears with the feature."""
    from types import SimpleNamespace

    from management_portal import permission_system
    from management_portal.http_portal_session import PortalSessionHttp

    monkeypatch.setattr(permission_system, "allows", lambda _actor, _name, _args=None: True)
    monkeypatch.setattr(permission_system, "user_category", lambda _actor: "portal_admin")
    monkeypatch.setattr(permission_system, "mode", lambda: "open")
    session = PortalSessionHttp(
        respond=lambda _request, _status, payload: payload, exception_response=None, read_json=None,
        session_actor=lambda _request: "alice", access=lambda: SimpleNamespace(user=lambda _actor: {"status": "active"}),
        avatar=lambda _actor: "", instance="dev", build_id="test",
        preferences=lambda _actor: {}, set_preferences=None, conversation_payload=None, workspace_adapter=None,
    )
    installed = session.me(None, None, None)["access"]["allows"]
    assert installed["GetTelepusStatus"] and installed["SetTelepusEnabled"]
    features()
    absent = session.me(None, None, None)["access"]["allows"]
    assert "GetTelepusStatus" not in absent and "SetTelepusEnabled" not in absent


def test_mailbox_without_feature_sources_keeps_resident_conversations(features):
    """没有 Chatroom 与公告板时 Mailbox 只含 Resident 对话，分区与未读照常

    :module: workbench.notifications
    :design: decision_15f7df737ed5
    """
    features()
    assert mailbox_sources.entries("alice") == []
    assert index.section_of({"key": "resident:resident_111111111111", "kind": "resident"}) == "conversation"
    assert index.section_of_key("chatroom:room") == "notification"
    rows = {"rows": {"resident:resident_111111111111": {"unread": True, "pinned": False, "read_revision": 2,
                                                         "dismissed_through": ""}}}
    decorated = index.decorate_entries([{"key": "resident:resident_111111111111", "kind": "resident",
                                         "updated_at": "2026-10-18T00:00:00Z", "unread": False}], rows)
    assert decorated["entries"][0]["unread"] is True


def test_installed_features_add_their_mailbox_sources():
    """Chatroom 是对话分区、自己记未读；公告板提及是通知

    :module: workbench.notifications
    :design: decision_15f7df737ed5
    """
    assert {source.prefix for source in mailbox_sources.sources()} == {"chatroom:", "notice-board:"}
    assert mailbox_sources.conversation_prefixes() == ("chatroom:",)
    assert mailbox_sources.own_read_state_prefixes() == ("chatroom:",)
    assert index.section_of_key("chatroom:room") == "conversation"
    assert index.section_of_key("notice-board:notice_0123456789abcdef") == "notification"


def test_schemes_skip_instructions_of_features_left_out(monkeypatch, caplog):
    """内置方案跳过未安装功能的指令的 A 元素并告警，新写入的方案仍拒绝不存在的 A 元素

    :module: security.identity-access
    :design: decision_b820faf42218
    """
    document = repository.builtin_documents()["default"]
    left_out = {name for name in document["instructions"] if "Chatroom" in name}
    assert left_out
    installed = {name: decides for name, decides in repository.declared_decisions().items() if "Chatroom" not in name}
    monkeypatch.setattr(repository, "declared_decisions", lambda: installed)
    monkeypatch.setattr(repository, "_REPORTED", set())
    with pytest.raises(SchemeError, match="A 元素不存在"):
        compile_scheme(document, instructions=installed)

    schemes = repository.Schemes(store=None)
    with caplog.at_level(logging.WARNING, logger=repository.__name__):
        compiled = schemes.compiled("default")
    assert not left_out & set(compiled.instructions)
    assert set(compiled.instructions) == set(installed) and compiled.unmapped() == []
    assert "skipped" in caplog.text
    rules = schemes.get("default")["rules"]
    assert not left_out & set(rules["instructions"])
    schemes.compile({**rules, "scheme_id": "copy_of_default"})
    with pytest.raises(SchemeError, match="A 元素不存在"):
        schemes.compile({**document, "scheme_id": "copy_of_default"})
