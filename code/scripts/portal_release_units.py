"""Browser compatibility units: groups of files that reach Release together.

A browser runtime split across an HTML shell and several scripts works only
when all of its files come from one revision. A publication that selects any
member of a unit therefore publishes the whole unit; files of the unit that
did not change add nothing to the commit.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional

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
# its dependencies, so the entry script and every module are one unit.
CHAT_WORKSPACE_ENTRY = 'management_portal/static/portal_chat_workspace.js'
CHAT_WORKSPACE_MODULES = 'management_portal/static/chat_workspace_*.js'
CHAT_SCRIPT = re.compile(r'/static/(?:chat_workspace_[a-z_]+|portal_chat_workspace)\.js')
# Each page that loads the Chat lists its scripts, and so does the server's
# list of public static files.
CHAT_WORKSPACE_LISTINGS = (
    'management_portal/*.html',
    'management_portal/test_framework/page/*.html',
    '*_lan/*.html',
    'management_portal/serve_management_portal.py',
)

ReleasedText = Callable[[str], Optional[str]]


def chat_workspace_unit(root: Path, released_text: ReleasedText | None = None) -> tuple[str, ...]:
    """The Chat entry script and modules, and each file whose Chat script list differs from Release.

    A page or the server names the Chat scripts one by one. When a module is
    added, removed or renamed, that list changes in Dev; such a file joins the
    unit so Release never runs an entry script with modules its pages do not
    load. ``released_text(path)`` returns the file at the released commit, or
    ``None`` when it is absent there; without it no listing file joins.
    """
    members = [CHAT_WORKSPACE_ENTRY,
               *sorted(path.relative_to(root).as_posix() for path in root.glob(CHAT_WORKSPACE_MODULES))]
    if released_text is not None:
        for pattern in CHAT_WORKSPACE_LISTINGS:
            for path in sorted(root.glob(pattern)):
                name = path.relative_to(root).as_posix()
                listed = CHAT_SCRIPT.findall(path.read_text(encoding='utf-8'))
                if listed and listed != CHAT_SCRIPT.findall(released_text(name) or ''):
                    members.append(name)
    return tuple(members)


def release_units(root: Path, released_text: ReleasedText | None = None) -> tuple[tuple[str, ...], ...]:
    return (LINEAGE_WORKBENCH_RELEASE_UNIT, chat_workspace_unit(root, released_text))


def expand_release_unit_paths(paths: Iterable[str], root: Path,
                              released_text: ReleasedText | None = None,
                              units: Iterable[tuple[str, ...]] | None = None) -> tuple[str, ...]:
    """Close the selected source paths over every unit one of them belongs to.

    Units can share files (``lineage.html`` loads the Chat), so the closure
    repeats until no unit adds a file. Only members present under ``root`` are
    added; a member removed from the source does not block the others.
    """
    expanded = {PurePosixPath(path).as_posix().rstrip('/') for path in paths}
    units = tuple(release_units(root, released_text) if units is None else units)
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
