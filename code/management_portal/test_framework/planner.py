from __future__ import annotations

import hashlib
import json
from typing import Any

from management_portal.test_framework.catalog import ROOT, CatalogRepository, source_digests
from management_portal.test_framework.local_runs import plan_groups
from management_portal.test_framework.models import LIFECYCLES, CatalogError, clean_list
from management_portal.test_framework.registry import RunnerRegistry
from management_portal.test_framework.runner_ids import TAU_RUNNER_ID, runner_ids
from management_portal.test_framework.tau_limits import MAX_PARALLEL_MODULES_CEILING, tau_max_parallel_modules


class RunPlanner:
    """Combines selection and execution conditions without running anything."""

    def __init__(self, catalog: CatalogRepository, registry: RunnerRegistry):
        self.catalog = catalog
        self.registry = registry

    def preview(self, request: dict[str, Any]) -> dict[str, Any]:
        requested_selection = (
            request.get("select") if isinstance(request.get("select"), dict) else {}
        )
        selection = dict(requested_selection)
        if (
            "lifecycle" not in selection
            and not clean_list(selection.get("case_ids"))
        ):
            # Retirement removes a case from normal catalog and campaign flows.
            # An explicit case id remains runnable for diagnosis or recovery.
            selection["lifecycle"] = sorted(LIFECYCLES - {"retired"})
        implementation = request.get("implementation") if isinstance(request.get("implementation"), dict) else {}
        resident_account = implementation.get("resident_account_id", "")
        parallel_modules = implementation.get("max_parallel_modules")
        if "resident_account_id" in implementation:
            from management_portal.execution.account_adapters import CodexRoutes

            if not isinstance(resident_account, str) or not resident_account.strip():
                raise CatalogError("resident_account_id 必须是已登记的 Codex 账号")
            resident_account = CodexRoutes().get(resident_account).account_id
        if parallel_modules is not None and (
            type(parallel_modules) is not int
            or not 1 <= parallel_modules <= MAX_PARALLEL_MODULES_CEILING
        ):
            raise CatalogError(f"max_parallel_modules 必须是 1–{MAX_PARALLEL_MODULES_CEILING} 的整数")
        resident_runtime = _resident_runtime(implementation.get("resident_runtime"))
        fail_fast = implementation.get("fail_fast", False)
        if not isinstance(fail_fast, bool):
            raise CatalogError("fail_fast 必须是布尔值")
        if resident_runtime and not resident_account:
            raise CatalogError("resident_runtime 需要同时指定 resident_account_id")
        selected_runners = set(runner_ids(implementation.get("runners")))
        implementation_ids = set(clean_list(implementation.get("implementation_ids")))
        profile_id = str(implementation.get("profile") or "").strip()
        profile = None
        if profile_id:
            profile = self.registry.profile(profile_id)
        cases = self.catalog.query(selection)
        selected: list[dict[str, Any]] = []
        excluded: list[dict[str, str]] = []
        for case in cases:
            compatible = []
            for item in case.implementations:
                if implementation_ids and item.id not in implementation_ids:
                    continue
                if selected_runners and item.runner not in selected_runners:
                    continue
                descriptor = self.registry.runner(item.runner)
                if not descriptor.available:
                    continue
                if profile_id and profile_id not in item.profiles:
                    continue
                compatible.append(item)
            if not compatible:
                excluded.append({"case_id": case.id, "title": case.title, "reason": "no-compatible-implementation"})
                continue
            for item in compatible:
                selected.append(
                    {
                        "case_id": case.id,
                        "case_title": case.title,
                        "description": case.description,
                        "importance": case.importance,
                        "module": case.module,
                        "aspects": list(case.aspects),
                        "is_design_point": case.is_design_point,
                        "implementation": item.public(),
                        "runner": self.registry.runner(item.runner).public(),
                        "profile": profile_id,
                        # The implementation owns execution side effects. A Profile
                        # contributes a separate environment risk instead of hiding
                        # an undeclared implementation behind a Profile label.
                        "risk": item.risk,
                        "profile_risk": profile.risk if profile else "",
                        **source_digests(case.id, item.public(), ROOT),
                        **({"resident_account_id": resident_account} if resident_account else {}),
                        **({"resident_runtime": resident_runtime} if resident_runtime else {}),
                    }
                )
        groups: list[dict[str, Any]] = []
        is_tau_campaign = bool(selected) and all(
            item["runner"]["id"] == TAU_RUNNER_ID for item in selected
        )
        if (resident_account or parallel_modules is not None) and not is_tau_campaign:
            raise CatalogError("Resident 账号与模块并发设置仅用于 TAU Campaign")
        if fail_fast and is_tau_campaign:
            raise CatalogError("fail_fast 仅用于本地运行；TAU Campaign 总是继续执行其他用例")
        if not is_tau_campaign and any(item["runner"]["id"] == TAU_RUNNER_ID for item in selected):
            # TAU is a live Campaign on DEV with its own Residents; a local
            # run executes pytest only.
            local_cases = {item["case_id"] for item in selected if item["runner"]["id"] != TAU_RUNNER_ID}
            for item in selected:
                if item["runner"]["id"] == TAU_RUNNER_ID and item["case_id"] not in local_cases:
                    local_cases.add(item["case_id"])
                    excluded.append({"case_id": item["case_id"], "title": item["case_title"], "reason": "tau-campaign-only"})
            selected = [item for item in selected if item["runner"]["id"] != TAU_RUNNER_ID]
        if is_tau_campaign:
            for module_id in dict.fromkeys(item["module"] for item in selected):
                groups.append({
                    "group_id": f"module:{module_id}",
                    "module_id": module_id,
                    "runner": TAU_RUNNER_ID,
                    "item_indexes": [
                        index for index, item in enumerate(selected)
                        if item["module"] == module_id
                    ],
                    "continue_on_failure": True,
                })
        elif selected:
            groups = plan_groups(selected, continue_on_failure=not fail_fast)
        normalized_implementation = {
            **implementation,
            **({"runners": list(runner_ids(implementation.get("runners")))} if implementation.get("runners") else {}),
        }
        execution = {
            "mode": "parallel-modules" if is_tau_campaign else "batch",
            "groups": groups,
            **(
                {"max_parallel_modules": parallel_modules if parallel_modules is not None else tau_max_parallel_modules()}
                if is_tau_campaign
                else {}
            ),
        }
        identity = json.dumps({"selection": selection, "implementation": normalized_implementation, "selected": selected, "execution": execution}, sort_keys=True, ensure_ascii=False)
        return {
            "schema": "tfi.test-run-plan.v2",
            "plan_id": f"plan-{hashlib.sha256(identity.encode()).hexdigest()[:16]}",
            "request": {"select": selection, "implementation": normalized_implementation},
            "selected": selected,
            "excluded": excluded,
            "selected_case_count": len({item["case_id"] for item in selected}),
            "selected_implementation_count": len(selected),
            "execution": execution,
            "campaign": "tau" if is_tau_campaign else "",
            "executable": bool(selected),
        }


RESIDENT_RUNTIME_FIELDS = ("model", "reasoning_effort", "permission_preset")
PERMISSION_PRESETS = {"default", "auto-review", "full-access"}


def _resident_runtime(value: Any) -> dict[str, str]:
    """Campaign-wide Codex Resident settings (model, reasoning_effort, permission_preset)."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not value or set(value) - set(RESIDENT_RUNTIME_FIELDS):
        raise CatalogError("resident_runtime 只接受 model、reasoning_effort、permission_preset")
    runtime = {}
    for key in RESIDENT_RUNTIME_FIELDS:
        if key not in value:
            continue
        item = value[key]
        if not isinstance(item, str) or not item.strip() or len(item) > 100:
            raise CatalogError(f"resident_runtime.{key} 必须是非空字符串")
        runtime[key] = item.strip()
    if runtime.get("permission_preset", "full-access") not in PERMISSION_PRESETS:
        raise CatalogError("resident_runtime.permission_preset 不是已知的权限预设")
    return runtime
