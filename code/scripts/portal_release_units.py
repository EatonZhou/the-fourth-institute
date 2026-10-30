"""Browser compatibility units: groups of files that reach Release together.

A browser runtime split across an HTML shell and several scripts works only
when all of its files come from one revision. A publication that selects any
member of a unit therefore publishes the whole unit; files of the unit that
did not change add nothing to the commit.
"""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Iterable

# A Lineage page is one browser runtime, even though its implementation is
# split across an HTML shell and lazily loaded modules. Publishing only one
# member can leave the Release DOM and JavaScript contracts at different
# revisions, so selecting any member captures the whole compatibility unit.
LINEAGE_WORKBENCH_RELEASE_UNIT = (
    'management_portal/lineage.html',
    'management_portal/hatg-panel.html',
    'management_portal/static/lineage_workbench.css',
    'management_portal/static/lineage_workbench.js',
    'management_portal/static/lineage_workbench_watchboard.js',
    'management_portal/static/lineage_workbench_state.js',
    'management_portal/static/lineage_workbench_controller.js',
    'management_portal/static/lineage_workbench_which_resident.js',
    'management_portal/static/lineage_workbench_chatrooms.js',
    'management_portal/static/lineage_workbench_resident_dialogs.js',
    'management_portal/static/lineage_workbench_preview_documents.js',
    'management_portal/static/lineage_workbench_preview_refresh.js',
    'management_portal/static/lineage_workbench_preview_rendering.js',
    'management_portal/static/lineage_workbench_preview_editing.js',
    'management_portal/static/lineage_workbench_preview_files.js',
    'management_portal/static/lineage_workbench_preview_links.js',
    'management_portal/static/lineage_workbench_preview_chat_links.js',
    'management_portal/static/lineage_workbench_preview.js',
    'management_portal/static/lineage_workbench_card_menus.js',
    'management_portal/static/lineage_workbench_sidebar_targets.js',
    'management_portal/static/lineage_workbench_sidebar_filters.js',
    'management_portal/static/lineage_workbench_sidebar_snapshot.js',
    'management_portal/static/lineage_workbench_sidebar_agent_list.js',
    'management_portal/static/lineage_workbench_sidebar_favorites.js',
    'management_portal/static/lineage_workbench_sidebar_reveal.js',
    'management_portal/static/lineage_workbench_sidebar.js',
    'management_portal/static/lineage_workbench_recent.js',
    'management_portal/static/lineage_workbench_location.js',
    'management_portal/static/lineage_workbench_chat_targets.js',
    'management_portal/static/lineage_workbench_selection.js',
    'management_portal/static/lineage_workbench_chat_detail.js',
    'management_portal/static/lineage_workbench_layout.js',
    'management_portal/static/lineage_workbench_editors.js',
    'management_portal/static/lineage_workbench_dialog_controls.js',
    'management_portal/static/portal_lineage_catalogs.js',
    'management_portal/static/portal_hatg_loader.js',
    'management_portal/static/portal_resident_menu_tabs.js',
    'management_portal/static/portal_resident_menu_resident.js',
    'management_portal/static/portal_resident_menu_transfer.js',
    'management_portal/static/portal_resident_menu_family.js',
    'management_portal/static/portal_resident_menu_group.js',
    'management_portal/static/portal_resident_menu_identity.js',
    'management_portal/static/portal_resident_menu_journal.js',
    'management_portal/static/portal_resident_menu_runtime.js',
    'management_portal/static/portal_resident_menu_events.js',
    'management_portal/static/portal_resident_menu.js',
    'management_portal/static/portal_resident_menu.css',
    'management_portal/static/portal_scope_menu.js',
    'management_portal/static/hatg_panel.css',
    'management_portal/static/hatg_panel_edges.css',
    'management_portal/static/hatg_panel_nodes.css',
    'management_portal/static/hatg_panel_navigation.css',
    'management_portal/static/hatg_panel_library.css',
    'management_portal/static/hatg_panel_inspector.css',
    'management_portal/static/hatg_panel_relations.css',
    'management_portal/static/hatg_panel_dialogs.css',
    'management_portal/static/hatg_panel_responsive.css',
    'management_portal/static/hatg_panel_effect_scripts.css',
    'management_portal/static/hatg_graph_model.js',
    'management_portal/static/hatg_lineage_state.js',
    'management_portal/static/hatg_program_edits.js',
    'management_portal/static/hatg_family_blocks.js',
    'management_portal/static/hatg_node_status.js',
    'management_portal/static/hatg_canvas_selection.js',
    'management_portal/static/hatg_function_block_menu.js',
    'management_portal/static/hatg_program_inspector_references.js',
    'management_portal/static/hatg_program_inspector_primitive_fields.js',
    'management_portal/static/hatg_program_inspector_delivery.js',
    'management_portal/static/hatg_program_inspector_observation.js',
    'management_portal/static/hatg_program_inspector_manual_events.js',
    'management_portal/static/hatg_program_inspector_saving.js',
    'management_portal/static/hatg_program_inspector_render.js',
    'management_portal/static/hatg_program_inspector.js',
    'management_portal/static/hatg_relations.js',
    'management_portal/static/hatg_layout.js',
    'management_portal/static/hatg_toolbar.js',
    'management_portal/static/hatg_graph_projection.js',
    'management_portal/static/hatg_graph_view.js',
    'management_portal/static/hatg_resident_runtime.js',
    'management_portal/static/hatg_workspace_results.js',
    'management_portal/static/hatg_resident_dialogs.js',
    'management_portal/static/hatg_lineage_dialogs.js',
    'management_portal/static/hatg_seeds.js',
    'management_portal/static/hatg_leader.js',
    'management_portal/static/hatg_graph_search.js',
    'management_portal/static/hatg_panel_lineage_loading.js',
    'management_portal/static/hatg_panel_template_shelf.js',
    'management_portal/static/hatg_panel_bindings.js',
    'management_portal/static/hatg_panel_mount.js',
    'management_portal/static/hatg_panel.js',
    'management_portal/static/hatg_panel_features.js',
    'management_portal/static/hatg_panel_state.js',
    'management_portal/static/lineage_program.js',
    'management_portal/static/lineage_primitive_definitions.json',
    'management_portal/static/lineage_primitive_inspector.js',
    'management_portal/static/lineage_invocation_inspector.js',
    'management_portal/static/lineage_element_fields.js',
    'management_portal/static/lineage_graph_layout.js',
    'management_portal/static/lineage_graph_viewport.js',
    'management_portal/static/lineage_graph_nodes.js',
    'management_portal/static/lineage_graph_element_index.js',
    'management_portal/static/lineage_graph_ports.js',
    'management_portal/static/lineage_graph_hover.js',
    'management_portal/static/lineage_graph_edge_routes.js',
    'management_portal/static/lineage_graph_edges.js',
    'management_portal/static/lineage_graph_minimap.js',
    'management_portal/static/lineage_graph_function_blocks.js',
    'management_portal/static/lineage_graph_camera.js',
    'management_portal/static/lineage_graph_selection.js',
    'management_portal/static/lineage_graph_wiring.js',
    'management_portal/static/lineage_graph_quick_fork.js',
    'management_portal/static/lineage_graph_drag.js',
    'management_portal/static/lineage_graph_cards.js',
    'management_portal/static/lineage_graph_canvas_events.js',
    'management_portal/static/lineage_graph_render.js',
    'management_portal/static/lineage_graph.js',
    'management_portal/static/lineage_templates_dialogs.js',
    'management_portal/static/lineage_templates_shelf.js',
    'management_portal/static/lineage_templates_resident_settings.js',
    'management_portal/static/lineage_templates_application.js',
    'management_portal/static/lineage_templates_editing.js',
    'management_portal/static/lineage_templates_editor_draft.js',
    'management_portal/static/lineage_templates_editor_pages.js',
    'management_portal/static/lineage_templates_editor_elements.js',
    'management_portal/static/lineage_templates_editor_inspector.js',
    'management_portal/static/lineage_templates_editor_actions.js',
    'management_portal/static/lineage_templates.js',
    'management_portal/static/lineage_function_blocks.css',
)

