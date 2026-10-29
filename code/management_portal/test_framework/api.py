from __future__ import annotations

import threading
from typing import Any

from management_portal.test_framework.service import TestFrameworkService
from management_portal.test_framework.tau import TauConfig
from management_portal.test_framework.registry import RunnerRegistry
from management_portal.test_framework.tau_residents import TauResidentAssignments
from management_portal.test_framework.paths import state_root


_LOCK = threading.Lock()
_SERVICE: TestFrameworkService | None = None


def service(*, refresh_runtime: bool = False) -> TestFrameworkService:
    """The process's Test Framework service, built on first use."""
    global _SERVICE
    with _LOCK:
        if _SERVICE is None:
            root = state_root()
            tau = TauConfig.from_environment()
            tau_reason = tau.unavailable_reason()
            _SERVICE = TestFrameworkService(
                state_root=root,
                registry=RunnerRegistry(tau_available=not tau_reason, tau_reason=tau_reason),
                tau_residents=TauResidentAssignments(root / "tau-residents.json"),
            )
        elif refresh_runtime:
            # Dependencies and the test browser login may be provisioned after
            # Web started. Keep durable runs and their owners; refresh readiness.
            reason = TauConfig.from_environment().unavailable_reason()
            _SERVICE.registry.refresh_tau(available=not reason, reason=reason)
        return _SERVICE


def start_runtime() -> None:
    """Relaunch the executors of runs that lost theirs once Web starts; no unit survives a host restart."""
    threading.Thread(target=lambda: service().runs.recover(), name="test-framework-start", daemon=True).start()


def tau_admission(*, actor: str) -> dict[str, Any]:
    """Read the Society-owned TAU admission fence for the Test workbench."""
    from management_portal.permission_system.administrators import is_administrator
    from management_portal.society_service import application_target_configuration

    return {"configuration": application_target_configuration.get("tau"),
            "can_manage": is_administrator(actor)}


def set_tau_admission(paused: bool, expected_revision: int, *, actor: str) -> dict[str, Any]:
    """Only Portal administrators may pause or resume acceptance of new campaigns."""
    from management_portal.permission_system.administrators import is_administrator
    from management_portal.society_service import application_target_configuration

    if not is_administrator(actor):
        raise PermissionError("需要 Portal 管理员身份")
    if type(paused) is not bool:
        raise ValueError("paused must be a boolean")
    return {"configuration": application_target_configuration.update(
        "tau", {"admission_paused": paused, "reason": "Test 页面暂停" if paused else ""},
        actor, expected_revision=expected_revision), "can_manage": True}


def query(filters: dict[str, Any] | None = None, *, actor: str = "") -> dict[str, Any]:
    return service(refresh_runtime=True).query(filters, actor=actor)


def query_workspace(filters: dict[str, Any] | None = None, *, actor: str = "") -> dict[str, Any]:
    return service(refresh_runtime=True).query_workspace(filters, actor=actor)


def query_overview(*, actor: str = "") -> dict[str, Any]:
    return service(refresh_runtime=True).query_overview(actor=actor)


def query_page(filters: dict[str, Any] | None = None, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    return service().query_page(filters, offset=offset, limit=limit)


def query_case_ids(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    return service().query_case_ids(filters)


def implementation_source(
    case_id: str,
    implementation_id: str,
    *,
    actor: str,
) -> dict[str, Any]:
    return service().implementation_source(
        case_id,
        implementation_id,
        actor=actor,
    )


def case_definition(case_id: str) -> dict[str, Any]:
    return service().catalog.get(case_id).public()


def case_for_design_point(point_ref: str) -> dict[str, Any] | None:
    from management_portal.test_framework.models import CatalogError, design_point_ref

    if not design_point_ref(point_ref):
        raise CatalogError("精确点查询必须提供 design_point_ref")
    cases = service().catalog.query({"design_point_ref": point_ref})
    if len(cases) > 1:
        raise CatalogError(f"设计点 {point_ref} 存在重复绑定")
    return cases[0].public() if cases else None


def preview(request: dict[str, Any], *, actor: str) -> dict[str, Any]:
    return service(refresh_runtime=True).preview(request, actor=actor)


def start(request: dict[str, Any], *, actor: str) -> dict[str, Any]:
    return service(refresh_runtime=True).start(request, actor=actor)


def get_run(run_id: str, *, actor: str) -> dict[str, Any]:
    return service().get_run(run_id, actor=actor)


def cancel_run(run_id: str, *, actor: str) -> dict[str, Any]:
    return service().cancel_run(run_id, actor=actor)


def delete_runs(run_ids: list[str], *, actor: str) -> dict[str, Any]:
    return service().delete_runs(run_ids, actor=actor)


def rotate_tau_resident(module_id: str, *, actor: str, backend_route_id: str = "") -> dict[str, Any]:
    return service().rotate_tau_resident(module_id, actor=actor, backend_route_id=backend_route_id)


def configure_tau_residents(module_ids: list[str], *, actor: str, backend_route_id: str, fast: bool) -> dict[str, Any]:
    return service().configure_tau_residents(module_ids, actor=actor, backend_route_id=backend_route_id, fast=fast)


def tau_resident_status(module_ids: list[str]) -> dict[str, Any]:
    return service().tau_resident_status(module_ids)
