"""Migrate current Society definitions when ISA contracts change.

Name-only and argument-compatible changes share this owner. Operations moved
out of ISA cannot be substituted with asynchronous ScriptRun handles: their
owners must supply an equivalent workflow, or preflight stops with locations.
Execution journals, approved drafts and published template history are never
inputs to a rewrite.
"""

from copy import deepcopy
import json
import re

from management_portal import portal_features
from management_portal.hatg.instruction_renames import INSTRUCTION_RENAMES

from . import lineage_effect_script


_POST_MARKS = {"SetPostPinned": "UpdatePostMarks", "SetPostFeatured": "UpdatePostMarks"}
# These are migration directions, not instruction aliases or script adapters.
# The owning domain decides the replacement workflow and its result handling.
_REMOVED = {
    "PublishRelease": "RunScript（Portal 运维的 publish_release 脚本；用 GetScriptRun 读取发布结果）",
    "InstallProviderRuntime": "RunScript（Portal 运维的 install_provider_runtime 脚本）",
    "SetColabSessionRunning": "RunScript（Portal 运维的 colab_session 脚本）",
    "CheckArticleRestore": "RunScript（Portal 运维的 article_restore 脚本 check 步骤；用 GetScriptRun 读取预检结果）",
    "RestoreArticle": "RunScript（Portal 运维的 article_restore 脚本 apply 步骤；用 GetScriptRun 读取恢复结果）",
    "GetResearchMapDigest": "GetResearchMap 与 ListResearchMapChanges（由 Project 流程汇总修订和产出变化）",
    "GetKnowledgeTags": "ReadResource（知识标签配置资源，读取正文和版本）",
    "UpdateKnowledgeTags": "UpdateResource（知识标签配置资源，带 expected_version 整表替换正文）",
}
_OBJECT_ARGUMENTS = {
    "UpdateProviderAccountConfiguration": ("model_provider", "changes"),
    "CreateProviderAccountLogin": ("region", "options"),
}
_PROVIDER_GUIDANCE = {
    "UpdateProviderAccountConfiguration": "（配置字段放在 changes 对象中）",
    "CreateProviderAccountLogin": "（登录选项放在 options 对象中）",
}
_NAME = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(
        (*INSTRUCTION_RENAMES, *_POST_MARKS, *_REMOVED, *_PROVIDER_GUIDANCE)
    ) + r")(?![A-Za-z0-9_])"
)
_PROMPT_FIELDS = {"guide", "instructions", "message", "prompt", "system_prompt", "task"}
_REVIEW_FIELDS = {"script_draft"}


class DefinitionMigrationRequired(ValueError):
    """Locations and remediation only; never include a script or argument payload."""

    def __init__(self, issues):
        self.issues = issues
        super().__init__("ISA definition migration requires owner resolution: " + json.dumps(issues))


def _current_name(name):
    name = INSTRUCTION_RENAMES.get(name, name)
    return _POST_MARKS.get(name, name)


def _post_mark_conflict(instruction, arguments):
    # Do not turn an invalid old single-mark call into a valid dual-mark write.
    legacy = INSTRUCTION_RENAMES.get(instruction, instruction)
    return (legacy == "SetPostPinned" and "featured" in arguments
            or legacy == "SetPostFeatured" and "pinned" in arguments)


def _issue(instruction, reason, *, line=None, path=()):
    canonical = _current_name(instruction)
    issue = {
        "instruction": instruction, "reason": reason, "path": list(path),
        "action": _REMOVED.get(canonical, "Update the owning definition to the current argument contract"),
    }
    if line is not None:
        issue["line"] = line
    return issue


def normalize_prompt(text):
    def replacement(match):
        name = _current_name(match[0])
        if name in _REMOVED:
            return _REMOVED[name]
        guidance = _PROVIDER_GUIDANCE.get(name, "")
        if guidance and text[match.end():].startswith(guidance):
            guidance = ""
        return name + guidance

    return _NAME.sub(replacement, text)


def normalize_script(source):
    if not source:
        return source
    # Optional domain owners replace complete original workflows through the
    # feature contract. Custom or unowned variants keep the explicit diagnostics
    # below; Society does not load feature implementations directly.
    source = portal_features.migrate_workflow_script(source)
    try:
        calls = lineage_effect_script.parse(source).calls
    except lineage_effect_script.ScriptSyntaxError:
        # Definitions can store half-written scripts. Leave their syntax and
        # diagnostics intact until the script is made runnable.
        return source
    replacements = {}
    issues = []
    for call in calls:
        name = _current_name(call.instruction)
        arguments = {argument.name: argument for argument in call.arguments}
        if _post_mark_conflict(call.instruction, arguments):
            issues.append(_issue(call.instruction, "old_single_mark_arguments_conflict", line=call.line))
            continue
        if name in _REMOVED:
            issues.append(_issue(call.instruction, "operation_moved_out_of_isa", line=call.line))
            continue
        fields = _OBJECT_ARGUMENTS.get(name)
        if fields is None:
            continue
        old, new = fields
        if old not in arguments:
            continue
        if new in arguments:
            issues.append(_issue(call.instruction, "old_and_new_arguments_conflict", line=call.line))
            continue
        argument = arguments[old]
        try:
            replacements[argument.offset] = lineage_effect_script.object_argument(new, old, argument.value)
        except ValueError:
            issues.append(_issue(call.instruction, "argument_requires_intermediate_variable", line=call.line))
    if issues:
        raise DefinitionMigrationRequired(issues)
    return lineage_effect_script.rewrite_call_source(
        source, calls, {call.instruction: _current_name(call.instruction) for call in calls
                        if call.instruction != _current_name(call.instruction)}, normalize_prompt,
        arguments=lambda _call, argument: replacements.get(argument.offset),
    )


