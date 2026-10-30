"""Execution contract snapshots and change impact for typed Programs.

Definition edits change subscriber contracts here; runtime execution consumes
these immutable fingerprints and impact summaries.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from management_common.limit_contract import normalize_optional_limit
from . import lineage_program_compilation, lineage_program_event_scope, lineage_program_vocabulary
from .lineage_values import _clone_json


# Dispatch-only fields invalidate pending delivery contracts without resetting source progress.
_EVENT_CONTRACT_FIELDS = {
    'component_id': (True, lambda event_source: str(event_source.get('component_id') or '')),
    'source_port': (True, lambda event_source: str(event_source.get('source_port') or '')),
    'event_type': (False, lambda event_source: str(event_source.get('event_type') or '')),
    'output_schema': (True, lambda event_source: _clone_json(event_source.get('output_schema') or {})),
    'max_fires': (True, lambda event_source: normalize_optional_limit(event_source.get('max_fires'))),
    'subject_resident_id': (False, lambda event_source: '' if lineage_program_event_scope.watches_many_residents(event_source) else str(event_source.get('subject_resident_id') or '')),
    'subject_event_source_id': (True, lambda event_source: str(event_source.get('subject_event_source_id') or '')),
    'subject_wire_id': (True, lambda event_source: str(event_source.get('subject_wire_id') or '')),
    'subject_effect_id': (True, lambda event_source: str(event_source.get('subject_effect_id') or '')),
    'state_domain': (False, lambda event_source: str(event_source.get('state_domain') or '')),
    'threshold_percent': (False, lambda event_source: event_source.get('threshold_percent')),
    'duration_seconds': (False, lambda event_source: event_source.get('duration_seconds')),
    'pressure_metric': (False, lambda event_source: str(event_source.get('pressure_metric') or '')),
    'queue_length_threshold': (False, lambda event_source: event_source.get('queue_length_threshold')),
    'wait_seconds_threshold': (False, lambda event_source: event_source.get('wait_seconds_threshold')),
    'key': (False, lambda event_source: str(event_source.get('key') or '')),
    'operator': (False, lambda event_source: str(event_source.get('operator') or '')),
    'expected_value': (False, lambda event_source: _clone_json(event_source.get('expected_value'))),
    'fire_on_initial_match': (False, lambda event_source: bool(event_source.get('fire_on_initial_match'))),
    'resident_scope': (False, lambda event_source: str(event_source.get('resident_scope') or '')),
    'resident_ids': (False, lambda event_source: sorted((str(value) for value in event_source.get('resident_ids') or [] if str(value)))),
    'path': (False, lambda event_source: str(event_source.get('path') or '')),
    'schedule_mode': (False, lambda event_source: str(event_source.get('schedule_mode') or '')),
    'scheduled_at': (False, lambda event_source: str(event_source.get('scheduled_at') or '')),
    'delay_seconds': (False, lambda event_source: event_source.get('delay_seconds')),
    'recurrence_kind': (False, lambda event_source: str(event_source.get('recurrence_kind') or '')),
    'interval_seconds': (False, lambda event_source: event_source.get('interval_seconds')),
    'timezone': (False, lambda event_source: str(event_source.get('timezone') or '')),
    'calendar_frequency': (False, lambda event_source: str(event_source.get('calendar_frequency') or '')),
    'calendar_time': (False, lambda event_source: str(event_source.get('calendar_time') or '')),
    'calendar_weekdays': (False, lambda event_source: _clone_json(event_source.get('calendar_weekdays') or [])),
    'calendar_day': (False, lambda event_source: event_source.get('calendar_day')),
    'misfire_policy': (False, lambda event_source: str(event_source.get('misfire_policy') or '')),
    'overlap_policy': (False, lambda event_source: str(event_source.get('overlap_policy') or '')),
    'jitter_seconds': (False, lambda event_source: event_source.get('jitter_seconds')),
    'subscriber_routes': (True, lambda event_source: sorted(((str(item.get('wire_id') or ''), str(item.get('target_id') or ''), str(item.get('target_kind') or ''), json.dumps(item.get('config') or {}, ensure_ascii=False, sort_keys=True)) for item in event_source.get('subscriber_routes') or [] if isinstance(item, dict)))),
}


def _program_event_execution_configs(
    plan: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        str(event_source_id): {
            **{
                key: _clone_json(event_source.get(key))
                for key, (dispatch_only, _normalize) in _EVENT_CONTRACT_FIELDS.items()
                if not dispatch_only and key != "resident_ids" and key in event_source
                # Publication backlog policy changes delivery, not the fact cursor.
                and not (key == "misfire_policy" and event_source.get("event_type") == "portal.release_published")
            },
            "resident_ids": sorted(
                str(value)
                for value in event_source.get("resident_ids") or []
                if str(value)
            ),
            # Several watched Residents: changing the set re-baselines the source.
            **(
                {"subject_resident_ids": list(lineage_program_event_scope.event_source_subject_ids(event_source))}
                if lineage_program_event_scope.watches_many_residents(event_source)
                else {}
            ),
        }
        for event_source_id, event_source in (
            plan.get("event_sources") or {}
        ).items()
        if isinstance(event_source, dict)
    }


def _program_invoke_wire_execution_configs(
    plan: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return every triggering wire that enters an Invoke port."""
    return {
        str(wire.get("id") or ""): {
            "kind": str(wire.get("kind") or ""),
            "source": str(wire.get("source") or ""),
            "target": str(wire.get("target") or ""),
            "config": _clone_json(wire.get("config") or {}),
        }
        for wire in plan.get("wires") or []
        if (
            isinstance(wire, dict)
            and str(wire.get("kind") or "") in {"route"}
            and str(wire.get("id") or "")
        )
    }


