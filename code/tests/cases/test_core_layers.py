"""Portal's packages form tiers: a module imports only its own tier's modules and lower tiers'.

``TIERS`` places every module of ``management_portal`` and ``management_common``,
bottom to top: the shared runtime (``common``); the declarations every service
reads, which are the ISA catalog and contracts, the feature table and the address of
each service (``contracts``); stores of any package, the flat stores and the
notification bus (``stores``); the permission system, Portal access control and
the facts they judge by (``policy``); ResourceService, the instruction kernel and
router, and the backends of the core resource types (``resources``);
ExecutionService, its Workers and the Provider runtimes (``execution``);
SocietyService (``society``); and everything else, which is the features, Web,
the workbench projections and the test framework (``features``).  A row lists
patterns of module names relative to ``management_portal``; an exact name wins
over a pattern and a longer pattern over a shorter one, so a package sits in one
tier and only the modules named apart sit elsewhere.  A package's stores sit in
``stores``; a resource provider, named by ``config/resource-types`` rather than
imported, sits with the domain it serves; a feature's own modules
(``portal_features.FEATURES[].modules``) sit in ``features``.

Every import that runs counts: at module top, inside a function, and by name,
through ``importlib.import_module`` or a module path the code resolves (a
Provider's adapter, a Society RPC ``Operation``'s ``implementation``, which
``society_service.server`` imports, the router's kernel builders).  Imports
under ``if TYPE_CHECKING`` do not, nor do module paths given to another process
(``python -m``) or compared with, nor ``portal_features.FEATURES``, which is the
table through which core reaches the features without importing them.
Importing a module also runs its package's ``__init__``; this guard does not
count that, so a package with modules in a lower tier keeps its ``__init__``
light.

``ALLOWED`` lists the imports that still point up, or close a cycle between
packages inside a tier, and what keeps each; an entry leaves the list together
with its import.  Apart from them the core (every tier below ``features``) has
no import cycle through two packages.  ``test_society_service_layers`` orders
SocietyService's own modules.

:module: spec.design-governance
:importance: P1
"""
from __future__ import annotations

import ast
import fnmatch
import re
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTAL = "management_portal"
COMMON = "management_common"

# Bottom to top.  Names are relative to management_portal; "*" is every module no other row places.
TIERS = (
    ("common", "management_common.*"),
    ("contracts", """
    hatg hatg.catalog hatg.catalog.* hatg.contracts hatg.declarations hatg.lineage_versions hatg.resource_actions
    portal_features service_invocation_contract execution.config resources.config society_service.config
    """),
    ("stores", """
    hatg.store execution.service_settings notifications notifications.*
    resources.collaboration_registry resources.domain_projection_evidence resources.errors resources.portal
    resources.registry_store resources.retired_identity_boundary resources.schema resources.timing
    resources.workspace_store
    access_audit_store agent_token_auth application_targets conversation_read_state conversation_receipt_store
    identity_store project_registry resident_view_projection semantic_search_surfaces shared_state
    workflow_bindings_store
    """),
    ("policy", "permission_system permission_system.* portal_access_control user_enrollment"),
    ("resources", """
    resources resources.* hatg.kernel hatg.maintenance hatg.routing hatg.slow_call_profile hatg.socket_rpc
    avatar_database avatar_store device_identity_backend isa_client project_rename project_seeds resident_avatar
    """),
    ("execution", """
    execution execution.* agent_prompt_context agent_skill_context ai_provider_registry antigravity_* business_tasks
    claude_* codex_* dsh_* jev_provider kimi_* model_api_store native_agent_transport opencode_* prompt_injection
    provider_transport_diagnostics provider_websocket resident_shell_environment resource_refs usage_read_model
    """),
    ("society", "society_service society_service.* agent_context_selection resources.providers.collaboration"),
    ("features", """
    * resources.providers.article resources.providers.chatroom resources.providers.direction resources.providers.notice
    resources.providers.paper resources.providers.repo resources.providers.review resources.providers.search
    resources.providers.test resources.providers.workbench resources.repo_recovery
    """),
)
FEATURES = len(TIERS) - 1

