"""Resident domain modules import each other only along recorded edges.

``society_service/resident_*domain*.py`` are being split into layers.  Most
sibling imports sit inside function bodies, so a new cycle never fails at
import time and only shows up when the function first runs.  The check parses
every module, function bodies included, and compares the sibling modules each
one imports with the recorded edge set.  A new edge fails; an edge that
disappeared asks for the record to be tightened, so the set only shrinks while
the split proceeds.

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

# Module short name -> sibling modules it imports.  Short names drop the
# ``resident_`` prefix and ``_domain`` suffix; ``resident_domain`` is "domain".
EDGES = {
    "actual_sent": "conversation_projection record",
    "conversation_projection": "domain_common",
    "directory": "conversation_projection domain_common lifecycle record runtime watchboard",
    "domain": "actual_sent conversation_projection directory domain_common execution fork intake lifecycle management record registration restart runtime watchboard workspace",
    "domain_common": "",
    "domain_host": "directory fork lifecycle management record registration restart workspace",
    "execution": "directory domain_common input_queue lifecycle prompt record runtime session_backend",
    "fork": "domain_common execution lifecycle record runtime session_backend watchboard workspace",
    "input_queue": "domain_common lifecycle prompt record",
    "intake": "domain_common input_queue lifecycle media prompt record runtime session_backend",
    "lifecycle": "domain_common execution record restart runtime session_backend workspace",
    "management": "conversation_projection domain_common input_queue intake lifecycle record workspace",
    "media": "domain_common record",
    "project": "",
    "prompt": "domain_common record runtime",
    "queue_control": "domain_common execution input_queue record",
    "record": "domain_common",
    "registration": "domain_common fork project record runtime session_backend workspace",
    "restart": "conversation_projection domain_common execution input_queue record runtime session_backend",
    "runtime": "domain_common record",
    "session_backend": "domain_common execution record runtime",
    "watchboard": "domain_common record",
    "workspace": "domain_common lifecycle record",
}


def _short(stem: str) -> str:
    return "domain" if stem == "resident_domain" else stem.removeprefix("resident_").removesuffix("_domain")


def _modules() -> dict[str, ast.Module]:
    return {path.stem: ast.parse(path.read_text(encoding="utf-8")) for path in sorted(SOURCE.glob("resident_*domain*.py"))}


def _sibling_imports(stem: str, tree: ast.Module, stems: set[str]):
    """Yield (line, sibling, imported name or None) for each sibling import."""
    for node in ast.walk(tree):
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


def test_sibling_imports_follow_recorded_edges():
    modules = _modules()
    stems = set(modules)
    assert {_short(stem) for stem in stems} == set(EDGES), "Record the edges of an added or removed Resident domain module in EDGES"
    added, removed = [], []
    for stem, tree in modules.items():
        actual = {_short(sibling) for _line, sibling, _name in _sibling_imports(stem, tree, stems)}
        recorded = set(EDGES[_short(stem)].split())
        added += [f"{_short(stem)} -> {name}" for name in sorted(actual - recorded)]
        removed += [f"{_short(stem)} -> {name}" for name in sorted(recorded - actual)]
    assert not added, f"New imports between Resident domain modules: {added}"
    assert not removed, f"These edges are gone; remove them from EDGES: {removed}"


def test_modules_use_only_public_names_of_siblings():
    modules = _modules()
    stems = set(modules)
    private = []
    for stem, tree in modules.items():
        bound = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.level, node.module) in {(1, None), (0, PACKAGE)}:
                bound.update({alias.asname or alias.name: alias.name for alias in node.names if alias.name in stems})
        for line, sibling, name in _sibling_imports(stem, tree, stems):
            if name and name.startswith("_"):
                private.append(f"{stem}:{line} imports {sibling}.{name}")
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in bound and node.attr.startswith("_")):
                private.append(f"{stem}:{node.lineno} reads {bound[node.value.id]}.{node.attr}")
    assert not private, private