def _invalid_previous_program_recovery_impact(
    previous: dict[str, Any],
    current: dict[str, Any],
    after_plan: dict[str, Any],
) -> dict[str, set[str]]:
    """Conservatively fence runtime owned by an unparseable old Program.

    A valid Graph repair must not depend on successfully compiling the broken
    state it replaces. We do not interpret or normalize removed authoring
    shapes here: stable IDs and typed endpoints are sufficient to forget every
    runtime cursor that could have belonged to the unknown contract.
    """

    raw_program = (
        previous.get("program")
        if isinstance(previous.get("program"), dict)
        else {}
    )
    raw_wires = [
        wire
        for wire in raw_program.get("wires") or []
        if isinstance(wire, dict)
    ]
    operator_ids = {
        str(component.get("id") or "")
        for lineage in (previous, current)
        for component in (
            (lineage.get("program") or {}).get("components") or []
        )
        if (
            isinstance(component, dict)
            and str(component.get("kind") or "")
            in lineage_program_vocabulary.PACKET_OPERATOR_KINDS
            and str(component.get("id") or "")
        )
    }
    raw_event_source_ids = {
        str(component.get("id") or "")
        for component in raw_program.get("components") or []
        if (
            isinstance(component, dict)
            and str(component.get("kind") or "") == "event_source"
            and str(component.get("id") or "")
        )
    }
    raw_invoke_wire_ids = {
        str(wire.get("id") or "")
        for wire in raw_wires
        if (
            str(wire.get("kind") or "") in {"route"}
            and str(wire.get("id") or "")
        )
    }
    return {
        "operator_ids": operator_ids,
        "loop_ids": set((after_plan.get("loops") or {}).keys()) | {
            str(component.get("id") or "")
            for component in raw_program.get("components") or []
            if isinstance(component, dict) and str(component.get("kind") or "") == "loop"
        },
        "event_source_ids": (
            raw_event_source_ids
            | set(_program_event_execution_configs(after_plan))
        ),
        "invoke_wire_ids": (
            raw_invoke_wire_ids
            | set(_program_invoke_wire_execution_configs(after_plan))
        ),
        "effect_ids": set((after_plan.get("effects") or {}).keys()) | {
            str(component.get("id") or "")
            for component in raw_program.get("components") or []
            if (
                isinstance(component, dict)
                and str(component.get("kind") or "") == "effect"
                and str(component.get("id") or "")
            )
        },
    }


