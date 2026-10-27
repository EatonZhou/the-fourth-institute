"""Resident domain modules form layers: each imports only modules of lower layers.

``society_service/resident_*domain*.py`` split the Resident domain by
responsibility.  ``LAYERS`` lists them bottom to top; a module may import a
sibling only from a row below its own, so the modules have no cycles and
modules of one row never import each other.  Sibling imports sit at module
top, where a cycle would fail as soon as the module loads; the check still
parses function bodies so an import placed there cannot hide an edge.  A new
module needs a row.

The other modules of the package sit between the layers.  When a Resident
domain module imports one of them, the Resident domain modules that module
imports must lie below the lowest row importing it, otherwise the two form a
cycle through it.  A call that has to go up, such as a recovery driver
restarting a session, goes through ``domain_ports.resident_domain_port``, which
``resident_domain_host`` serves.

A name used across modules is part of its module's interface and carries no
leading underscore; importing or reading another module's ``_name`` fails.

:module: spec.design-governance
:importance: P1
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "management_portal.society_service"
SOURCE = ROOT / "management_portal" / "society_service"

# Bottom to top.  Short names drop the ``resident_`` prefix and ``_domain``
# suffix; ``resident_domain`` is "domain".
LAYERS = (
    "domain_common project",
    "record",
    "lifecycle runtime conversation_projection media watchboard",
    "prompt",
    "input_queue",
    "execution_engine",
    "session_backend",
    "execution",
    "intake",
    "workspace",
    "restart",
    "fork",
    "registration",
    "management",
    "queue_control",
    "directory actual_sent",
    "lifecycle_steps",
    "domain_host",
    "domain",
)
LAYER = {name: row for row, names in enumerate(LAYERS) for name in names.split()}


def _short(stem: str) -> str:
    return "domain" if stem == "resident_domain" else stem.removeprefix("resident_").removesuffix("_domain")


def _modules() -> dict[str, ast.Module]:
    return {path.stem: ast.parse(path.read_text(encoding="utf-8")) for path in sorted(SOURCE.glob("resident_*domain*.py"))}


def _sibling_imports(stem: str, nodes, stems: set[str]):
    """Yield (line, sibling, imported name or None) for each sibling import among ``nodes``."""
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.removeprefix(PACKAGE + ".") in stems - {stem}:
                    yield node.lineno, alias.name.removeprefix(PACKAGE + "."), None
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 1:
            base = node.module
        elif node.level == 0 and node.module in {PACKAGE} | {f"{PACKAGE}.{name}" for name in stems}:
            base = node.module.removeprefix(PACKAGE).removeprefix(".") or None
        else:
            continue
        for alias in node.names:
            if base is None and alias.name in stems - {stem}:
                yield node.lineno, alias.name, None
            elif base in stems - {stem}:
                yield node.lineno, base, alias.name


def test_sibling_imports_point_to_lower_layers():
    """Resident 领域模块只导入更低层的兄弟模块"""
    modules = _modules()
    stems = set(modules)
    assert {_short(stem) for stem in stems} == set(LAYER), "Give an added Resident domain module its row in LAYERS"
    upward = [
        f"{_short(stem)}:{line} -> {_short(sibling)}"
        for stem, tree in modules.items()
        for line, sibling, _name in _sibling_imports(stem, ast.walk(tree), stems)
        if LAYER[_short(sibling)] >= LAYER[_short(stem)]
    ]
    assert not upward, f"Imports must point to a lower layer: {upward}"


def test_sibling_imports_sit_at_module_top():
    """Resident 领域模块在模块顶层导入兄弟模块"""
    modules = _modules()
    stems = set(modules)
    nested = []
    for stem, tree in modules.items():
        top = {id(node) for node in tree.body}
        inner = [node for node in ast.walk(tree) if id(node) not in top]
        nested += [f"{stem}:{line} {sibling}" for line, sibling, _name in _sibling_imports(stem, inner, stems)]
    assert not nested, f"Move these imports to the module top: {nested}"


def _package_imports() -> dict[str, set[str]]:
    """Module of the package (dotted, relative to it) -> package modules it imports, function bodies included."""
    files = {}
    for path in sorted(SOURCE.rglob("*.py")):
        parts = path.relative_to(SOURCE).with_suffix("").parts
        package = parts[:-1]
        name = ".".join(package if parts[-1] == "__init__" else parts)
        if name:
            files[name] = (package, ast.parse(path.read_text(encoding="utf-8")))
    edges = {}
    for name, (package, tree) in files.items():
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bases = [alias.name.removeprefix(PACKAGE + ".") for alias in node.names
                         if alias.name.startswith(PACKAGE + ".")]
                found.update(base for base in bases if base in files)
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                if node.level - 1 > len(package):
                    continue
                base = ".".join([*package[:len(package) - node.level + 1], *([node.module] if node.module else [])])
            elif node.module == PACKAGE or str(node.module).startswith(PACKAGE + "."):
                base = node.module.removeprefix(PACKAGE).removeprefix(".")
            else:
                continue
            found.update(target for target in [base, *(f"{base}.{a.name}" if base else a.name for a in node.names)]
                         if target in files)
        edges[name] = found - {name}
    return edges


def test_modules_between_layers_import_only_lower_layers():
    """Resident 领域模块导入的其他模块只导入更低层的 Resident 领域模块"""
    edges = _package_imports()
    stems = set(_modules())
    upward = []
    for name, targets in sorted(edges.items()):
        if name in stems:
            continue
        importers = sorted((LAYER[_short(stem)], _short(stem)) for stem in stems if name in edges[stem])
        if not importers:
            continue
        row, importer = importers[0]
        reached = sorted(_short(target) for target in targets & stems if LAYER[_short(target)] >= row)
        if reached:
            upward.append(f"{name} (imported by {importer}) -> {reached}")
    assert not upward, f"These modules import Resident domain modules at or above their importers: {upward}"


def test_modules_use_only_public_names_of_siblings():
    """Resident 领域模块只使用兄弟模块的公开名字"""
    modules = _modules()
    stems = set(modules)
    private = []
    for stem, tree in modules.items():
        bound = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.level, node.module) in {(1, None), (0, PACKAGE)}:
                bound.update({alias.asname or alias.name: alias.name for alias in node.names if alias.name in stems})
        for line, sibling, name in _sibling_imports(stem, ast.walk(tree), stems):
            if name and name.startswith("_"):
                private.append(f"{stem}:{line} imports {sibling}.{name}")
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in bound and node.attr.startswith("_")):
                private.append(f"{stem}:{node.lineno} reads {bound[node.value.id]}.{node.attr}")
    assert not private, private
