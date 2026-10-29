from __future__ import annotations

import ast
import hashlib
import json
import threading
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from management_common.file_lock import lock_stream
from management_common.format_tags import schema_matches
from management_portal.test_framework.catalog_source import JS_TEST_RE, build_catalog, javascript_title
from management_portal.test_framework.models import ASPECTS, IMPORTANCE, CatalogError, Implementation, TestCase, clean_list, design_point_ref
from management_portal.test_framework.taxonomy import CORE_FEATURE, features as feature_titles


ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def catalog_write_lock(path: Path):
    with path.with_suffix(path.suffix + ".lock").open("a+b") as handle:
        with lock_stream(handle):
            yield


def atomic_catalog_write(path: Path, payload: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def declaration_line(entrypoint: str, root: Path = ROOT) -> int | None:
    """The line that declares a pytest or Playwright entrypoint now (None when it is gone).

    The line stored in the catalog goes stale as soon as the file above the
    test is edited, so runners and source views resolve it from the source.
    """
    source, _separator, name = str(entrypoint).partition("::")
    path = root / source
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if path.suffix == ".py":
        *owners, function = name.split("::")
        function = function.split("[", 1)[0]
        try:
            body = ast.parse(content, filename=source).body
        except SyntaxError:
            return None
        for owner in owners:
            body = next((node.body for node in body if isinstance(node, ast.ClassDef) and node.name == owner), [])
        return next((node.lineno for node in body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function), None)
    return next((content.count("\n", 0, match.start()) + 1 for match in JS_TEST_RE.finditer(content)
                 if javascript_title(match) == name), None)


def source_digests(case_id: str, implementation: dict[str, Any], root: Path = ROOT) -> dict[str, str]:
    """Freeze the source file and, for declarative TAU, the selected case's semantics."""
    source = str(implementation['source'])
    path = (root / source).resolve(strict=False)
    if root.resolve() not in path.parents or not path.is_file():
        raise CatalogError(f"测试实现源码不可用：{source}")
    try:
        content = path.read_bytes()
        digests = {'source_sha256': hashlib.sha256(content).hexdigest()}
        if implementation.get('runner') != 'tau' or path.suffix != '.json':
            return digests
        document = json.loads(content)
        if not any(schema_matches(document.get('schema'), expected=tag) for tag in (
            'tfi.portal-tau-cases.v1', 'tfi.portal-spec-design-cases.v1',
        )):
            raise ValueError('unknown TAU source schema')
        matches = [case for case in document['cases'] if case['id'] == case_id]
        if len(matches) != 1:
            raise ValueError('TAU case must have one declaration')
        case = matches[0]
        implementations = case['implementations']
        selected = [item for item in implementations if item['id'] == implementation['id']]
        if len(selected) != 1 or Implementation.parse(selected[0], case_id).public() != implementation:
            raise ValueError('catalog and TAU implementation differ')
        # A generated source line and other cases are not this declaration's semantics.
        case = {**case, 'implementations': [
            {key: value for key, value in item.items() if key != 'line'} for item in implementations
        ]}
        snapshot = {key: value for key, value in document.items() if key != 'cases'}
        snapshot['cases'] = [case]
        encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        digests['source_case_sha256'] = hashlib.sha256(encoded).hexdigest()
        return digests
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise CatalogError(f"测试实现源码声明不可用或与目录不一致：{source} / {case_id}") from exc


def planned_source_changed(item: dict[str, Any], root: Path = ROOT) -> bool:
    """Check the plan's recorded scope; old plans retain their whole-file contract."""
    implementation = item['implementation']
    if 'source_case_sha256' in item:
        try:
            actual = source_digests(item['case_id'], implementation, root)
        except CatalogError:
            return True
        return actual.get('source_case_sha256') != item['source_case_sha256']
    path = (root / str(implementation['source'])).resolve(strict=False)
    actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and root.resolve() in path.parents else ''
    return actual != item.get('source_sha256')


class CatalogRepository:
    """The only owner of Test Case definitions and catalog query semantics.

    The catalog is built from the test sources under ``root``
    (``catalog_source``).
    """

    def __init__(self, *, verify_sources: bool = True, root: Path | None = None):
        self.verify_sources = verify_sources
        self.root = root
        self._lock = threading.RLock()
        self._projection_cache_revision: tuple[Any, ...] | None = None
        self._projection_cache: tuple[tuple[TestCase, ...], tuple[tuple[str, str], ...]] | None = None

    def all(self) -> tuple[TestCase, ...]:
        """Every case; any catalog error raises."""
        cases, errors = self.projection()
        if errors:
            raise CatalogError("；".join(f"{item['resource_id']}：{item['error']}" for item in errors[:5]))
        return cases

    def projection(self) -> tuple[tuple[TestCase, ...], list[dict[str, str]]]:
        """Return every valid case while isolating malformed catalog entries."""
        with self._lock:
            catalog = build_catalog(self.root or ROOT)
            revision, raw_cases, build_errors, discovered = catalog.revision, catalog.cases, catalog.errors, catalog.discovered
            if revision == self._projection_cache_revision and self._projection_cache is not None:
                cached_cases, cached_errors = self._projection_cache
                return cached_cases, [
                    {"resource_id": resource_id, "error": error}
                    for resource_id, error in cached_errors
                ]
            cases: list[TestCase] = []
            errors: list[dict[str, str]] = list(build_errors)
            raw_ids: dict[str, int] = {}
            for item in raw_cases:
                raw_id = str(item.get("id") or "") if isinstance(item, dict) else ""
                if raw_id:
                    raw_ids[raw_id] = raw_ids.get(raw_id, 0) + 1
                try:
                    if not isinstance(item, dict):
                        raise CatalogError("测试用例必须是对象")
                    cases.append(TestCase.parse(item))
                except (CatalogError, TypeError, ValueError) as exc:
                    errors.append({"resource_id": raw_id, "error": str(exc)[:1000]})
            conflicts: dict[str, list[TestCase]] = {}
            for case in cases:
                keys = [f"ID:{case.id}", f"key:{case.key}"]
                if case.design_point_ref:
                    keys.append(f"设计点:{case.design_point_ref}")
                for impl in case.implementations:
                    keys.extend((f"实现ID:{impl.id}", f"实现入口:{impl.entrypoint}"))
                for key in keys:
                    conflicts.setdefault(key, []).append(case)
            blocked: dict[str, str] = {key: "测试用例 ID 重复" for key, count in raw_ids.items() if count > 1}
            for key, owners in conflicts.items():
                if len(owners) > 1:
                    problem = "设计点重复绑定" if key.startswith("设计点:") else "测试定义重复"
                    blocked.update({case.id: f"{problem}：{key}" for case in owners})
            if self.verify_sources:
                for case in cases:
                    for impl in case.implementations:
                        if impl.runner in {"pytest", "browser-script"} and impl.entrypoint not in discovered:
                            blocked[case.id] = f"测试实现源码不可用或入口已失效：{impl.entrypoint}"
            errors.extend({"resource_id": key, "error": value} for key, value in blocked.items())
            projected = tuple(case for case in cases if case.id not in blocked)
            cached_errors = tuple(
                (str(item.get("resource_id") or ""), str(item.get("error") or ""))
                for item in errors
            )
            self._projection_cache_revision = revision
            self._projection_cache = (projected, cached_errors)
            return projected, [
                {"resource_id": resource_id, "error": error}
                for resource_id, error in cached_errors
            ]

    def get(self, case_id: str) -> TestCase:
        cases, errors = self.projection()
        for case in cases:
            if case.id == case_id:
                return case
        failure = next((item for item in errors if item["resource_id"] == case_id), None)
        if failure:
            raise CatalogError(failure["error"])
        raise KeyError(f"未知测试用例：{case_id}")

    def query(self, filters: dict[str, Any] | None = None) -> tuple[TestCase, ...]:
        values = filters if isinstance(filters, dict) else {}
        ids = set(clean_list(values.get("case_ids")))
        modules = set(clean_list(values.get("modules")))
        features = set(clean_list(values.get("features")))
        importance = {item.upper() for item in clean_list(values.get("importance"))}
        aspects = set(clean_list(values.get("aspects")))
        design = values.get("is_design_point")
        has_live = values.get("has_live")
        lifecycle = set(clean_list(values.get("lifecycle")))
        # "none" stands for a case without any implementation.
        runners = set(clean_list(values.get("runners")))
        current = values.get("current") is True
        search = str(values.get("search") or "").strip().casefold()
        point_ref = design_point_ref(values.get("design_point_ref"))
        has_binding = values.get("has_design_point_ref")
        if importance - set(IMPORTANCE) or aspects - ASPECTS or features - {CORE_FEATURE, *feature_titles()}:
            raise CatalogError("测试目录筛选条件无效")
        cases, errors = self.projection()
        failures = [
            item for item in errors
            if item["resource_id"] in ids
            or (
                point_ref
                and f"设计点:{point_ref}" in item["error"]
            )
        ]
        if failures:
            if point_ref and any(
                f"设计点:{point_ref}" in item["error"]
                for item in failures
            ):
                raise CatalogError(f"设计点重复绑定 {point_ref}")
            raise CatalogError("；".join(item["error"] for item in failures))
        unknown = ids - {case.id for case in cases}
        if unknown:
            raise CatalogError(f"未知测试用例：{sorted(unknown)}")
        result: list[TestCase] = []
        for case in cases:
            if point_ref and case.design_point_ref != point_ref:
                continue
            if isinstance(has_binding, bool) and bool(case.design_point_ref) is not has_binding:
                continue
            if ids and case.id not in ids:
                continue
            if modules and case.module not in modules:
                continue
            if features and not features.intersection(case.features or (CORE_FEATURE,)):
                continue
            if importance and case.importance not in importance:
                continue
            if aspects and not aspects.intersection(case.aspects):
                continue
            if isinstance(design, bool) and case.is_design_point is not design:
                continue
            if isinstance(has_live, bool) and bool(case.live) is not has_live:
                continue
            if lifecycle and case.lifecycle not in lifecycle:
                continue
            if current and case.lifecycle == "retired":
                continue
            if runners and not runners.intersection({item.runner for item in case.implementations} or {"none"}):
                continue
            searchable = (case.id, case.title, case.description, case.module, case.key,
                          *(implementation.entrypoint for implementation in case.implementations))
            if search and search not in " ".join(searchable).casefold():
                continue
            result.append(case)
        return tuple(result)

    def summary(self, cases: Iterable[TestCase] | None = None) -> dict[str, Any]:
        selected = tuple(cases) if cases is not None else self.projection()[0]
        return {
            "total": len(selected),
            "design_points": sum(case.is_design_point for case in selected),
            "without_implementation": sum(not case.implementations for case in selected),
            "needs_review": sum(case.lifecycle == "needs-review" for case in selected),
            "by_importance": {value: sum(case.importance == value for case in selected) for value in IMPORTANCE},
        }
