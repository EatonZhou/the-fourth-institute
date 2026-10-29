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
    'management_portal/static/lineage_workbench_preview.js',
    'management_portal/static/lineage_workbench_card_menus.js',
    'management_portal/static/lineage_workbench_sidebar.js',
    'management_portal/static/lineage_workbench_recent.js',
    'management_portal/static/portal_lineage_catalogs.js',
    'management_portal/static/portal_hatg_loader.js',
    'management_portal/static/portal_resident_menu.js',
    'management_portal/static/portal_resident_menu.css',
    'management_portal/static/portal_scope_menu.js',
    'management_portal/static/hatg_panel.css',
    'management_portal/static/hatg_graph_model.js',
    'management_portal/static/hatg_lineage_state.js',
    'management_portal/static/hatg_program_edits.js',
    'management_portal/static/hatg_family_blocks.js',
    'management_portal/static/hatg_node_status.js',
    'management_portal/static/hatg_canvas_selection.js',
    'management_portal/static/hatg_function_block_menu.js',
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
    'management_portal/static/hatg_panel.js',
    'management_portal/static/hatg_panel_features.js',
    'management_portal/static/hatg_panel_state.js',
    'management_portal/static/lineage_program.js',
    'management_portal/static/lineage_primitive_definitions.json',
    'management_portal/static/lineage_primitive_inspector.js',
    'management_portal/static/lineage_invocation_inspector.js',
    'management_portal/static/lineage_element_fields.js',
    'management_portal/static/lineage_graph.js',
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


# The Paper, Article, Repo and Projects pages each load their root script after
# the module scripts it creates its parts from. The page lists them, so the page,
# its style sheet and every script it loads by the page's prefix are one unit.
RESOURCE_PAGES = (
    ('management_portal/paper.html', 'paper'),
    ('management_portal/article_workspace.html', 'article_workspace'),
    ('management_portal/repo_workspace.html', 'repo_workspace'),
    ('management_portal/projects.html', 'project'),
)


def resource_page_unit(root: Path, page: str, prefix: str) -> tuple[str, ...]:
    """The page and the static scripts and style sheets it loads whose names start with ``prefix``."""
    document = root / page
    if not document.is_file():
        return ()
    names = re.findall(rf'(?:src|href)="/static/({re.escape(prefix)}[a-z_]*\.(?:js|css))"', document.read_text(encoding='utf-8'))
    return (page, *(f'management_portal/static/{name}' for name in dict.fromkeys(names)))


def release_units(root: Path) -> tuple[tuple[str, ...], ...]:
    return (LINEAGE_WORKBENCH_RELEASE_UNIT, chat_workspace_unit(root),
            *(resource_page_unit(root, page, prefix) for page, prefix in RESOURCE_PAGES))


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