LOCAL_KERNELS = ("local routing builds every owner's kernel in this process (KERNEL_BUILDERS), as tests and "
                 "one-off scripts do")
AMA_AREAS = "a Workspace's files are read and written under the personal AMA area rule of lineage_access_policy"
ALLOWED: dict[tuple[str, str], str] = {
    ("hatg.catalog.environment", "execution.service_settings"):
        "UpdateExecutionSettings takes the retention bounds the execution settings keep (RETENTION_DAYS)",
    ("hatg.catalog.resident", "execution.provider_catalog"): "the Resident instructions list the registered Providers",
    ("hatg.catalog.resident", "prompt_injection"): "the Resident instructions list the prompt injection presets and parts",
    ("resources.registry_store", "resources"):
        "a registry write announces its changes to ResourceService, which refreshes its index",
    ("permission_system", "society_service.resident_work_origin"):
        "on_behalf_of reads the User whose input a Resident's running execution works on",
    ("permission_system.facts", "society_service.state_store"):
        "collecting the facts places a Resident's own Workspace with it, from the Lineage store's residents table",
    ("permission_system.management", "society_service"):
        "a credential lent with a message lasts while SocietyService says that input is open",
    ("permission_system.par", "hatg.kernel"):
        "``via: instruction_execution`` slots read the receipts this process's kernels hold (receipt_scopes)",
    ("hatg.maintenance", "society_service"):
        "the other owners hand Lineage's committed projection receipts to SocietyService to retry",
    ("hatg.routing", "execution.isa"): LOCAL_KERNELS,
    ("hatg.routing", "resources.isa"): LOCAL_KERNELS,
    ("hatg.routing", "society_service.isa"): LOCAL_KERNELS,
    ("hatg.routing", "web_isa"): LOCAL_KERNELS,
    ("project_rename", "society_service.resource_client"):
        "renaming a Project reads Workspace paths through resource_client.workspace_paths_many, which tests replace",
    ("resources.content_changes", "execution.service"):
        "content changes are captured only for the running execution of the writing Resident (ExecutionService status)",
    ("resources.isa", "society_service"): "DeleteProject checks and deletes the Project's Lineages in SocietyService",
    ("resources.isa", "society_service.lineage_access_policy"): AMA_AREAS,
    ("resources.providers.folder_files", "society_service.lineage_storage"):
        "a Lineage Folder's files are read under the Lineage's canonical folder",
    ("resources.providers.workspace", "society_service.lineage_access_policy"): AMA_AREAS,
    ("resources.runtime", "resource_index_backend"): "ResourceService's index is resource_index_backend, whose resource_index_* modules are still top-level",
    ("resources.server", "resource_auto_registration"): "ResourceService runs the automatic registration of Repos and Articles",
    ("resources.service_collaboration", "society_service.business_fact_views"):
        "Lineage, Resident and Family projections commit in SocietyService's projection transaction",
    ("resources.service_index", "society_service.business_fact_views"):
        "collaboration descriptors are completed from SocietyService's business fact views (project_resources)",
    ("resources.service_index", "society_service.lineage_access_policy"):
        "reading a Workspace record applies the personal AMA rule (hidden_private_ids)",
    ("execution.isa", "society_service.resource_client"):
        "Home's token usage groups Residents by their current Lineage and Project, read from SocietyService's catalog",
    ("resources.providers.collaboration", "resident_handoff"):
        "unregistering a Resident deletes it through the Resident handoff, which also forgets its invocation bindings",
    ("society_service.service_invocation_domain", "resident_handoff"):
        "search invocations deliver and cancel their inputs through the shared Resident handoff",
}

_MODULE_PATH = re.compile(r"management_(?:portal|common)(?:\.\w+)+(?::[\w.]+)?")


