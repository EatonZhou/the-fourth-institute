"""Features a Portal package may leave out, and their parts in shared actions.

Serving Web routes, running Web background work, deleting or renaming a
Project, deleting a User, filling a Mailbox, collecting the permission facts,
opening a built-in workbench and cleaning up after a test run each need a part
from every installed feature.  Core code asks ``contributions(hook)`` for
those parts instead of importing the features.  ``FEATURES`` names each part
as a ``"module:attribute"`` string (or a bare module) that is imported on
first use, the way ``execution/provider_catalog.py`` names Provider adapters.
A package that leaves a feature out leaves out its modules; the feature then
contributes nothing and the shared action goes on without it.  ``modules``
lists the feature's own modules (``fnmatch`` patterns of dotted names): core
modules must not import them at the top level (``scripts/
check_portal_architecture.py``).  Feature keys follow the distribution units
of decision_15aab18c0ab3 where one exists.  The text only a feature shows has
its translations in ``translations/<feature key>.json``; Web and the browser
catalog build merge the files that are present into the core catalog
(``localization.py``, ``scripts/build_portal_translations.py``).

Hooks:

``web.routes`` ``(portal) -> Iterable[http_routes.RouteDefinition]``
    The feature's pages, APIs and public static files.  ``portal`` is the Web
    shell module (``serve_management_portal``), whose ``_PUBLIC_PAGES_HTTP``
    serves public static files; handlers answer through the Web request and
    response modules (``http_responses``, ``http_request_body``, ``http_auth``,
    ``http_documents``, ``http_files``, ``http_static_files``).  The shell
    serves them after its own routes and before its fallbacks.
``web.lifespan`` ``() -> stop | None``
    Start the feature's background work when Web starts; the returned
    callable stops it when Web stops.
``watchboard.application``
    A Watchboard application module (``watchboards/service.py``).
``isa.instructions``
    A module of HATG instruction declarations every service reads:
    ``DECLARATIONS`` (``hatg.declarations.InstructionDeclaration``) and, for a
    category only features use, ``CATEGORIES`` (category -> title).  The
    catalog (``hatg/catalog``) merges them with the core instructions.
``web.instructions``
    The Portal Web handlers of the feature's Web-owned instructions:
    ``HANDLERS`` (name -> handler) and optionally ``version_of(inv,
    object_id)`` for the version tokens of the feature's objects
    (``web_isa.py``).  Only Web loads it.
``web.page_instructions``
    Instruction names and sample arguments the feature's management controls
    need in ``/api/me`` access.allows. Missing features contribute none.
``web.native_view``
    The feature's view in the development projection page (``/development``):
    a dict with ``id``, ``label``, ``icon``, ``title`` and ``template``, the
    path of an HTML file holding ``<template id="<id>NativeView">`` and a
    ``<script type="application/json" id="<id>NativeViewAssets">`` with the
    view's ``styles`` and ``scripts``; ``{"script-group": <name>}`` among the
    scripts stands for a group of ``script_groups.json``.  The page's activity
    rail lists the installed views (``http_portal_pages.py``,
    ``static/development_workspace.js``).
``web.home_panel``
    The path of an HTML fragment with the feature's panel on Home: its
    markup, styles and script.  Home inserts the installed fragments before
    its own script (``http_portal_pages.home_page``); the script registers
    the panel with ``PortalHomeComponents.registerPanel``.
``web.navigation``
    A tuple of the feature's entries in the site navigation: dicts with ``id``,
    ``label``, ``href`` and ``order`` (the place among the main entries), or
    with ``section`` naming the main entry whose secondary menu lists them.
    Pages read them from ``window.__PORTAL_FRONTEND__`` (``frontend_manifest.py``).
``workbench.builtin``
    The built-in business workbench the feature provides: a dict with
    ``name``, ``title``, ``path`` and ``description``.  Opening ``path``
    initializes its authoritative entry (``static/portal_projections.js``).

``project.relations`` ``(project_id, new_project_id="", *, write=True) -> list[str]``
    Re-point the feature's references to a Project at its new ID, or drop
    them when ``new_project_id`` is empty; ``write=False`` only reports the
    resource IDs that would change.  ``related_types`` names the feature's
    resource types a Project lists but does not own: deleting the Project
    drops their relation instead of unregistering them.
``user.deleted`` ``(username) -> object``
    Remove what the feature keeps for a deleted User; a truthy result means
    something was removed.
``mailbox.source``
    A ``mailbox_sources.MailboxSource``: the feature's items in Users' Mailboxes.
``permission.records`` ``() -> Iterable[dict]``
    The feature's objects the permission system places: each record has
    ``resource_id``, ``resource_type``, ``created_by`` and the relations the
    scheme reads (``permission_system/facts.py``).
``application.targets``
    The feature's applications that give their work to a configured Resident
    or Lineage: ``{name: {"target": "resident" | "lineage", "admission":
    bool}}`` (``application_targets.py``).  Their rows live in the core table
    ``application_targets``; a left-out feature's row stays unread.
``workbench.workflows.<workbench>`` ``(role) -> list[dict]``
    The workflows a built-in workbench offers a role.
``test.fixtures`` ``(root, manifest, actor, *, timeout) -> (report, errors)``
    Release what a test run created under ``root``; the report joins the
    cleanup receipt, errors keep the run's files for another attempt.
``resource.index_adapters``
    A tuple of ResourceAdapter classes. Only installed features contribute
    adapters to ResourceIndexService; their scans/projections belong to the feature.
``resource.registry_projection``
    A source with resource_type, collection, validate(id, document), and
    project(item, *, adapter). The shared registry adapter owns change routing
    and per-resource error handling; the source owns its domain representation.
``resource.portal_routes``
    ``{resource_type: (path, parameter)}``: the feature's page for resources
    of these types, replacing the core page in resource links and the
    browser route contract (``resources/portal.py``).  The Projects page
    opens a Project at ``/projects?project=<ID>``.
``resource.article_registration``
    The Article source: records() -> (records, errors), discover_roots(roots)
    -> (articles, skipped), and is_storage_path(path) -> bool. Automatic
    registration and Repo classification use this same optional source.
"""
from __future__ import annotations
import importlib
import logging
from dataclasses import dataclass, field
from functools import cache
from typing import Any
LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class PortalFeature:
    key: str
    title: str
    contributions: dict[str, str] = field(default_factory=dict)
    related_types: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    presentation_feature: str = ''