def _program_execution_contract_change(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, set[str]]:
    """Diff the one compiled authority for queued HATG program work."""

    after_plan = lineage_program_compilation.compile_program(
        current.get("program"),
        current.get("nodes") or [],
    )
    try:
        before_plan = lineage_program_compilation.compile_program(
            previous.get("program"),
            previous.get("nodes") or [],
        )
    except ValueError:
        return _invalid_previous_program_recovery_impact(
            previous,
            current,
            after_plan,
        )
    before_operators = before_plan.get("packet_operators") or {}
    after_operators = after_plan.get("packet_operators") or {}
    changed_operator_ids = {
        operator_id
        for operator_id in set(before_operators) | set(after_operators)
        if before_operators.get(operator_id) != after_operators.get(operator_id)
    }
    before_events = _program_event_execution_configs(before_plan)
    after_events = _program_event_execution_configs(after_plan)
    changed_event_source_ids = {
        event_source_id
        for event_source_id in set(before_events) | set(after_events)
        if before_events.get(event_source_id)
        != after_events.get(event_source_id)
    }
    before_invoke_wires = _program_invoke_wire_execution_configs(before_plan)
    after_invoke_wires = _program_invoke_wire_execution_configs(after_plan)
    changed_invoke_wire_ids = {
        wire_id
        for wire_id in set(before_invoke_wires) | set(after_invoke_wires)
        if before_invoke_wires.get(wire_id)
            != after_invoke_wires.get(wire_id)
    }
    changed_recurring_source_ids = {
        event_source_id
        for event_source_id in changed_event_source_ids
        if any(
            str((configs.get(event_source_id) or {}).get("schedule_mode") or "")
            == "recurring"
            for configs in (before_events, after_events)
        )
    }
    # A queued occurrence carries the old schedule generation. Changing a
    # recurring source must therefore fence its outgoing wire backlog even
    # when the wire definition itself did not change.
    changed_invoke_wire_ids.update(
        wire_id
        for contracts in (before_invoke_wires, after_invoke_wires)
        for wire_id, contract in contracts.items()
        if (
            str(contract.get("kind") or "") == "route"
            and str(contract.get("source") or "")
            in changed_recurring_source_ids
        )
    )
    before_effects = before_plan.get("effects") or {}
    after_effects = after_plan.get("effects") or {}
    changed_effect_ids = {
        effect_id
        for effect_id in set(before_effects) | set(after_effects)
        if before_effects.get(effect_id) != after_effects.get(effect_id)
    }
    before_loops = before_plan.get("loops") or {}
    after_loops = after_plan.get("loops") or {}
    return {
        "operator_ids": changed_operator_ids,
        "loop_ids": {
            loop_id
            for loop_id in set(before_loops) | set(after_loops)
            if loop_id not in before_loops or loop_id not in after_loops
        },
        "event_source_ids": changed_event_source_ids,
        "invoke_wire_ids": changed_invoke_wire_ids,
        "effect_ids": changed_effect_ids,
    }


def _program_event_source_contract_fingerprint(
    event_source: dict[str, Any],
) -> str:
    """Fingerprint the source locator and exact subscriber contract."""

    contract = {
        **{key: normalize(event_source) for key, (_, normalize) in _EVENT_CONTRACT_FIELDS.items()},
        **(({'subject_resident_ids': list(lineage_program_event_scope.event_source_subject_ids(event_source))} if lineage_program_event_scope.watches_many_residents(event_source) else {})),
        **(({'subject_effect_ids': list(event_source.get('subject_effect_ids') or []), 'outcome': str(event_source.get('outcome') or 'any')} if event_source.get('event_type') == 'script.finished' else {})),
    }
    return hashlib.sha256(
        json.dumps(
            contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
