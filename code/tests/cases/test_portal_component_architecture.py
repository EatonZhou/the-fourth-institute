from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "management_portal" / "static"

WORKBENCH_HOSTS = (
    ROOT / "management_portal" / "article_workspace.html",
    ROOT / "management_portal" / "resource_catalog.html",
    ROOT / "management_portal" / "lineage.html",
    ROOT / "management_portal" / "repo_workspace.html",
    ROOT / "management_portal" / "paper.html",
    ROOT / "knowledge_board_lan" / "knowledge_board.html",
    ROOT / "management_portal" / "projects.html",
    ROOT / "management_portal" / "test_demo_components.html",
    ROOT / "management_portal" / "workbenches.html",
)

SHARED_INTERNAL_STYLE_OWNERS = {
    "hatg_panel.css",
    "lineage_function_blocks.css",
    "portal_board_toolbar.css",
    "portal_collection_board.css",
    "portal_workbench.css",
    "portal_components.css",
    "portal_design_system.css",
    "portal_image_editor.css",
    "portal_live_tour.css",
    "portal_section_nav.css",
    "portal_themes.css",
    "resource_backup.css",
    "resource_tabs.css",
}

MIGRATED_ESCAPE_HELPERS = (
    "portal_chat_workspace.js",
    "portal_workbench.js",
    "portal_resource_chips.js",
    "portal_workspace_surface.js",
    "portal_preview_runtime.js",
    "portal_file_tree.js",
    "resource_backup.js",
    "project_common.js",
)

# Modules that take elementOf and staticAssetUrl from PortalUIPrimitives instead of their own copies.
PRIMITIVE_DOM_HELPERS = (
    "portal_overlay.js",
    "portal_ui.js",
    "portal_form_controls.js",
    "portal_detail_surface.js",
    "portal_resource_header.js",
    "portal_board_shell.js",
    "portal_resource_detail.js",
    "portal_demo_player.js",
    "portal_nav.js",
    "settings.js",
    "portal_chat_workspace.js",
)

# Modules that load Portal scripts and stylesheets lazily through
# PortalUIPrimitives.loadAsset, so one file gets one tag on a page.
LAZY_ASSET_LOADERS = (
    "portal_hatg_loader.js",
    "portal_nav.js",
    "settings.js",
    "chat_workspace_message_content.js",
    "chat_workspace_messages.js",
)

# Resource pages that read record fields with PortalUIPrimitives.objectOf and
# send ResourceService requests through their recovery's guard.
RESOURCE_SERVICE_PAGES = (
    "repo_workspace.js",
    "resource_catalog.js",
)

MIGRATED_DATE_HELPERS = (
    "portal_board_shell.js",
    "resource_catalog.js",
    "article_workspace.js",
    "project_common.js",
    "hatg_panel.js",
    "repo_workspace.js",
    "portal_agent_inbox.js",
    "settings.js",
)

PROTECTED_INTERNAL = re.compile(
    r"(?:^|[>+~\s])\.pane(?:Head(?:Text|Actions)?|Title)(?:[\s.#:>+~,{]|$)"
    r"|"
    r"[>+~\s]\.portal(?:"
    r"Workbench(?:Stage|Canvas|Split|SplitChild|Stack|StackBody|PanelFrame|Tabs|Tab|TabSlot)"
    r"|PanelHeadSurface"
    r"|Workspace(?:Head|Body|Identity|Actions|Surface|Preview|Terminal)"
    r"|Chat(?:Workspace|Message|Composer|Runtime|Refs|Forward|Queue)"
    r"|ResourceIdentity(?:TitleRow|Title|Separator|Type|Id)"
    r"|Preview(?:Toolbar|Body|Frame|Content|Status|Image|Code)"
    r"|FileTree(?:Node|Row|Children|Label|Icon)"
    r"|Toolbar(?:Group|Button|Action|Spacer)"
    r"|Dialog(?:Header|Body|Footer|Title|Actions)"
    r"|Toast(?:Title|Message|Actions)"
    r"|Detail(?:Header|Body|Footer)|ActivityRailView)"
    r"|[>+~\s]\.hatg(?:Toolbar|Viewport|Canvas|World|Nodes|Edges|Node|Port|History|Inspector)"
    r"(?:[\s.#:>+~,{]|$)"
)