@cache
def _modules() -> dict[str, tuple[bool, ast.Module]]:
    """Dotted module name -> (whether it is a package's ``__init__``, its tree)."""
    found = {}
    for top in (COMMON, PORTAL):
        for path in sorted((ROOT / top).rglob("*.py")):
            if "node_modules" in path.parts:
                continue
            parts = path.relative_to(ROOT).with_suffix("").parts
            package = parts[-1] == "__init__"
            found[".".join(parts[:-1] if package else parts)] = (package, ast.parse(path.read_text(encoding="utf-8")))
    return found


def _short(module: str) -> str:
    return module.removeprefix(PORTAL + ".")


def _patterns():
    """``(row, absolute pattern)`` for every name in ``TIERS``."""
    for row, (_tier, names) in enumerate(TIERS):
        for name in names.split():
            yield row, name if name.startswith(COMMON) else PORTAL + ".*" if name == "*" else f"{PORTAL}.{name}"


def _placements(module: str) -> list[tuple[tuple[bool, int], int, str]]:
    """``((exact, length), row, pattern)`` of each pattern placing ``module``, the most specific first."""
    found = [((module == pattern, len(pattern)), row, pattern) for row, pattern in _patterns()
             if module == pattern or ("*" in pattern and fnmatch.fnmatchcase(module, pattern))]
    return sorted(found, reverse=True)


def _tier(module: str) -> int:
    if module in (PORTAL, COMMON):
        return 0  # The top-level packages only set up import paths.
    return _placements(module)[0][1]


def _runtime_nodes(tree: ast.AST):
    """``(node, parent)`` for every node except those under ``if TYPE_CHECKING:``, which only name types."""
    pending: list[tuple[ast.AST, ast.AST | None]] = [(tree, None)]
    while pending:
        node, parent = pending.pop()
        yield node, parent
        for child in ast.iter_child_nodes(node):
            test = child.test if isinstance(child, ast.If) else None
            if getattr(test, "id", getattr(test, "attr", "")) == "TYPE_CHECKING":
                pending.extend((grandchild, child) for grandchild in child.orelse)
            else:
                pending.append((child, node))


@cache
def _imports() -> dict[str, dict[str, int]]:
    """Module -> {imported module: first line}, function bodies and module paths named in code included."""
    modules = _modules()
    edges: dict[str, dict[str, int]] = {name: {} for name in modules}

    def named(dotted: str) -> str | None:
        """The module a dotted reference names: itself, or the module holding the attribute it names."""
        name = dotted.partition(":")[0]
        while name and name not in modules:
            name = name.rpartition(".")[0]
        return name or None

    def add(source: str, target: str | None, line: int) -> None:
        if target in modules and target != source:
            edges[source].setdefault(target, line)

    for name, (package, tree) in modules.items():
        parts = name.split(".") if package else name.split(".")[:-1]
        operations = set()
        for node, parent in _runtime_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add(name, named(alias.name), node.lineno)
            elif isinstance(node, ast.ImportFrom):
                base = (".".join([*parts[:len(parts) - node.level + 1], *([node.module] if node.module else [])])
                        if node.level else node.module or "")
                for alias in node.names:
                    full = f"{base}.{alias.name}"
                    add(name, full if full in modules else named(base), node.lineno)
            elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Operation":
                for keyword in node.keywords:
                    if keyword.arg == "implementation" and isinstance(keyword.value, ast.Constant):
                        operations.add(id(keyword.value))
                        add(f"{PORTAL}.society_service.server", named(str(keyword.value.value)), node.lineno)
            elif (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in operations
                  and _MODULE_PATH.fullmatch(node.value) and name != f"{PORTAL}.portal_features"
                  and not isinstance(parent, (ast.List, ast.Tuple, ast.Set, ast.Compare))
                  and not (isinstance(parent, ast.keyword) and parent.arg == "prog")):
                add(name, named(node.value), node.lineno)
    return edges


def _package(module: str) -> str | None:
    """The package a module belongs to; a module directly under management_portal belongs to none."""
    parts = module.split(".")
    if parts[0] == COMMON:
        return COMMON
    if len(parts) > 2 or (len(parts) == 2 and (ROOT / PORTAL / parts[1] / "__init__.py").is_file()):
        return parts[1]
    return None