def _normalize_fields(value, *, prompt=False, script=False, instruction=False):
    if isinstance(value, list):
        result, issues = [], []
        for index, item in enumerate(value):
            try:
                result.append(_normalize_fields(item, prompt=prompt, script=script, instruction=instruction))
            except DefinitionMigrationRequired as error:
                issues.extend({**issue, "path": [index, *issue["path"]]} for issue in error.issues)
        if issues:
            raise DefinitionMigrationRequired(issues)
        return result
    if isinstance(value, dict):
        if isinstance(value.get("arguments"), dict) and isinstance(value.get("instruction"), str):
            if _post_mark_conflict(value["instruction"], value["arguments"]):
                raise DefinitionMigrationRequired([_issue(value["instruction"], "old_single_mark_arguments_conflict")])
        keys = {"value", "default", "concat"}
        result, issues = {}, []
        for key, item in value.items():
            try:
                result[key] = item if key in _REVIEW_FIELDS else _normalize_fields(
                    item,
                    prompt=key in _PROMPT_FIELDS or (prompt and key in keys),
                    script=key == "script" or (script and key in keys),
                    instruction=key == "instruction" or (instruction and key in keys),
                )
            except DefinitionMigrationRequired as error:
                issues.extend({**issue, "path": [key, *issue["path"]]} for issue in error.issues)
        if issues:
            raise DefinitionMigrationRequired(issues)
        fields = _OBJECT_ARGUMENTS.get(result.get("instruction")) if isinstance(result.get("instruction"), str) else None
        arguments = result.get("arguments")
        if fields and isinstance(arguments, dict) and fields[0] in arguments:
            old, new = fields
            if new in arguments:
                raise DefinitionMigrationRequired([_issue(result["instruction"], "old_and_new_arguments_conflict")])
            result["arguments"] = {key: item for key, item in arguments.items() if key != old}
            result["arguments"][new] = {old: arguments[old]}
        return result
    if isinstance(value, str):
        if script:
            return normalize_script(value)
        if prompt:
            return normalize_prompt(value)
        if instruction:
            name = _current_name(value)
            if name in _REMOVED:
                raise DefinitionMigrationRequired([_issue(value, "operation_moved_out_of_isa")])
            return name
    return value


def normalize_component_config(config):
    return _normalize_fields(config)


def normalize_wire_config(config):
    return _normalize_fields(config)


def normalize_template(template):
    return {**template, "body": _normalize_fields(template["body"])}


def _normalize_definition(definition, *, diagnostics=None):
    def normalize(config, identity, normalizer):
        try:
            return normalizer(config)
        except DefinitionMigrationRequired as error:
            if diagnostics is None:
                raise
            diagnostics.extend({"kind": "definition_contract", **identity, **issue} for issue in error.issues)
            return config

    program = definition["program"]
    changed_effects = set()
    effect_ids = set()
    for component in program["components"]:
        before = component.get("config")
        if isinstance(before, dict):
            component["config"] = normalize(before, {"component_id": component["id"]}, normalize_component_config)
        if component["kind"] == "effect":
            effect_ids.add(component["id"])
            if component.get("config") != before:
                changed_effects.add(component["id"])
    affected_wires = {}
    for wire in program["wires"]:
        before = wire.get("config")
        if isinstance(before, dict):
            wire["config"] = normalize(before, {"wire_id": wire["id"]}, normalize_wire_config)
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
            diagnostics = []
            effects, wires = _normalize_definition(definition, diagnostics=diagnostics)
            blocked.extend({"lineage_id": lineage_id, **issue} for issue in diagnostics)
            blocked.extend(_blockers(connection, lineage_id, effects, wires))
        # Current, editable templates participate in the same preflight.
        # The template store's published snapshots and versions are evidence,
        # and are normalized only when installed into a current definition.
        template_table = sql.Identifier(portal_database(schema="templates").schema, "templates")
        if raw.execute("SELECT to_regclass(%s) AS relation", (template_table.as_string(raw),)).fetchone()["relation"]:
            for row in raw.execute(sql.SQL("SELECT template_id, body FROM {}").format(template_table)):
                try:
                    normalize_template(row)
                except DefinitionMigrationRequired as error:
                    blocked.extend({"kind": "template_contract", "template_id": row["template_id"], **issue}
                                   for issue in error.issues)
    return _report(blocked)


def _require_clear(report):
    if report["blocked_count"]:
        raise RuntimeError("ISA definition migration blocked by unresolved contracts or Effect work: " + json.dumps(report))


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
                before, after, "Migrate current ISA instruction contracts",
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