NAV_INTERNAL = re.compile(
    r"(?:^|[>+~\s])\.portal(?:"
    r"Nav(?:Shell|Top|Main|OverflowToggle)|Brand|Module(?:Nav|Link)|User"
    r")(?:[\s.#:>+~,{]|$)"
)

RESOURCE_SECTION_INTERNAL = re.compile(
    r"(?:^|[>+~\s])(?:\.resourceSectionPageHeader|\.resourceSectionFrame|\.resourceCategory(?:Shell|Viewport|Row|Tabs|Link|Extra)|\[data-resource-section-header\])"
    r"(?:[\s.#:>+~,{]|$)"
)

RESOURCE_SECTION_HOSTS = (
    ROOT / "management_portal" / "resource_catalog.html",
    ROOT / "management_portal" / "repo_workspace.html",
    ROOT / "management_portal" / "article_workspace.html",
    ROOT / "management_portal" / "resource_backup.html",
    ROOT / "knowledge_board_lan" / "knowledge_board.html",
)

SITE_HEADER_HOSTS = (
    ROOT / "management_portal" / "index.html",
    ROOT / "management_portal" / "lineage.html",
    ROOT / "management_portal" / "workbenches.html",
    ROOT / "management_portal" / "paper.html",
    ROOT / "management_portal" / "settings.html",
    ROOT / "management_portal" / "portal_management.html",
    ROOT / "management_portal" / "principal_backpack.html",
    *RESOURCE_SECTION_HOSTS,
)

RESOURCE_PAGE_STYLES = {
    "resource_catalog.html": "resource_catalog.css",
    "repo_workspace.html": "repo_workspace.css",
    "article_workspace.html": "article_workspace.css",
    "resource_backup.html": "resource_backup.css",
}

LEGACY_THEME_VARIABLE = re.compile(
    r"var\(\s*--(?:"
    r"line|surface|surface-2|surface-subtle|surface-hover|ink|hover|hover-bg|input-bg|"
    r"panel-soft|accent-soft|accent-ink|accent-contrast|"
    r"portal-bg|portal-surface|portal-surface-subtle|portal-surface-muted|"
    r"portal-surface-raised|portal-surface-sunken|portal-text|portal-text-muted|"
    r"portal-border|portal-border-subtle|portal-border-strong|portal-accent"
    r")\s*(?:,|\))"
)


def _protected_selectors(source: str) -> list[tuple[int, str]]:
    clean = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    matches: list[tuple[int, str]] = []
    for match in re.finditer(r"([^{}]+)\{", clean):
        selector = match.group(1).strip()
        if selector.startswith("@") or not PROTECTED_INTERNAL.search(selector):
            continue
        matches.append((clean.count("\n", 0, match.start()) + 1, " ".join(selector.split())))
    return matches


def _navigation_selectors(source: str) -> list[tuple[int, str]]:
    clean = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    matches: list[tuple[int, str]] = []
    for match in re.finditer(r"([^{}]+)\{", clean):
        selector = match.group(1).strip()
        if selector.startswith("@") or not NAV_INTERNAL.search(selector):
            continue
        matches.append((clean.count("\n", 0, match.start()) + 1, " ".join(selector.split())))
    return matches


def _resource_section_selectors(source: str) -> list[tuple[int, str]]:
    clean = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    matches: list[tuple[int, str]] = []
    for match in re.finditer(r"([^{}]+)\{", clean):
        selector = match.group(1).strip()
        if selector.startswith("@") or not RESOURCE_SECTION_INTERNAL.search(selector):
            continue
        matches.append((clean.count("\n", 0, match.start()) + 1, " ".join(selector.split())))
    return matches


