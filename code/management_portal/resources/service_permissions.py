"""ResourceService permission instance: every live object's facts, and keeping the permission
schemes' instances up to date with them."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .service import ResourceService


LOGGER = logging.getLogger(__name__)


def _par_permissions():
    """The PAR implementation that keeps the instances, with the permission system on or off."""
    from management_portal import permission_system
    from management_portal.permission_system.par import PARPermissions

    return permission_system.active() if permission_system.mode() == permission_system.PAR else PARPermissions()


def permission_facts(service: "ResourceService") -> list:
    from management_portal.permission_system.facts import collect_portal_facts

    return collect_portal_facts(service)


def prepare_permissions(service: "ResourceService", *, actor: str) -> dict[str, Any]:
    service._require_mutation_actor(actor)
    from management_portal.permission_system.par import PARPermissions

    return PARPermissions().prepare_active(service.permission_facts())


def ensure_permission_instance(service: "ResourceService", *, max_removed: int) -> dict[str, Any] | None:
    permissions = _par_permissions()
    stale = permissions.stale_builtin()
    if not stale:
        return None
    facts = service.permission_facts()
    reports = {}
    for scheme_id in stale:
        try:
            report = permissions.follow_release(scheme_id, facts, max_removed=max_removed)
        except Exception as exc:  # noqa: BLE001 - reported per scheme, the others still follow
            LOGGER.exception("permission scheme %s: following the release failed", scheme_id)
            report = {"error": f"{exc.__class__.__name__}: {exc}"}
        if report is not None:
            reports[scheme_id] = report
    return reports or None


def merge_permission_sources(service: "ResourceService", *, max_removed: int) -> dict[str, Any]:
    facts: list = []

    def collect() -> list:
        if not facts:
            facts.extend(service.permission_facts())
        return facts

    return _par_permissions().follow_sources(collect, max_removed=max_removed)


def complete_permission_instance(service: "ResourceService", *, max_removed: int) -> dict[str, Any] | None:
    return _par_permissions().complete_active(service.permission_facts(),
                                             max_removed=max_removed)