FEATURES: tuple[PortalFeature, ...] = (PortalFeature('paper', 'Paper', {'web.routes': 'management_portal.http_paper:routes', 'web.navigation': 'management_portal.http_paper:NAVIGATION', 'resource.index_adapters': 'management_portal.paper_resource_index:ADAPTERS', 'project.relations': 'management_portal.paper_store:project_relations', 'workbench.builtin': 'management_portal.paper_projection:BUILTIN', 'workbench.workflows.paper': 'management_portal.paper_projection:definitions'}, related_types=('paper',), modules=('management_portal.paper_*', 'management_portal.http_paper', 'management_portal.resources.providers.paper')), PortalFeature('projects', 'Projects', {'web.routes': 'management_portal.http_projects:routes', 'web.navigation': 'management_portal.http_projects:NAVIGATION', 'resource.portal_routes': 'management_portal.http_projects:RESOURCE_ROUTES', 'isa.instructions': 'management_portal.research_map_instructions', 'web.instructions': 'management_portal.research_map_web_isa', 'permission.records': 'management_portal.research_map_store:permission_records', 'project.relations': 'management_portal.research_map_store:project_relations'}, modules=('management_portal.http_projects', 'management_portal.research_map_*', 'management_portal.resources.providers.direction')), PortalFeature('article', 'Article', {'web.routes': 'management_portal.http_articles:routes', 'resource.registry_projection': 'management_portal.article_resource_index:REGISTRY_PROJECTION', 'resource.article_registration': 'management_portal.article_auto_registration:SOURCE'}, modules=('management_portal.article_*', 'management_portal.http_articles', 'management_portal.resources.providers.article')), PortalFeature('repo', 'Repo', {'web.routes': 'management_portal.http_repo:routes'}, modules=('management_portal.http_repo', 'management_portal.resources.providers.repo')), PortalFeature('backup', 'Resource Backup', {'web.routes': 'management_portal.http_resource_backup:routes', 'web.navigation': 'management_portal.http_resource_backup:NAVIGATION'}, modules=('management_portal.resource_backup_backend', 'management_portal.backup_lineage', 'management_portal.http_resource_backup')), PortalFeature('wall', 'WALL', {'web.routes': 'management_portal.http_wall:routes', 'web.lifespan': 'management_portal.wall_backend:web_lifespan', 'web.native_view': 'management_portal.http_wall:NATIVE_VIEW', 'workbench.builtin': 'management_portal.wall_workflows:BUILTIN', 'workbench.workflows.wall': 'management_portal.wall_workflows:definitions', 'test.fixtures': 'management_portal.wall_backend:release_test_fixtures'}, modules=('management_portal.wall_*', 'management_portal.http_wall')), PortalFeature('test_framework', 'Test', {'web.routes': 'management_portal.test_framework.web:routes', 'web.lifespan': 'management_portal.test_framework.api:start_runtime', 'web.native_view': 'management_portal.test_framework.web:NATIVE_VIEW', 'workbench.builtin': 'management_portal.test_framework.workflows:BUILTIN', 'workbench.workflows.test': 'management_portal.test_framework.workflows:definitions', 'resource.index_adapters': 'management_portal.test_framework.resource_index:ADAPTERS', 'application.targets': 'management_portal.test_framework.tau_runtime:APPLICATION_TARGETS'}, modules=('management_portal.test_framework', 'management_portal.test_framework.*', 'management_portal.resources.providers.test'), presentation_feature='test_view'), PortalFeature('notice_board', 'Notice Board', {'web.routes': 'management_portal.http_notice_board:routes', 'web.home_panel': 'management_portal.http_notice_board:HOME_PANEL', 'web.lifespan': 'management_portal.notice_board_classifier:web_lifespan', 'isa.instructions': 'management_portal.notice_board_instructions', 'web.instructions': 'management_portal.notice_board_web_isa', 'mailbox.source': 'management_portal.notice_board_backend:MAILBOX_SOURCE', 'permission.records': 'management_portal.notice_board_backend:resource_records', 'application.targets': 'management_portal.notice_board_classifier:APPLICATION_TARGETS'}, modules=('management_portal.notice_*', 'management_portal.http_notice_board', 'management_portal.resources.providers.notice')), PortalFeature('chatroom', 'Chatroom', {'isa.instructions': 'management_portal.chatroom_instructions', 'web.instructions': 'management_portal.chatroom_web_isa', 'mailbox.source': 'management_portal.chatroom_backend:MAILBOX_SOURCE', 'permission.records': 'management_portal.chatroom_backend:resource_records'}, modules=('management_portal.chatroom_*', 'management_portal.resources.providers.chatroom')), PortalFeature('pulse', 'Pulse', {'isa.instructions': 'management_portal.pulse_instructions', 'web.instructions': 'management_portal.pulse_web_isa'}, modules=('management_portal.portal_pulse_backend', 'management_portal.pulse_*')), PortalFeature('telepus', 'Telepus', {'isa.instructions': 'management_portal.telepus_instructions', 'web.instructions': 'management_portal.telepus_web_isa', 'web.page_instructions': 'management_portal.telepus_instructions:PAGE_INSTRUCTIONS', 'watchboard.application': 'management_portal.watchboards.telepus', 'user.deleted': 'management_portal.watchboards.telepus:remove_user_contacts'}, modules=('management_portal.watchboards.telepus', 'management_portal.telepus_*')))

