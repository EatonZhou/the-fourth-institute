"""Build the Test Catalog from pytest sources and explicit JSON cases.

Pytest tests are discovered from source on each catalog refresh. Their docstrings
provide titles, descriptions and catalog fields. TAU and design cases remain
explicit JSON declarations; the browser suite is no longer discovered.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from management_common.format_tags import schema_matches
from management_portal.test_framework.catalog_metadata import aspects_for, importance_for, module_for
from management_portal.test_framework.models import (ASPECTS, IMPLEMENTATION_RISKS, IMPORTANCE, LIFECYCLES, CatalogError, TestCase,
                                    design_point_ref)
from management_portal.test_framework.registry import PROFILES
from management_portal.test_framework.taxonomy import canonical_feature, canonical_module, features as feature_titles
from management_portal.test_framework.paths import ROOT


# Only source-line lookup for retained JavaScript evidence uses this pattern.
JS_TEST_RE = re.compile(
    r"(?m)^[ \t]*test(?:\.(?:skip|only|fixme))?\([ \t]*(?P<quote>[\"'`])(?P<title>(?:\\.|(?!(?P=quote))[^\r\n\\])*)(?P=quote)[ \t]*,"
)
PY_FIELD_RE = re.compile(r"^:(?P<name>[a-z][a-z-]*):(?:[ \t]+(?P<value>.*?))?[ \t]*$")
FIELDS = frozenset({"module", "feature", "importance", "aspects", "design", "design-point", "lifecycle", "risk", "profiles"})
CJK = re.compile(r"[　-ヿ㐀-鿿＀-￯]")


def javascript_title(match: re.Match) -> str:
    # Decode quoted literals consistently for discovery and line lookup.
    return re.sub(r"\\([\\\"'`])", lambda item: item.group(1), match.group("title")).strip()


def derived_case_id(entrypoint: str) -> str:
    return "test_" + hashlib.sha256(f"test-case:{entrypoint}".encode("utf-8")).hexdigest()[:12]


def implementation_id(entrypoint: str) -> str:
    return "impl-" + hashlib.sha256(f"impl:{entrypoint}".encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Doc:
    """A Python docstring: title, description and catalog fields."""

    title: str = ""
    description: str = ""
    fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SourceTest:
    entrypoint: str
    name: str
    runner: str
    source: str
    line: int
    doc: Doc


@dataclass(frozen=True)
class SourceFile:
    source: str
    doc: Doc = Doc()
    tests: tuple[SourceTest, ...] = ()
    error: str = ""
    features: tuple[str, ...] = ()


@dataclass
class Catalog:
    revision: tuple = ()
    cases: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    # Every pytest entrypoint found in the sources.
    discovered: frozenset[str] = frozenset()


def _join(lines: list[str]) -> str:
    text = ""
    for line in (item.strip() for item in lines):
        if not line:
            continue
        # Wrapped Chinese needs no space where the lines meet.
        glue = "" if not text or (CJK.search(text[-1]) and CJK.search(line[0])) else " "
        text += glue + line
    return text


def parse_doc(text: str | None, field_re: re.Pattern) -> Doc:
    lines = (text or "").strip().splitlines()
    fields: list[tuple[str, str]] = []
    while lines:
        line = lines[-1].strip()
        match = field_re.match(line)
        if line and not match:
            break
        lines.pop()
        if match:
            fields.insert(0, (match.group("name"), (match.group("value") or "").strip()))
    paragraphs: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            paragraphs[-1].append(line)
        elif paragraphs[-1]:
            paragraphs.append([])
    texts = [_join(item) for item in paragraphs if item]
    return Doc(texts[0] if texts else "", "\n".join(texts[1:]), tuple(fields))


def _is_test_function(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
        return False
    for decorator in node.decorator_list:
        function = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = function.attr if isinstance(function, ast.Attribute) else function.id if isinstance(function, ast.Name) else ""
        if name in {"fixture", "yield_fixture"}:
            return False
    return True


def _python_tests(tree: ast.Module, relative: str):
    for top in tree.body:
        if _is_test_function(top):
            yield f"{relative}::{top.name}", top.name, top
        elif isinstance(top, ast.ClassDef) and (
            top.name.startswith("Test")
            # pytest also collects unittest.TestCase subclasses of any name.
            or any(ast.unparse(base).endswith("TestCase") for base in top.bases)
        ):
            for child in top.body:
                if _is_test_function(child):
                    yield f"{relative}::{top.name}::{child.name}", child.name, child


def imported_features(tree: ast.Module) -> tuple[str, ...]:
    """The features whose modules a test file imports anywhere; ``from a import b`` names ``a`` and ``a.b``."""
    from management_portal.portal_features import FEATURES
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.update([node.module, *(f"{node.module}.{alias.name}" for alias in node.names)])
    return tuple(feature.key for feature in FEATURES
                 if any(fnmatch.fnmatchcase(name, pattern) for name in names for pattern in feature.modules))


def parse_source(path: Path, relative: str) -> SourceFile:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return SourceFile(relative, error=f"测试源码无法读取：{exc}")
    if path.suffix == ".py":
        try:
            tree = ast.parse(content, filename=relative)
        except SyntaxError as exc:
            return SourceFile(relative, error=f"测试源码无法解析：{exc}")
        return SourceFile(relative, parse_doc(ast.get_docstring(tree), PY_FIELD_RE), tuple(
            SourceTest(entrypoint, name, "pytest", relative, node.lineno, parse_doc(ast.get_docstring(node), PY_FIELD_RE))
            for entrypoint, name, node in _python_tests(tree, relative)
        ), features=imported_features(tree))
    return SourceFile(relative)


def source_paths(root: Path) -> list[Path]:
    cases_root = root / "tests" / "cases"
    return sorted(cases_root.glob("test_*.py"))


def explicit_paths(root: Path) -> list[Path]:
    cases_root = root / "tests" / "cases"
    return [path for path in (
        cases_root / "tau_cases.json",
        cases_root / "spec_design_cases.json",
        *sorted((cases_root / "design_live").glob("*.json")),
    ) if path.is_file()]


def case_ids_path(root: Path) -> Path:
    return root / "tests" / "cases" / "case_ids.json"


def live_cases_path(root: Path) -> Path:
    return root / "tests" / "cases" / "live_cases.json"


def catalog_paths(root: Path) -> list[Path]:
    """Every file the catalog is built from, for change tracking."""
    return [*source_paths(root), *explicit_paths(root), case_ids_path(root), live_cases_path(root)]


def humanized(name: str) -> str:
    words = name.removeprefix("test_").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _values(value: str) -> list[str]:
    return [item for item in re.split(r"[\s,，、]+", value) if item]


def _apply_fields(target: dict[str, Any], fields: tuple[tuple[str, str], ...], where: str) -> list[str]:
    errors = []
    for name, value in fields:
        try:
            if name not in FIELDS:
                raise CatalogError(f"未知目录字段 {name!r}")
            if name == "module":
                target["module"] = canonical_module(value)
            elif name == "importance":
                if value.upper() not in IMPORTANCE:
                    raise CatalogError(f"重要等级无效 {value!r}")
                target["importance"] = value.upper()
            elif name == "aspects":
                aspects = _values(value)
                if not aspects or not set(aspects) <= ASPECTS:
                    raise CatalogError(f"测试方面无效 {value!r}")
                target["aspects"] = aspects
            elif name == "design":
                if not _values(value):
                    raise CatalogError("design 必须列出 Design ID")
                target["design_refs"] = [*target.get("design_refs", []), *_values(value)]
            elif name == "design-point":
                target["is_design_point"] = True
                if value:
                    ref = design_point_ref(value)
                    target["design_point_ref"] = ref
                    target["design_refs"] = [*target.get("design_refs", []), ref.partition("#")[0]]
            elif name == "feature":
                if not _values(value):
                    raise CatalogError("feature 必须列出功能键")
                target["features"] = [*target.get("features", []), *map(canonical_feature, _values(value))]
            elif name == "lifecycle":
                if value not in LIFECYCLES:
                    raise CatalogError(f"生命周期无效 {value!r}")
                target["lifecycle"] = value
            elif name == "risk":
                if value not in IMPLEMENTATION_RISKS:
                    raise CatalogError(f"执行风险无效 {value!r}")
                target["risk"] = value
            elif name == "profiles":
                profiles = _values(value)
                if not set(profiles) <= {profile.id for profile in PROFILES}:
                    raise CatalogError(f"运行配置无效 {value!r}")
                target["profiles"] = profiles
        except (CatalogError, ValueError) as exc:
            errors.append(f"{where}：{exc}")
    return errors


def derive_case(test: SourceTest, file_doc: Doc, case_id: str, live: Any,
                imported: tuple[str, ...] = ()) -> tuple[dict[str, Any], list[str]]:
    """The case of one test; the errors are those of the test's own fields.

    ``imported`` are the features whose modules the test's file imports; a
    ``feature`` field adds the ones a test reaches another way (HTTP, files).
    """
    case: dict[str, Any] = {
        "id": case_id,
        "key": test.entrypoint,
        "title": test.doc.title or humanized(test.name),
        "description": test.doc.description,
        "module": module_for(test.source),
        "importance": importance_for(test.source, test.name),
        "aspects": aspects_for(test.source),
        "is_design_point": False,
        "design_refs": [],
        "lifecycle": "active",
    }
    _apply_fields(case, file_doc.fields, test.source)
    errors = _apply_fields(case, test.doc.fields, test.entrypoint)
    case["design_refs"] = list(dict.fromkeys(case["design_refs"]))
    wanted = {*imported, *case.get("features", [])}
    case["features"] = [key for key in feature_titles() if key in wanted]
    if "architecture-conformance" in case["aspects"] and not case["design_refs"]:
        case["design_refs"] = [str(ROOT / "SPEC.md")]
    case["implementations"] = [{
        "id": implementation_id(test.entrypoint),
        "runner": test.runner,
        "entrypoint": test.entrypoint,
        "source": test.source,
        "profiles": case.pop("profiles", []),
        "risk": case.pop("risk", "unspecified"),
    }]
    if live is not None:
        case["live"] = live
    return case, errors


def _read_json(path: Path, schema: str | None = None) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or (schema and not schema_matches(raw.get("schema"), expected=schema)):
        raise CatalogError(f"{path.name} 必须使用 {schema}")
    return raw


def explicit_cases(root: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in explicit_paths(root):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
            raise CatalogError(f"{path}：显式目录必须包含 cases 数组")
        is_live_source = path.parent.name == "design_live"
        expected_schema = "tfi.portal-design-live-cases.v1" if is_live_source else {
            "tau_cases.json": "tfi.portal-tau-cases.v1",
            "spec_design_cases.json": "tfi.portal-spec-design-cases.v1",
        }[path.name]
        if not schema_matches(raw.get("schema"), expected=expected_schema):
            raise CatalogError(f"{path}：显式目录必须使用 {expected_schema}")
        for item in raw["cases"]:
            case = TestCase.parse(item)
            if is_live_source and (not case.design_point_ref or not case.live):
                raise CatalogError(f"{path}：{case.id} 必须具有精确点绑定和可播放 Live")
            cases.append(dict(item))
    return cases


_LOCK = threading.Lock()
_FILES: dict[Path, tuple[tuple[int, int], SourceFile]] = {}
_BUILDS: dict[Path, tuple[tuple, Catalog]] = {}


def _stat(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _parsed(path: Path, root: Path) -> SourceFile:
    revision = _stat(path)
    cached = _FILES.get(path)
    if cached and cached[0] == revision:
        return cached[1]
    parsed = parse_source(path, path.relative_to(root).as_posix())
    _FILES[path] = (revision, parsed)
    return parsed


def read_case_ids(root: Path) -> dict[str, str]:
    return dict(_read_json(case_ids_path(root), "tfi.test-case-ids.v1").get("ids", {}))


def effective_case_ids(pins: dict[str, str], entrypoints: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Pinned IDs by current entrypoint, and the pins whose test is gone.

    A pinned test moved to another file of the same directory keeps its ID,
    as long as exactly one unpinned test there has its name.
    """
    current = {entry: case_id for entry, case_id in pins.items() if entry in entrypoints}
    orphans = {entry: case_id for entry, case_id in pins.items() if entry not in entrypoints}

    def place(entry: str) -> tuple[str, str]:
        source, _separator, name = entry.partition("::")
        return str(Path(source).parent), name

    unpinned: dict[tuple[str, str], list[str]] = {}
    for entry in entrypoints - current.keys():
        unpinned.setdefault(place(entry), []).append(entry)
    followed = {}
    for entry, case_id in orphans.items():
        candidates = unpinned.get(place(entry), [])
        if len(candidates) == 1 and sum(place(other) == place(entry) for other in orphans) == 1:
            followed[entry] = candidates[0]
            current[candidates[0]] = case_id
    return current, {entry: case_id for entry, case_id in orphans.items() if entry not in followed}