def test_portal_theme_contract_has_one_provider_and_no_parallel_dialect_contracts(subtests, isolated_portal_case):
    """主题变量只由 portal_themes.css 提供，共享 UI 辅助函数和面板注册各有唯一来源并按序加载"""
    with subtests.test(contract='test_portal_theme_contract_has_one_provider_and_no_parallel_dialect'), isolated_portal_case() as case_path:
        themes = (STATIC / 'portal_themes.css').read_text(encoding='utf-8')
        theme_blocks = 9
        for token in ('--bg', '--panel', '--text', '--border', '--accent', '--on-accent', '--danger', '--on-danger'):
            assert themes.count(f'{token}:') >= theme_blocks, token
        sources = [*sorted((ROOT / 'management_portal').glob('*.html')), *sorted(STATIC.glob('*.css')), *sorted(STATIC.glob('*.js')), *sorted((ROOT / 'management_portal' / 'test_framework' / 'page').glob('*.css')), *sorted((ROOT / 'management_portal' / 'test_framework' / 'page').glob('*.js'))]
        violations: list[str] = []
        for path in sources:
            if path.name == 'portal_themes.css':
                continue
            source = path.read_text(encoding='utf-8')
            for match in LEGACY_THEME_VARIABLE.finditer(source):
                line = source.count('\n', 0, match.start()) + 1
                violations.append(f'{path.relative_to(ROOT)}:{line}: {match.group(0)}')
        assert not violations, 'parallel theme variables:\n' + '\n'.join(violations)
    with subtests.test(contract='test_workbench_hosts_load_shared_contract_owners_before_workbench'), isolated_portal_case() as case_path:
        for path in WORKBENCH_HOSTS:
            source = path.read_text(encoding='utf-8')
            editor_open = source.index('portal_editor_open_service.js')
            registry = source.index('portal_command_registry.js')
            context = source.index('portal_workbench_context.js')
            workbench = source.index('portal_workbench.js')
            assert editor_open < workbench, path
            assert registry < workbench, path
            assert context < workbench, path
    with subtests.test(contract='test_core_ui_helpers_delegate_to_their_shared_owners'), isolated_portal_case() as case_path:
        primitives = (STATIC / 'portal_ui_primitives.js').read_text(encoding='utf-8')
        assert 'global.PortalUIPrimitives = api' in primitives
        escape_entities = ('&amp;', '&lt;', '&gt;', '&quot;', '&#39;')
        for owner in MIGRATED_ESCAPE_HELPERS:
            source = (STATIC / owner).read_text(encoding='utf-8')
            assert 'PortalUIPrimitives.escapeHtml' in source, owner
            assert not any((entity in source for entity in escape_entities)), owner
        for owner in PRIMITIVE_DOM_HELPERS:
            source = (STATIC / owner).read_text(encoding='utf-8')
            assert 'PortalUIPrimitives.' in source, owner
            assert 'document.getElementById(ref) || document.querySelector(ref)' not in source, owner
            assert 'searchParams.get("portal_v")' not in source, owner
        for owner in LAZY_ASSET_LOADERS:
            source = (STATIC / owner).read_text(encoding='utf-8')
            assert 'PortalUIPrimitives.loadAsset' in source or '{ loadAsset } = window.PortalUIPrimitives' in source, owner
            assert 'portalLoaded' not in source, owner
            assert 'createElement(css ? "link" : "script")' not in source, owner
        for owner in RESOURCE_SERVICE_PAGES:
            source = (STATIC / owner).read_text(encoding='utf-8')
            assert 'PortalUIPrimitives.objectOf' in source, owner
            assert 'resourceServiceRecovery.guard(' in source, owner
            for copy in ('function attributesOf', 'function sourceOf', 'function getJson', 'function observeResourceServiceFailure'):
                assert copy not in source, (owner, copy)
        for owner in MIGRATED_DATE_HELPERS:
            source = (STATIC / owner).read_text(encoding='utf-8')
            assert 'PortalUI.formatDateTime' in source, owner
    with subtests.test(contract='test_migrated_ui_helpers_load_after_their_shared_owners'), isolated_portal_case() as case_path:
        html_paths = [*sorted((ROOT / 'management_portal').glob('*.html')), *sorted(ROOT.glob('*_lan/*.html'))]
        for path in html_paths:
            source = path.read_text(encoding='utf-8')
            for owner in MIGRATED_ESCAPE_HELPERS:
                if owner not in source:
                    continue
                assert 'portal_ui_primitives.js' in source, (path, owner)
                assert source.index('portal_ui_primitives.js') < source.index(owner), (path, owner)
            if 'portal_nav.js' in source:
                assert source.index('portal_ui_primitives.js') < source.index('portal_nav.js'), path
            # These call PortalUIPrimitives only after the page has loaded, so it may come later.
            for owner in (*PRIMITIVE_DOM_HELPERS, *LAZY_ASSET_LOADERS):
                if owner in source:
                    assert 'portal_ui_primitives.js' in source, (path, owner)
            for owner in MIGRATED_DATE_HELPERS:
                if owner not in source:
                    continue
                assert 'portal_ui.js' in source, (path, owner)
                assert source.index('portal_ui.js') < source.index(owner), (path, owner)
    with subtests.test(contract='test_command_palette_hosts_load_the_command_registry_first'), isolated_portal_case() as case_path:
        html_paths = [*sorted((ROOT / 'management_portal').glob('*.html')), *sorted(ROOT.glob('*_lan/*.html'))]
        for path in html_paths:
            source = path.read_text(encoding='utf-8')
            if 'portal_command_palette.js' not in source:
                continue
            assert 'portal_command_registry.js' in source, path
            assert source.index('portal_command_registry.js') < source.index('portal_command_palette.js'), path
    with subtests.test(contract='test_panel_packages_are_the_only_panel_registration_protocol'), isolated_portal_case() as case_path:
        sources = '\n'.join((path.read_text(encoding='utf-8') for path in STATIC.glob('*.js')))
        assert 'registerPanelType' not in sources
        assert 'registeredPanelTypes' not in sources
        for owner in ('portal_navigation.js', 'portal_hatg_loader.js', 'portal_workspace_surface.js', 'portal_chat_workspace.js'):
            source = (STATIC / owner).read_text(encoding='utf-8')
            assert 'registerPanelPackage' in source, owner
            for field in ('version:', 'presentation:', 'sizing:', 'instances:', 'capabilities:', 'behavior:', 'context:', 'persistence:', 'create'):
                assert field in source, f'{owner} misses {field}'
            assert 'registerPanelPackage?.' not in source, owner


