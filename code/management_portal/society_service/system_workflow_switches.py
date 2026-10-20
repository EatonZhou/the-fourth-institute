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
from management_portal.hatg.contracts import InstructionError

from . import invariant_locks, lineage_program_runtime


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


def _definition(lineage_id: str) -> dict[str, Any]:
    from . import lineage_domain

    try:
        return lineage_domain._lineage_catalog_store().get(lineage_id)
    except KeyError:
        return {}


def _matches(definition: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [component for component in (definition.get("program") or {}).get("components") or []
            if component.get("client_key") == key]


def sync_lineage(lineage_id: str, *, known_keys: tuple[str, ...] = ()) -> dict[str, bool]:
    """Synchronously converge current Event state, including deleted bindings."""
    keys = _keys(lineage_id)
    if not keys:
        return {}
    if invariant_locks.lock_held():
        raise RuntimeError("Workflow projection must run after releasing Lineage state locks")
    from . import lineage_domain

    with lineage_domain._lineage_execution_authority(lineage_id):
        settings = ExecutionSettings()
        managed = settings.managed_workflow_keys() | set(known_keys)
        definition = _definition(lineage_id)
        runtime = None
        for key in sorted(keys):
            matches = _matches(definition, key)
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
        _matches(definition or {}, key) for definition in definitions
    )))
    invariant_locks.defer_until_state_unlock(
        lambda: project_after_commit(lineage_id, known_keys=known),
    )


def restore() -> None:
    for lineage_id in sorted({owner for owner, _field in workflow_switches().values()}):
        sync_lineage(lineage_id)


def update_settings(changes: dict[str, bool], actor: str) -> dict[str, bool]:
    """Route installed settings through the same public component switch owner."""
    from . import component_runtime, lineage_domain

    bindings = workflow_switches()
    requested = {key: (lineage_id, field) for key, (lineage_id, field) in bindings.items()
                 if field in changes}
    with lineage_domain._lineage_execution_authorities({owner for owner, _field in requested.values()}):
        settings = ExecutionSettings()
        managed = settings.managed_workflow_keys()
        installed: list[tuple[str, str, bool]] = []
        unmanaged: dict[str, bool] = {}
        # Fresh installations may have no workflow yet. Once a key was managed,
        # removing its deployment binding must not bypass its Event authority.
        for key, (_option, field) in WORKFLOW_FIELDS.items():
            if field not in changes or key in bindings:
                continue
            if key in managed:
                raise InstructionError("instruction.conflict", "系统开关部署绑定缺失", details={"client_key": key})
            unmanaged[field] = changes[field]
        # Validate every requested binding before changing either setting.
        for key, (lineage_id, field) in requested.items():
            matches = _matches(_definition(lineage_id), key)
            if len(matches) == 1 and matches[0].get("kind") == "event_source":
                lineage_domain._read_lineage(lineage_id, actor=actor)
                installed.append((lineage_id, str(matches[0]["id"]), changes[field]))
            elif matches or key in managed:
                raise InstructionError(
                    "instruction.conflict", "系统开关 Event 已删除或绑定无效，请先恢复 Lineage 中的开关节点",
                    details={"lineage_id": lineage_id, "client_key": key,
                             "binding_state": "invalid" if matches else "missing"},
                )
            else:
                unmanaged[field] = changes[field]
        if unmanaged:
            settings.update_unmanaged(unmanaged)
        for lineage_id, component_id, enabled in installed:
            component_runtime.set_paused(lineage_id, component_id, not enabled, actor)
        return settings.get()