def build_catalog(root: Path) -> Catalog:
    """The catalog of the sources under ``root``; reused until a source changes."""
    root = root.resolve()
    paths = catalog_paths(root)
    revision = tuple((str(path), _stat(path)) for path in paths)
    with _LOCK:
        cached = _BUILDS.get(root)
        if cached and cached[0] == revision:
            return cached[1]
        files = [_parsed(path, root) for path in source_paths(root)]
    catalog = Catalog(revision=revision)
    try:
        explicit = explicit_cases(root)
    except (CatalogError, ValueError, OSError) as exc:
        explicit = []
        catalog.errors.append({"resource_id": "explicit", "error": str(exc)[:1000]})
    try:
        pins = read_case_ids(root)
        live = _read_json(live_cases_path(root), "tfi.portal-live-cases.v1").get("implementations", {})
    except (CatalogError, ValueError, OSError) as exc:
        pins, live = {}, {}
        catalog.errors.append({"resource_id": "catalog", "error": str(exc)[:1000]})
    claimed = {impl["entrypoint"] for case in explicit for impl in case.get("implementations", [])}
    catalog.discovered = frozenset(test.entrypoint for parsed in files for test in parsed.tests)
    case_ids, _orphans = effective_case_ids(pins, set(catalog.discovered))
    for case in explicit:
        for impl in case.get("implementations", []):
            pinned = case_ids.get(impl["entrypoint"])
            if pinned and pinned != case["id"]:
                catalog.errors.append({"resource_id": case["id"], "error": (
                    f"{impl['entrypoint']} 已属于 {pinned}，不能迁移到 {case['id']} 并丢弃稳定 ID")})
    grouped: dict[str, dict[str, Any]] = {}
    for parsed in files:
        if parsed.error:
            catalog.errors.append({"resource_id": parsed.source, "error": parsed.error})
            continue
        catalog.errors.extend(
            {"resource_id": parsed.source, "error": error}
            for error in _apply_fields({}, parsed.doc.fields, parsed.source)
        )
        for test in parsed.tests:
            if test.entrypoint in claimed:
                continue
            case_id = case_ids.get(test.entrypoint) or derived_case_id(test.entrypoint)
            case, errors = derive_case(test, parsed.doc, case_id, live.get(test.entrypoint), parsed.features)
            catalog.errors.extend({"resource_id": case_id, "error": error} for error in errors)
            previous = grouped.get(case_id)
            if previous is None:
                grouped[case_id] = case
            elif any(impl["entrypoint"] == test.entrypoint for impl in previous["implementations"]):
                # Repeated declarations cannot identify distinct implementations.
                catalog.errors.append({"resource_id": case_id, "error": f"测试入口重复：{test.entrypoint}"})
            else:
                # Entrypoints pinned to one ID are one case; the first gives its metadata.
                first, other = sorted((previous, case), key=lambda item: item["key"])
                first["implementations"] = [*first["implementations"], *other["implementations"]]
                first["features"] = list(dict.fromkeys([*first["features"], *other["features"]]))
                if "live" not in first and "live" in other:
                    first["live"] = other["live"]
                grouped[case_id] = first
    catalog.cases = [*explicit, *grouped.values()]
    with _LOCK:
        _BUILDS[root] = (revision, catalog)
    return catalog