def test_page_styles_do_not_reach_into_shared_component_internals_contracts(subtests, isolated_portal_case):
    """页面样式不触及共享组件内部选择器"""
    with subtests.test(contract='test_page_styles_do_not_reach_into_shared_component_internals'), isolated_portal_case() as case_path:
        violations: list[str] = []
        page_styles = [path for path in STATIC.glob('*.css') if path.name not in SHARED_INTERNAL_STYLE_OWNERS]
        for path in page_styles:
            source = path.read_text(encoding='utf-8')
            for line_number, selector in _protected_selectors(source):
                violations.append(f'{path.relative_to(ROOT)}:{line_number}: {selector}')
        html_paths = [*sorted((ROOT / 'management_portal').glob('*.html')), *sorted(ROOT.glob('*_lan/*.html'))]
        for path in html_paths:
            source = path.read_text(encoding='utf-8')
            for block in re.findall('<style[^>]*>(.*?)</style>', source, flags=re.I | re.S):
                for line_number, selector in _protected_selectors(block):
                    violations.append(f'{path.relative_to(ROOT)}:<style>:{line_number}: {selector}')
        assert not violations, 'Page CSS crosses shared component boundaries:\n' + '\n'.join(violations)


def test_global_navigation_has_one_shared_host_and_style_owner():
    """全局导航只由共享站点头渲染和定义样式，页面 CSS 不改写导航内部"""
    violations: list[str] = []
    page_styles = [path for path in STATIC.glob('*.css') if path.name != 'portal_components.css']
    for path in page_styles:
        for line_number, selector in _navigation_selectors(path.read_text(encoding='utf-8')):
            violations.append(f'{path.relative_to(ROOT)}:{line_number}: {selector}')

    html_paths = [*sorted((ROOT / 'management_portal').glob('*.html')), *sorted(ROOT.glob('*_lan/*.html'))]
    for path in html_paths:
        source = path.read_text(encoding='utf-8')
        for block in re.findall('<style[^>]*>(.*?)</style>', source, flags=re.I | re.S):
            for line_number, selector in _navigation_selectors(block):
                violations.append(f'{path.relative_to(ROOT)}:<style>:{line_number}: {selector}')
    for path in SITE_HEADER_HOSTS:
        source = path.read_text(encoding='utf-8')
        assert source.count('data-portal-site-header') == 1, path
        assert 'data-portal-nav-active=' in source, path
        assert 'portalNavShell' not in source, path
        assert not re.search(r'\sdata-portal-nav(?:\s|=|>)', source), path
        body_offset = source.index('<body')
        body_tag = source[body_offset:source.find('>', body_offset) + 1]
        assert 'portalBoardShell' in body_tag, f'{path.relative_to(ROOT)} does not load shared shell styles'

    nav_js = (STATIC / 'portal_nav.js').read_text(encoding='utf-8')
    assert 'function renderSiteHeader(root)' in nav_js
    assert 'function mountSiteHeaders(scope = document)' in nav_js
    assert 'PortalUIPrimitives.registerEnhancer' in nav_js
    assert 'id: "site-headers", selector: "[data-portal-site-header]"' in nav_js
    assert nav_js.index('function renderSiteHeader(root)') < nav_js.index('document.querySelectorAll("[data-portal-nav]").forEach(render);')
    assert '<div class="portalNavShell">' in nav_js
    assert '<div class="portalUser"></div>' in nav_js

    assert not violations, 'Page CSS restyles shared navigation internals:\n' + '\n'.join(violations)