def _strongly_connected(edges: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    for start in sorted(edges):
        if start in index:
            continue
        work = [(start, iter(sorted(edges[start])))]
        index[start] = low[start] = len(index)
        stack.append(start)
        on_stack.add(start)
        while work:
            node, targets = work[-1]
            for target in targets:
                if target not in index:
                    index[target] = low[target] = len(index)
                    stack.append(target)
                    on_stack.add(target)
                    work.append((target, iter(sorted(edges[target]))))
                    break
                if target in on_stack:
                    low[node] = min(low[node], index[target])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    component = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    found.append(sorted(component))
    return found


def test_every_module_has_one_tier():
    """每个模块恰好由一层放置，分层表里没有失效的名称"""
    modules = set(_modules()) - {PORTAL, COMMON}
    unused = sorted(pattern for _row, pattern in _patterns()
                    if not any(module == pattern or fnmatch.fnmatchcase(module, pattern) for module in modules))
    assert not unused, f"Remove these names from TIERS: {unused}"
    ambiguous = sorted(module for module in modules
                       if len(placements := _placements(module)) > 1 and placements[0][0] == placements[1][0])
    assert not ambiguous, f"These modules are placed twice: {ambiguous}"


def test_feature_modules_sit_in_the_features_tier():
    """功能自己的模块（portal_features 中声明的 modules）在功能层"""
    from management_portal.portal_features import FEATURES as DECLARED

    patterns = [pattern for feature in DECLARED for pattern in feature.modules]
    misplaced = sorted(module for module in _modules()
                       if any(fnmatch.fnmatchcase(module, pattern) for pattern in patterns) and _tier(module) != FEATURES)
    assert not misplaced, f"Feature modules must sit in the features tier: {misplaced}"


def test_imports_point_to_own_or_lower_tiers():
    """模块只导入本层或更低层的模块"""
    names = [tier for tier, _names in TIERS]
    upward = [
        f"{_short(source)}:{line} -> {_short(target)} ({names[_tier(source)]} -> {names[_tier(target)]})"
        for source, targets in sorted(_imports().items())
        for target, line in sorted(targets.items())
        if _tier(target) > _tier(source) and (_short(source), _short(target)) not in ALLOWED
    ]
    assert not upward, f"Imports must point to the module's own tier or lower: {upward}"


def test_core_packages_form_no_import_cycle():
    """核心各层的包之间没有导入环"""
    core = {module for module in _modules() if _tier(module) < FEATURES}
    edges = {source: {target for target in targets if target in core and (_short(source), _short(target)) not in ALLOWED}
             for source, targets in _imports().items() if source in core}
    cycles = [component for component in _strongly_connected(edges)
              if len({_package(module) for module in component} - {None}) > 1]
    assert not cycles, "Import cycles through several packages: " + "; ".join(
        f"{sorted({_package(module) for module in component} - {None})}: "
        + ", ".join(_short(module) for module in component[:12]) for component in cycles)


def test_allowed_imports_are_still_there():
    """ALLOWED 中的每条导入仍然存在，且仍然上行或连通两个包"""
    edges = _imports()
    absolute = {(source if source.startswith(COMMON) else f"{PORTAL}.{source}",
                 target if target.startswith(COMMON) else f"{PORTAL}.{target}"): (source, target)
                for source, target in ALLOWED}
    gone = sorted(f"{source} -> {target}" for (full_source, full_target), (source, target) in absolute.items()
                  if full_target not in edges.get(full_source, {}))
    assert not gone, f"Remove these entries from ALLOWED: {gone}"
    needless = sorted(f"{source} -> {target}" for (full_source, full_target), (source, target) in absolute.items()
                      if _tier(full_target) <= _tier(full_source) and _package(full_source) == _package(full_target))
    assert not needless, f"These imports need no entry in ALLOWED: {needless}"
