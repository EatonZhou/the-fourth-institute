from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from management_portal.test_framework.catalog import ROOT, CatalogRepository, declaration_line
from management_portal.test_framework.models import ASPECTS, IMPORTANCE
from management_portal.test_framework.planner import RunPlanner
from management_portal.test_framework.records import RunCoordinator, RunRecords
from management_portal.test_framework.registry import RunnerRegistry
from management_portal.test_framework.taxonomy import MODULE_GROUPS, public_features, public_taxonomy
from management_portal.test_framework.tau_residents import TauResidentAssignments


class TestFrameworkService:
    """Unified application boundary for every Test Framework consumer."""

    def __init__(
        self,
        *,
        state_root: Path,
        registry: RunnerRegistry | None = None,
        tau_residents: TauResidentAssignments | None = None,
        launch: Callable[[str], None] | None = None,
    ):
        # The catalog comes from the test sources; a source being edited
        # only isolates its own cases.
        self.catalog = CatalogRepository(verify_sources=False)
        self.registry = registry or RunnerRegistry()
        self.tau_residents = tau_residents or TauResidentAssignments(state_root / "tau-residents.json")
        self.planner = RunPlanner(self.catalog, self.registry)
        self.records = RunRecords(state_root / "runs.json")
        self.runs = RunCoordinator(self.records, state_root / "run-artifacts", launch=launch)

    def query(self, request: dict[str, Any] | None = None, *, actor: str = "") -> dict[str, Any]:
        filters = request if isinstance(request, dict) else {}
        cases = self.catalog.query(filters)
        all_cases, catalog_errors = self.catalog.projection()
        module_counts = {module.id: sum(case.module == module.id for case in all_cases) for group in MODULE_GROUPS for module in group.modules}
        return {
            "schema": "tfi.test-framework.v2",
            "summary": self.catalog.summary(cases),
            "catalog_errors": catalog_errors,
            "filters": {"importance": list(IMPORTANCE), "modules": public_taxonomy(), "features": public_features(),
                        "aspects": sorted(ASPECTS)},
            "module_counts": module_counts,
            "capabilities": self.registry.public(),
            "cases": [case.public() for case in cases],
            "recent_runs": self.records.recent_summaries() if actor else [],
            "tau_residents": self.tau_residents.list([
                module.id for group in MODULE_GROUPS for module in group.modules
                if module_counts[module.id]
            ]),
        }

    def query_workspace(self, request: dict[str, Any] | None = None, *, actor: str = "") -> dict[str, Any]:
        """Return the bounded bootstrap projection used by the Test workspace."""
        filters = request if isinstance(request, dict) else {}
        cases = self.catalog.query(filters)
        all_cases, catalog_errors = self.catalog.projection()
        module_counts = {
            module.id: sum(case.module == module.id for case in all_cases)
            for group in MODULE_GROUPS
            for module in group.modules
        }
        return {
            "schema": "tfi.test-workspace-catalog.v1",
            "summary": self.catalog.summary(cases),
            "catalog_errors": catalog_errors,
            "filters": {
                "importance": list(IMPORTANCE),
                "modules": public_taxonomy(),
                "features": public_features(),
                "aspects": sorted(ASPECTS),
            },
            "module_counts": module_counts,
            "capabilities": self.registry.public(),
            "cases": [case.workspace_summary() for case in cases],
            "recent_runs": self.records.recent_summaries() if actor else [],
            "tau_residents": self.tau_residents.list([
                module.id
                for group in MODULE_GROUPS
                for module in group.modules
                if module_counts[module.id]
            ]),
        }

    def query_overview(self, *, actor: str = "") -> dict[str, Any]:
        """The Test page's overview: catalog figures per module, recent runs and TAU slots, without case rows.

        Retired cases are left out of every figure.  The catalog page reads its
        cases a page at a time (``query_page``), so the overview stays small
        however large the catalog grows.  Each recent run also names how many
        cases it selected, their modules and, for a single case, its title.
        """
        all_cases, catalog_errors = self.catalog.projection()
        cases = [case for case in all_cases if case.lifecycle != "retired"]
        members: dict[str, list[Any]] = {}
        for case in cases:
            members.setdefault(case.module, []).append(case)
        modules = []
        for group in MODULE_GROUPS:
            for module in group.modules:
                runners = [{item.runner for item in case.implementations} for case in members.get(module.id, [])]
                modules.append({
                    "id": module.id,
                    "group_id": group.id,
                    "total": len(runners),
                    "by_importance": {value: sum(case.importance == value for case in members.get(module.id, [])) for value in IMPORTANCE},
                    "design_points": sum(case.is_design_point for case in members.get(module.id, [])),
                    "tau": sum("tau" in kinds for kinds in runners),
                    "without_implementation": sum(not kinds for kinds in runners),
                    "live": sum(bool(case.live) for case in members.get(module.id, [])),
                })
        known = {case.id: case for case in all_cases}
        runs = self.records.recent_summaries() if actor else []
        for run in runs:
            selected = list(dict.fromkeys(item.get("case_id") for item in (run.get("plan") or {}).get("selected") or [] if item.get("case_id")))
            run["case_count"] = len(selected)
            run["title"] = known[selected[0]].title if len(selected) == 1 and selected[0] in known else ""
            run["module_ids"] = sorted({known[case_id].module for case_id in selected if case_id in known})
        return {
            "schema": "tfi.test-overview.v1",
            "summary": self.catalog.summary(cases),
            "catalog_errors": catalog_errors,
            "filters": {
                "importance": list(IMPORTANCE),
                "modules": public_taxonomy(),
                "features": public_features(),
                "aspects": sorted(ASPECTS),
            },
            "modules": modules,
            "capabilities": self.registry.public(),
            "recent_runs": runs,
            "tau_residents": self.tau_residents.list([module["id"] for module in modules if module["total"]]),
        }

    def query_page(self, request: dict[str, Any] | None = None, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """One page of the catalog list: the browse fields of the matching cases.

        Retired cases are left out unless the request names lifecycles.  The
        list runs module by module in taxonomy order, most important first, so
        a page shows each module's cases together.  ``count`` is the number of
        matches; ``limit`` is at most 500.
        """
        filters = dict(request) if isinstance(request, dict) else {}
        if not filters.get("lifecycle"):
            filters["current"] = True
        order = {module.id: index for index, module in enumerate(module for group in MODULE_GROUPS for module in group.modules)}
        cases = sorted(self.catalog.query(filters), key=lambda case: (
            order.get(case.module, len(order)), IMPORTANCE.index(case.importance) if case.importance in IMPORTANCE else len(IMPORTANCE), case.key))
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 500))
        return {
            "schema": "tfi.test-catalog-page.v1",
            "count": len(cases),
            "offset": offset,
            "limit": limit,
            "cases": [case.workspace_summary() for case in cases[offset:offset + limit]],
        }

    def query_case_ids(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """Project canonical Catalog query results without duplicating case data."""
        cases = self.catalog.query(request if isinstance(request, dict) else {})
        return {
            "schema": "tfi.test-catalog-query.v1",
            "case_ids": [case.id for case in cases],
            "count": len(cases),
        }

    def implementation_source(
        self,
        case_id: str,
        implementation_id: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        """Load only a Catalog-owned implementation source as a preview document."""

        if not actor:
            raise PermissionError("需要已认证用户")
        case = self.catalog.get(str(case_id or "").strip())
        implementation = next(
            (
                item
                for item in case.implementations
                if item.id == str(implementation_id or "").strip()
            ),
            None,
        )
        if implementation is None:
            raise KeyError("未知测试实现")
        source_path = (ROOT / implementation.source).resolve(strict=True)
        source_root = ROOT.resolve(strict=True)
        if source_path != source_root and source_root not in source_path.parents:
            raise PermissionError("测试实现源码超出仓库范围")
        if not source_path.is_file():
            raise FileNotFoundError(implementation.source)
        content = source_path.read_text(encoding="utf-8")
        return {
            "id": f"{case.id}:{implementation.id}",
            "title": implementation.source,
            "path": implementation.source,
            "content": content,
            "line": declaration_line(implementation.entrypoint) or 1,
            "entrypoint": implementation.entrypoint,
            "capabilities": {
                "download": False,
                "edit": False,
                "refresh": True,
                "save": False,
            },
        }

    def preview(self, request: dict[str, Any], *, actor: str) -> dict[str, Any]:
        if not actor:
            raise PermissionError("需要已认证用户")
        return self.planner.preview(request)

    def start(self, request: dict[str, Any], *, actor: str) -> dict[str, Any]:
        plan = self.preview(request, actor=actor)
        return self.runs.start(plan, actor)

    def get_run(self, run_id: str, *, actor: str) -> dict[str, Any]:
        return self.runs.get(run_id, actor)

    def cancel_run(self, run_id: str, *, actor: str) -> dict[str, Any]:
        return self.runs.cancel(run_id, actor)

    def delete_runs(self, run_ids: list[str], *, actor: str) -> dict[str, Any]:
        return self.runs.delete_reports(run_ids, actor)

    def rotate_tau_resident(self, module_id: str, *, actor: str, backend_route_id: str = "") -> dict[str, Any]:
        if not actor:
            raise PermissionError("需要已认证用户")
        if module_id not in {module.id for group in MODULE_GROUPS for module in group.modules}:
            raise KeyError(f"未知功能模块：{module_id}")
        return self.tau_residents.rotate(module_id, changed_by=actor, backend_route_id=backend_route_id)

    def configure_tau_residents(self, module_ids: list[str], *, actor: str, backend_route_id: str, fast: bool) -> dict[str, Any]:
        from management_portal.execution.account_adapters import CodexRoutes
        from management_portal.test_framework.tau_runtime import apply_runtime_options
        if not actor:
            raise PermissionError("需要已认证用户")
        if not isinstance(fast, bool):
            raise ValueError("fast 必须为 boolean")
        modules = self._tau_modules(module_ids)
        account = CodexRoutes().get(backend_route_id).account_id
        previous = {item["module_id"]: item for item in self.tau_residents.list(modules)}
        account_changes = {module for module in modules if previous[module].get("backend_route_id") != account}
        for run in self.records.recent():
            if run.get("status") not in {"queued", "running", "cancelling"}:
                continue
            selected = (run.get("plan") or {}).get("selected") or []
            if any(item.get("module") in account_changes and item.get("runner", {}).get("id") == "tau" for item in selected):
                raise ValueError("选定模块仍有 TAU 运行，不能在运行中更改账号")
        assignments = self.tau_residents.configure(
            modules, backend_route_id=account, service_tier="priority" if fast else "", changed_by=actor,
        )
        runtime_updates = []
        for assignment in assignments:
            update = {"module_id": assignment["module_id"], "resident_id": assignment.get("resident_id", "")}
            if assignment["module_id"] in account_changes:
                runtime_updates.append({**update, "status": "pending_prepare"})
                continue
            try:
                runtime_updates.append({**update, **apply_runtime_options(assignment)})
            except Exception as exc:
                runtime_updates.append({**update, "status": "failed", "error": str(exc)})
        failed = any(item["status"] == "failed" for item in runtime_updates)
        return {"residents": assignments, "runtime_updates": runtime_updates, "status": "partially_configured" if failed else "configured"}

    def tau_resident_status(self, module_ids: list[str]) -> dict[str, Any]:
        from management_portal.test_framework.tau_runtime import resident_status

        assignments = self.tau_residents.list(self._tau_modules(module_ids))
        results = [resident_status(assignment) for assignment in assignments]
        return {"residents": results, "ready": all(item["ready"] for item in results)}

    @staticmethod
    def _tau_modules(module_ids: list[str]) -> list[str]:
        if not isinstance(module_ids, list) or not module_ids or not all(isinstance(module_id, str) for module_id in module_ids):
            raise ValueError("module_ids 必须为非空模块 ID 数组")
        known = {module.id for group in MODULE_GROUPS for module in group.modules}
        if any(module_id not in known for module_id in module_ids):
            raise ValueError("module_ids 包含未知功能模块")
        return list(dict.fromkeys(module_ids))