# The Chat entry script calls each Chat module's factory by name and hands it
# its dependencies, so the entry script and every module are one unit. Pages
# load them as the "chat-workspace" script group, and the group's list in
# ``script_groups.json`` joins the unit: Release then never lists a module it
# lacks, nor lacks one the entry script calls.
SCRIPT_GROUPS = 'management_portal/script_groups.json'
CHAT_WORKSPACE_GROUP = 'chat-workspace'


def chat_workspace_unit(root: Path) -> tuple[str, ...]:
    """The script groups file and the Chat group's scripts, as source paths under ``root``."""
    groups = root / SCRIPT_GROUPS
    if not groups.is_file():
        return ()
    listed = json.loads(groups.read_text(encoding='utf-8'))[CHAT_WORKSPACE_GROUP]
    return (SCRIPT_GROUPS, *(f'management_portal{source}' for source in listed))


# portal_workbench.js creates each workbench, and portal_workspace_surface.js
# its Preview panels, workspace browsers, document sessions, document tabs and
# selection controllers, from the part factories their script groups load
# before them, so each root script and its parts are one unit. The group list
# itself stays with the Chat unit, which already carries it; joining it here
# would bind every workbench publication to the Chat.
PART_SCRIPT_GROUPS = ('portal-workbench', 'portal-workspace-surface')


