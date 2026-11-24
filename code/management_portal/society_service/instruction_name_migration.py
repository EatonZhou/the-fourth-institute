"""Normalize executable Society definitions at the ISA cutover."""

from copy import deepcopy
import json
import re

from management_portal.hatg.instruction_renames import INSTRUCTION_RENAMES

from . import lineage_effect_script


_NAME = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(INSTRUCTION_RENAMES) + r")(?![A-Za-z0-9_])"
)
_PROMPT_FIELDS = {"guide", "instructions", "message", "prompt", "system_prompt", "task"}
_REVIEW_FIELDS = {"script_draft"}


def normalize_prompt(text):
    return _NAME.sub(lambda match: INSTRUCTION_RENAMES[match[0]], text)


def normalize_script(source):
    if not source:
        return source
    try:
        calls = lineage_effect_script.parse(source).calls
    except lineage_effect_script.ScriptSyntaxError:
        # Definitions can store half-written scripts. Leave their syntax and
        # diagnostics intact until the script is made runnable.
        return source
    return lineage_effect_script.rewrite_call_source(
        source, calls, INSTRUCTION_RENAMES, normalize_prompt,
    )


def _normalize_fields(value, *, prompt=False, script=False, instruction=False):
    if isinstance(value, list):
        return [_normalize_fields(item, prompt=prompt, script=script, instruction=instruction)
                for item in value]
    if isinstance(value, dict):
        keys = {"value", "default", "concat"}
        return {
            key: item if key in _REVIEW_FIELDS else _normalize_fields(
                item,
                prompt=key in _PROMPT_FIELDS or (prompt and key in keys),
                script=key == "script" or (script and key in keys),
                instruction=key == "instruction" or (instruction and key in keys),
            )
            for key, item in value.items()
        }
    if isinstance(value, str):
        if script:
            return normalize_script(value)
        if prompt:
            return normalize_prompt(value)
        if instruction:
            return INSTRUCTION_RENAMES.get(value, value)
    return value


def normalize_component_config(config):
    return _normalize_fields(config)


def normalize_wire_config(config):
    return _normalize_fields(config)


def normalize_template(template):
    return {**template, "body": _normalize_fields(template["body"])}


def _normalize_definition(definition):
    program = definition["program"]
    changed_effects = set()
    effect_ids = set()
    for component in program["components"]:
        before = component.get("config")
        if isinstance(before, dict):
            component["config"] = normalize_component_config(before)
        if component["kind"] == "effect":
            effect_ids.add(component["id"])
            if component.get("config") != before:
                changed_effects.add(component["id"])
    affected_wires = {}
    for wire in program["wires"]:
        before = wire.get("config")
        if isinstance(before, dict):
            wire["config"] = normalize_wire_config(before)
        target = wire["target"]
        if target in effect_ids and (target in changed_effects or wire.get("config") != before):
            affected_wires[wire["id"]] = target
    for block in definition.get("function_blocks", ()):
        memory = (block.get("duty") or {}).get("memory") or {}
        if isinstance(memory.get("prompt"), str):
            memory["prompt"] = normalize_prompt(memory["prompt"])
    return changed_effects, affected_wires


def _blockers(connection, lineage_id, effects, wires):
    from . import domain_work_storage, program_work_storage, trigger_path_storage

    if not effects and not wires:
        return []
    blocked = []
    for key, record in domain_work_storage.list_work(connection, "effect_work", lineage_id).items():
        effect_id = record.get("effect_id")
        source_wire_id = record.get("source_wire_id")
        if (effect_id in effects or source_wire_id in wires or not effect_id
                or not source_wire_id and effect_id in wires.values()):
            step = trigger_path_storage.effect_step_status(connection, lineage_id, key)
            calls = [
                {"position": position, "status": call.get("status") or "unknown",
                 "instruction_id": call.get("instruction_id") or ""}
                for position, call in (record.get("calls") or {}).items() if isinstance(call, dict)
            ]
            blocked.append({
                "kind": "effect_activation", "lineage_id": lineage_id,
                "effect_id": effect_id, "activation_key": key, "wire_id": source_wire_id or "",
                "journal_status": record.get("status") or "unknown",
                "trigger_path_id": step.get("trigger_path_id") or record.get("trigger_path_id") or "",
                "step_status": step.get("status") or "unknown",
                "calls": calls,
            })
    if not wires:
        return blocked
    runtime = program_work_storage.read(connection, lineage_id)
    for wire_id, effect_id in wires.items():
        deliveries = runtime.get("event_wires", {}).get(wire_id, {}).get("pending_events", ())
        if deliveries:
            blocked.append({
                "kind": "pending_event_deliveries", "count": len(deliveries),
                "lineage_id": lineage_id, "wire_id": wire_id, "effect_id": effect_id,
                "deliveries": [_delivery_status(delivery) for delivery in deliveries],
            })
    for operator_id, state in runtime.get("operators", {}).items():
        for pending in state.get("pending_deliveries", {}).values():
            for wire_id in sorted(wires.keys() & set(pending.get("output_wire_ids", ()))):
                blocked.append({"kind": "pending_operator_delivery", "lineage_id": lineage_id,
                                "operator_id": operator_id, "wire_id": wire_id, "effect_id": wires[wire_id]})
    return blocked