def _load(reference: str) -> Any | None:
    """The attribute a reference names, or None when its module is not installed."""
    module_name, _, attribute = reference.partition(':')
    try:
        target = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if not exc.name or not (module_name == exc.name or module_name.startswith(exc.name + '.')):
            raise
        return None
    for name in filter(None, attribute.split('.')):
        target = getattr(target, name)
    return target

@cache
def contributions(hook: str) -> tuple[tuple[str, Any], ...]:
    """``(feature key, part)`` of every installed feature contributing to ``hook``, in ``FEATURES`` order."""
    found = []
    for feature in FEATURES:
        if feature.presentation_feature and hook in {'web.native_view', 'workbench.builtin', 'web.navigation'}:
            from management_common.distribution_features import included
            if not included(feature.presentation_feature):
                continue
        reference = feature.contributions.get(hook)
        if reference is None:
            continue
        part = _load(reference)
        if part is None:
            LOGGER.info('feature %s is not installed; %s goes on without it', feature.key, hook)
            continue
        found.append((feature.key, part))
    return tuple(found)

def contribution(hook: str) -> Any | None:
    """The part of the one installed feature contributing to ``hook``."""
    found = contributions(hook)
    return found[0][1] if found else None

def related_types() -> frozenset[str]:
    """The resource types Projects list but do not own (``project.relations``)."""
    return frozenset((kind for feature in FEATURES for kind in feature.related_types))