def script_group_unit(root: Path, group: str) -> tuple[str, ...]:
    """The scripts of one group in ``script_groups.json``, as source paths under ``root``."""
    groups = root / SCRIPT_GROUPS
    if not groups.is_file():
        return ()
    listed = json.loads(groups.read_text(encoding='utf-8')).get(group, ())
    return tuple(f'management_portal{source}' for source in listed)


# The Paper, Article, Repo and Projects pages each load their root script after
# the module scripts it creates its parts from, and the Knowledge board its root
# script after the part scripts that share its globals; the home and component
# demo pages keep their style sheets, and the demo page its script, in static
# files. The page lists them, so the page and every script and style sheet it
# loads by the page's prefix are one unit.
PAGES = (
    ('management_portal/paper.html', 'paper'),
    ('management_portal/article_workspace.html', 'article_workspace'),
    ('management_portal/repo_workspace.html', 'repo_workspace'),
    ('management_portal/projects.html', 'project'),
    ('management_portal/index.html', 'home'),
    ('management_portal/test_demo_components.html', 'test_demo_components'),
    ('knowledge_board_lan/knowledge_board.html', 'knowledge_board'),
)


def page_unit(root: Path, page: str, prefix: str) -> tuple[str, ...]:
    """The page and the static scripts and style sheets it loads whose names start with ``prefix``."""
    document = root / page
    if not document.is_file():
        return ()
    names = re.findall(rf'(?:src|href)="/static/({re.escape(prefix)}[a-z_]*\.(?:js|css))"', document.read_text(encoding='utf-8'))
    return (page, *(f'management_portal/static/{name}' for name in dict.fromkeys(names)))


# The Portal Management panel (portal_management.js) and the permission scheme
# page (portal_permission_schemes.js) create their parts from module scripts
# loaded before them, so each root script and its parts are one unit: the
# management scripts portal_management.html loads, with the page, and every
# portal_permission_schemes script. The management panel's other modules,
# which it imports when a section opens, stay outside.
MANAGEMENT_PAGE = ('management_portal/portal_management.html', 'portal_management')
PERMISSION_SCHEMES_PREFIX = 'portal_permission_schemes'


def prefix_unit(root: Path, prefix: str) -> tuple[str, ...]:
    """The static scripts named ``prefix``.js or ``prefix``_*.js, as source paths under ``root``."""
    static = root / 'management_portal/static'
    return tuple(sorted(f'management_portal/static/{path.name}' for path in static.glob(f'{prefix}*.js')
                        if path.stem == prefix or path.stem.startswith(f'{prefix}_')))


def release_units(root: Path) -> tuple[tuple[str, ...], ...]:
    return (LINEAGE_WORKBENCH_RELEASE_UNIT, chat_workspace_unit(root),
            *(script_group_unit(root, group) for group in PART_SCRIPT_GROUPS),
            *(page_unit(root, page, prefix) for page, prefix in PAGES),
            page_unit(root, *MANAGEMENT_PAGE), prefix_unit(root, PERMISSION_SCHEMES_PREFIX))


def expand_release_unit_paths(paths: Iterable[str], root: Path,
                              units: Iterable[tuple[str, ...]] | None = None) -> tuple[str, ...]:
    """Close the selected source paths over every unit one of them belongs to.

    Units can share files, so the closure repeats until no unit adds a file.
    Only members present under ``root`` are added; a member removed from the
    source does not block the others.
    """
    expanded = {PurePosixPath(path).as_posix().rstrip('/') for path in paths}
    units = tuple(release_units(root) if units is None else units)
    grown = True
    while grown:
        grown = False
        for unit in units:
            if any(member == selected or member.startswith(selected + '/')
                   for selected in expanded for member in unit):
                added = {member for member in unit if (root / member).is_file()} - expanded
                expanded |= added
                grown = grown or bool(added)
    return tuple(sorted(expanded))