def _delivery_status(delivery):
    hold = delivery.get("recovery_blocked") or {}
    event = delivery.get("event") or {}
    return {
        "delivery_id": delivery.get("claim_token") or "",
        "trigger_path_id": hold.get("trigger_path_id") or (event.get("payload") or {}).get("trigger_path_id") or "",
        "activation_key": hold.get("activation_key") or "",
        "instruction_id": hold.get("instruction_id") or "",
        "status": "blocked" if hold else "dispatching" if delivery.get("dispatch_token") else "waiting",
        "requires_resolution": hold.get("requires_resolution") is True,
    }


def _report(blocked):
    return {"blocked_count": sum(item.get("count", 1) for item in blocked), "blockers": blocked}


def check():
    """Read owner checkpoints and report IDs only, before stopping the old owner."""
    from psycopg import IsolationLevel, connect, sql
    from psycopg.rows import dict_row

    from management_common.portal_database import portal_database
    from management_common.postgres_database import PostgresConnection

    from . import lineage_data_definitions, lineage_metadata_storage

    target = portal_database(schema="lineage")
    blocked = []
    # An ordinary owner store initializes its schema on connection. Use an
    # unpooled read-only snapshot so preflight is safe with the old owner live.
    with connect(target.dsn, row_factory=dict_row) as raw:
        raw.read_only = True
        raw.isolation_level = IsolationLevel.REPEATABLE_READ
        raw.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(target.schema)))
        connection = PostgresConnection(raw)
        for record in lineage_metadata_storage.read_many(connection):
            lineage_id = record["lineage_id"]
            definition = lineage_data_definitions.definition_from_connection(connection, lineage_id)
            effects, wires = _normalize_definition(definition)
            blocked.extend(_blockers(connection, lineage_id, effects, wires))
    return _report(blocked)


def _require_clear(report):
    if report["blocked_count"]:
        raise RuntimeError("ISA name migration blocked by unsettled Effect work: " + json.dumps(report))


def _migrate_lineage(lineage_id):
    from . import lineage_aggregate, lineage_state

    catalog = lineage_aggregate.lineage_catalog_store()

    def migrate(connection):
        if catalog.state_store.lock_lineage_definition(lineage_id) is None:
            return
        before = lineage_aggregate.read_lineage_state(lineage_id)
        after = deepcopy(before)
        effects, wires = _normalize_definition(after)
        if after != before:
            _require_clear(_report(_blockers(connection, lineage_id, effects, wires)))
            lineage_state._persist_lineage_state_transaction(
                before, after, "Normalize ISA instruction names",
                synchronize_resource_identities=False,
                synchronize_resident_identities=False,
            )
            return True
        return False

    with lineage_aggregate.lineage_scope(lineage_id, operation="normalize_isa_instruction_names"):
        if catalog.state_store.database.write(migrate):
            lineage_state.invalidate_lineage_read_cache(lineage_id)


def migrate():
    """Complete before any Society runtime worker can read the definitions."""
    from . import lineage_aggregate, resident_runtime_domain
    from .templates import store

    _require_clear(check())
    catalog = lineage_aggregate.lineage_catalog_store()
    for record in catalog.listing_records():
        _migrate_lineage(record["lineage_id"])
    resident_runtime_domain.migrate_instruction_names()
    store.migrate_instruction_names()