def test_live_demonstrations_load_on_demand():
    """页面和生产组件不带 Live 演示代码：控制器只在有演示会话时加载，页面适配器按需加载

    :module: workbench.shell
    :design: decision_f2a6a5dd4646
    """
    pages = [*sorted((ROOT / 'management_portal').rglob('*.html')), *sorted(ROOT.glob('*_lan/*.html'))]
    for path in (path for path in pages if 'node_modules' not in path.parts):
        source = path.read_text(encoding='utf-8')
        for asset in ('portal_live_tour.js', 'portal_design_live_'):
            assert asset not in source, (path.relative_to(ROOT), asset)
        assert 'DemoAdapter' not in source and 'demoAdapter' not in source, path.relative_to(ROOT)
    for name in ('portal_nav.js', 'portal_command_palette.js'):
        assert 'demoAdapter' not in (STATIC / name).read_text(encoding='utf-8'), name
    # A tour session brings in the controller; starting one from Test loads it too.
    state = (STATIC / 'portal_state.js').read_text(encoding='utf-8')
    assert 'loadAsset("/static/portal_live_tour.js"' in state
    assert 'loadAsset("/static/portal_live_tour.js"' in (STATIC / 'portal_demo_player.js').read_text(encoding='utf-8')

    # Every adapter a Live definition names loads from the tour's list, except
    # Test's own (test-lab), which comes with the Test view.
    tour = (STATIC / 'portal_live_tour.js').read_text(encoding='utf-8')
    assets = dict(re.findall(r'^\s+"([a-z-]+)": "(/static/[a-z_]+\.js)",$', tour, flags=re.M))
    for adapter, asset in assets.items():
        source = (STATIC / asset.removeprefix('/static/')).read_text(encoding='utf-8')
        assert 'PortalDesignLiveAdapters' in source and f'"{adapter}"' in source, adapter
    named = set()
    for path in [ROOT / 'tests/cases/live_cases.json', *sorted((ROOT / 'tests/cases/design_live').glob('*.json'))]:
        named.update(re.findall(r'"adapter": "([a-z-]+)"', path.read_text(encoding='utf-8')))
    assert named and named - {'test-lab'} <= set(assets), sorted(named - set(assets))


