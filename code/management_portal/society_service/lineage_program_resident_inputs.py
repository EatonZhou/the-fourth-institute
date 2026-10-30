"""Program deliveries to Residents: their handoffs in trigger paths, the profile each
delivery runs with, and settling the trigger paths they belong to.
"""

from __future__ import annotations

from typing import Any

from . import lineage_program_runtime, program_work_storage, trigger_paths
from .lineage_program_runtime import _delivery_profile, _iso_now, _item_id
from .state_store import get_lineage_data_store


def record_delivery_profile(lineage_id: str, message_id: str, profile: dict[str, Any]) -> None:
    """Remember how the Resident should run one Event delivery."""

    clean_message_id = _item_id(message_id, field="delivery message id")
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        profiles = runtime.setdefault("delivery_profiles", {})
        profiles.pop(clean_message_id, None)
        profiles[clean_message_id] = _delivery_profile(profile)
    cleanup_delivery_profiles(lineage_id=lineage_id)


def cleanup_delivery_profiles(*, lineage_id: str = "", message_id: str = "") -> int:
    """Reclaim configurations after all readers of the terminal input have finished."""
    return get_lineage_data_store().database.write(
        lambda connection: program_work_storage.prune_delivery_profiles(
            connection, lineage_id=lineage_id, message_id=message_id,
        )
    )


def delivery_profile(lineage_id: str, message_id: str) -> dict[str, Any]:
    """How the Resident runs one Event delivery; empty for any other input."""

    clean_lineage_id = str(lineage_id or "").strip()
    clean_message_id = str(message_id or "").strip()
    if not clean_lineage_id or not clean_message_id:
        return {}
    return dict(lineage_program_runtime._read_object(clean_lineage_id, "delivery_profiles", clean_message_id) or {})


def register_resident_handoff(lineage_id: str, *, message_id: str, resident_id: str,
                                  trigger_path_id: str, event: dict[str, Any], source_wire_id: str,
                                  queued_at: str = "") -> dict[str, Any]:
    return trigger_paths.admit_resident(lineage_id, message_id=message_id, resident_id=resident_id,
        trigger_path_id=trigger_path_id, event=event, source_wire_id=source_wire_id, queued_at=queued_at)



def settle_resident_handoffs(lineage_id: str, *, message_id: str, resident_id: str,
                                status: str, completed_at: str = "", output: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows = trigger_paths.settle_input(lineage_id, resident_id, message_id, status=status,
        at=completed_at or _iso_now(), execution_id=str((output or {}).get("execution_id") or ""))
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        runtime["delivery_profiles"].pop(message_id, None)
    return rows



def pending_resident_handoffs(lineage_id: str) -> list[dict[str, Any]]:
    return trigger_paths.resident_inputs(lineage_id, pending=True)



def resident_handoff(lineage_id: str, resident_id: str, message_id: str) -> dict[str, Any] | None:
    return trigger_paths.resident_input(lineage_id, resident_id, message_id)



def path_resident_inputs(lineage_id: str, trigger_path_id: str) -> list[dict[str, Any]]:
    return trigger_paths.resident_inputs(lineage_id, paths=[trigger_path_id])



def settle_trigger_path(lineage_id: str, trigger_path_id: str) -> bool:
    finished = trigger_paths.settle(trigger_path_id)
    if finished:
        from .changes import lineage_changed
        lineage_changed(lineage_id, runtime=True)
    return finished
