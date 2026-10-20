from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from management_common.actor_identity import canonical_actor_id
from management_portal import portal_features
from management_portal.society_service import get_society_service
from management_portal.shared_state import exclusive_file_lock


LOG = logging.getLogger(__name__)


def applications() -> tuple:
    """The installed Watchboard applications (``watchboard.application``), e.g. Telepus."""
    return tuple(part for _feature, part in portal_features.contributions("watchboard.application"))


@dataclass(frozen=True)
class ApplicationContext:
    resident_id: str
    actor: str
    dashboard: dict[str, Any]

    @property
    def root(self) -> Path:
        return Path(self.dashboard["script_root"]).resolve()


def project(payload: dict, actor: str) -> dict:
    dashboard = payload.get("watchboard") or {}
    if not dashboard.get("enabled"):
        return payload
    context = ApplicationContext(str(payload["resident_id"]), canonical_actor_id(actor), dashboard)
    result = dict(dashboard)
    for application in applications():
        try:
            if not application.matches(context):
                continue
            result.update(application.project(context))
            result["application_id"] = application.APPLICATION_ID
            break
        except Exception:
            LOG.exception("Watchboard application projection failed: %s / %s", application.APPLICATION_ID, context.resident_id)
            result["application_error"] = "工作台应用投影暂不可用"
    result["signature"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**payload, "watchboard": result}


def read(resident_id: str, actor: str) -> dict:
    return project(get_society_service().resident.get_resident_watchboard(resident_id, actor), actor)


def action(resident_id: str, actor: str, application_id: str, operation: str, arguments: dict) -> dict:
    actor = canonical_actor_id(actor)
    if not isinstance(arguments, dict):
        raise ValueError("Application arguments must be an object")
    application = next((item for item in applications() if item.APPLICATION_ID == application_id), None)
    if application is None:
        raise ValueError("Unknown Watchboard application")
    resident = get_society_service().resident

    def admitted(context) -> bool:
        # An application acting like the board's commands needs can_run (TriggerEvent on the
        # Resident's Lineage); one managing the caller's own data (Telepus contacts) only a signed-in caller.
        if not context.dashboard.get("enabled") or not actor:
            return False
        return not getattr(application, "ACTS_LIKE_COMMANDS", True) or bool(context.dashboard.get("permissions", {}).get("can_run"))

    payload = resident.get_resident_watchboard(resident_id, actor)
    context = ApplicationContext(resident_id, actor, payload.get("watchboard") or {})
    if not admitted(context):
        raise PermissionError("工作台应用操作需要认证身份和已启用的 Watchboard")
    root = context.root
    with exclusive_file_lock(root / ".portal-application.lock"):
        payload = resident.get_resident_watchboard(resident_id, actor)
        context = ApplicationContext(resident_id, actor, payload.get("watchboard") or {})
        if not admitted(context):
            raise PermissionError("工作台应用操作需要认证身份和已启用的 Watchboard")
        if context.root != root:
            raise ValueError("Watchboard binding changed; reload before retrying")
        if not application.matches(context):
            raise ValueError("Application does not match this Watchboard")
        result = application.act(context, operation, arguments)
    return {**read(resident_id, actor), **result}
