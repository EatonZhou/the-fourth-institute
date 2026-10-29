"""Project installed system Events into local execution policy.

Event runtime owns the switch. These fixed bindings require no graph scan or
reverse RPC. Always re-read under execution authority after releasing state
locks, including when replaying an older definition's projection receipt.
"""
from __future__ import annotations

from typing import Any
import re

from management_common.projection_receipts import project_committed
from management_common.site_config import read_site_config
from management_portal.execution.service_settings import ExecutionSettings

from . import invariant_locks, lineage_program_runtime
from . import lineage_aggregate


WORKFLOW_FIELDS = {
    "system:request-timing:enabled": ("request_timing_lineage_id", "model_request_timing_enabled"),
    "system:harness-recording:enabled": ("harness_recording_lineage_id", "harness_recording_enabled"),
}
PROJECTION = "lineage.system_workflow_switches"


def workflow_switches() -> dict[str, tuple[str, str]]:
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict) or not isinstance(deployment.get("system_workflow", {}), dict):
        raise ValueError("deployment.system_workflow must be an object")
    config = deployment.get("system_workflow", {})
    result = {}
    for key, (option, field) in WORKFLOW_FIELDS.items():
        owner = config.get(option, "")
        if not isinstance(owner, str) or (owner and not re.fullmatch(r"lineage_[0-9a-f]{12}", owner)):
            raise ValueError(f"deployment.system_workflow.{option} must be a Lineage ID")
        if owner:
            result[key] = (owner, field)
    return result


def _keys(lineage_id: str) -> set[str]:
    return {key for key, (owner, _field) in workflow_switches().items() if owner == lineage_id}


def current_definition(lineage_id: str) -> dict[str, Any]:
    try:
        return lineage_aggregate.lineage_catalog_store().get(lineage_id)
    except KeyError:
        return {}


def bound_components(definition: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [component for component in (definition.get("program") or {}).get("components") or []
            if component.get("client_key") == key]


def sync_lineage(lineage_id: str, *, known_keys: tuple[str, ...] = ()) -> dict[str, bool]:
    """Synchronously converge current Event state, including deleted bindings."""
    keys = _keys(lineage_id)
    if not keys:
        return {}
    if invariant_locks.lock_held():
        raise RuntimeError("Workflow projection must run after releasing Lineage state locks")

    with lineage_aggregate.execution_authority(lineage_id):
        settings = ExecutionSettings()
        managed = settings.managed_workflow_keys() | set(known_keys)
        definition = current_definition(lineage_id)
        runtime = None
        for key in sorted(keys):
            matches = bound_components(definition, key)
            if not matches and key not in managed:
                continue  # Never installed: preserve the legacy setting.
            enabled = False
            if len(matches) == 1 and matches[0].get("kind") == "event_source":
                if runtime is None:
                    runtime = lineage_program_runtime.read_runtime(lineage_id)
                state = runtime["event_sources"].get(str(matches[0]["id"])) or {}
                enabled = not bool(state.get("paused"))
            # Missing or malformed installed bindings fail closed.
            settings.project_workflow_switch(key, enabled)
        return settings.get()


def project_after_commit(lineage_id: str, *, known_keys: tuple[str, ...] = ()) -> None:
    if not _keys(lineage_id):
        return
    project_committed(
        {"projection": PROJECTION, "lineage_id": lineage_id, "known_keys": list(known_keys),
         "committed_revision": None},
        lambda: sync_lineage(lineage_id, known_keys=known_keys),
    )


def defer_definition_projection(lineage_id: str, *definitions: dict[str, Any] | None) -> None:
    keys = _keys(lineage_id)
    if not keys:
        return
    # Keep only installation provenance; never capture an old enabled value.
    known = tuple(sorted(key for key in keys if any(
        bound_components(definition or {}, key) for definition in definitions
    )))
    invariant_locks.defer_until_state_unlock(
        lambda: project_after_commit(lineage_id, known_keys=known),
    )


def restore() -> None:
    for lineage_id in sorted({owner for owner, _field in workflow_switches().values()}):
        sync_lineage(lineage_id)