def test_resource_sections_share_one_header_and_style_owner():
    """资源分区页共用同一页头和样式来源，样式表按固定顺序加载"""
    for path in RESOURCE_SECTION_HOSTS:
        source = path.read_text(encoding="utf-8")
        # The shared page frame owns the section header's outer gutter.
        assert re.search(r'class="[^"]*\bresourceSectionFrame\b[^"]*\bportalPageHeaderFrame\b', source), path
        assert 'resourceSectionPageHeader' in source, path
        assert 'data-resource-section-header' in source, path
        assert 'resource_tabs.css' in source, path
        assert 'portalCollectionHeader' not in source, path

        styles = re.findall(r'<link[^>]+href="([^"]+\.css)"', source)
        names = [Path(href).name for href in styles]
        components = names.index("portal_components.css")
        tabs = names.index("resource_tabs.css")
        design = names.index("portal_design_system.css")
        themes = names.index("portal_themes.css")
        toolbar = names.index("portal_board_toolbar.css")
        assert components < tabs < design < themes < toolbar, (path, names)
        for feature in ("portal_collection_board.css", "portal_workbench.css", "portal_image_editor.css"):
            if feature in names:
                assert components < names.index(feature) < tabs, (path, names)
        page_style = RESOURCE_PAGE_STYLES.get(path.name)
        if page_style:
            assert tabs < names.index(page_style) < design, (path, names)

    renderer = (ROOT / "management_portal" / "page_renderers.py").read_text(encoding="utf-8")
    assert 'data-resource-tabs data-resource-category="{category}"' in renderer
    assert 'data-resource-section-header' not in renderer

    knowledge = (ROOT / "knowledge_board_lan" / "knowledge_board.html").read_text(encoding="utf-8")
    assert 'knowledgeSearchModeHint' not in knowledge

    toolbar_css = (STATIC / "portal_board_toolbar.css").read_text(encoding="utf-8")
    tabs_css = (STATIC / "resource_tabs.css").read_text(encoding="utf-8")
    assert "header.resourceSectionPageHeader" in tabs_css
    assert ".portalBoardHeader *" in toolbar_css
    assert "box-sizing: border-box;" in toolbar_css
    assert "--portal-control-height: 38px;" in toolbar_css
    assert "--portal-control-height-compact: 38px;" in toolbar_css
    # One page gutter for every Portal page (portalPageFrame / portalPageHeaderFrame).
    components_css = (STATIC / "portal_components.css").read_text(encoding="utf-8")
    assert "--portal-page-gap:" in components_css
    assert ".portalPageHeaderFrame" in components_css

    catalog_css = (STATIC / "resource_catalog.css").read_text(encoding="utf-8")
    repo_css = (STATIC / "repo_workspace.css").read_text(encoding="utf-8")
    backup_css = (STATIC / "resource_backup.css").read_text(encoding="utf-8")
    for source in (tabs_css, catalog_css, repo_css, backup_css):
        assert "--portal-resource-page-gutter" not in source
    assert "portalCollectionHeader" not in catalog_css
    assert "portalCollectionHeader" not in repo_css

    violations: list[str] = []
    style_owners = {"resource_tabs.css", "portal_board_toolbar.css"}
    for path in STATIC.glob("*.css"):
        if path.name in style_owners:
            continue
        for line_number, selector in _resource_section_selectors(path.read_text(encoding="utf-8")):
            violations.append(f"{path.relative_to(ROOT)}:{line_number}: {selector}")
    for path in [*sorted((ROOT / "management_portal").glob("*.html")), *sorted(ROOT.glob("*_lan/*.html"))]:
        source = path.read_text(encoding="utf-8")
        for block in re.findall('<style[^>]*>(.*?)</style>', source, flags=re.I | re.S):
            for line_number, selector in _resource_section_selectors(block):
                violations.append(f"{path.relative_to(ROOT)}:<style>:{line_number}: {selector}")

    assert not violations, 'Page CSS restyles shared Resource section navigation:\n' + '\n'.join(violations)
