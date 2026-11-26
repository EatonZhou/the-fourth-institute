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
from . import isa_contract_migration as contracts


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
    **contracts.RETIRED,
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
        (*INSTRUCTION_RENAMES, *_POST_MARKS, *_REMOVED, *_PROVIDER_GUIDANCE, *contracts.RENAMES, *contracts.PROMPT_NAMES)
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
        return contracts.PROMPT_NAMES.get(name, contracts.RENAMES.get(name, name)) + guidance

    return _NAME.sub(replacement, text)


def normalize_script(source, *, script_path=None):
    _validate_script_path(script_path)
    if not source:
        return source
    from .family_definition import is_original_family_script

    if is_original_family_script(source):
        raise DefinitionMigrationRequired([_issue(
            "Family", "original_workflow_requires_family_graph_context",
        )])
    # Optional domain owners replace complete original workflows through the
    # feature contract. Custom or unowned variants keep the explicit diagnostics
    # below; Society does not load feature implementations directly.
    source = (portal_features.migrate_workflow_script(source) if script_path is None
              else portal_features.migrate_workflow_script(source, script_path=script_path))
    try:
        calls = lineage_effect_script.parse(source).calls
    except lineage_effect_script.ScriptSyntaxError:
        # Definitions can store half-written scripts. Leave their syntax and
        # diagnostics intact until the script is made runnable.
        return source
    replacements = {}
    names = {}
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
        try:
            wrapped = {key: contracts.Value(argument.value, True) for key, argument in arguments.items()}
            names[call.offset], current = contracts.rewrite(name, wrapped)
            additions = []
            for key, value in current.items():
                previous = next((old for old, original in wrapped.items() if original is value), None)
                if previous is None:
                    additions.append(value.argument(key))
                elif previous != key:
                    replacements[arguments[previous].offset] = value.argument(key)
            for key, value in wrapped.items():
                if not any(value is item for item in current.values()):
                    replacements[arguments[key].offset] = ""
            if additions:
                # Every currently supported addition replaces an existing key
                # or supplies the explicit default to a call with a target.
                if not call.arguments:
                    raise contracts.ContractConflict("argument_requires_explicit_target")
                last = call.arguments[-1]
                prefix = replacements[last.offset] if last.offset in replacements else contracts.Value(last.value, True).argument(last.name)
                replacements[last.offset] = " ".join((prefix, *additions)).strip()
        except contracts.ContractConflict as error:
            issues.append(_issue(call.instruction, str(error), line=call.line))
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
        source, calls, lambda call: names.get(call.offset, _current_name(call.instruction)), normalize_prompt,
        arguments=lambda _call, argument: replacements.get(argument.offset),
    )


def _normalize_fields(value, *, prompt=False, script=False, instruction=False, script_path=None):
    if isinstance(value, list):
        result, issues = [], []
        for index, item in enumerate(value):
            try:
                result.append(_normalize_fields(item, prompt=prompt, script=script, instruction=instruction,
                                                script_path=script_path))
            except DefinitionMigrationRequired as error:
                issues.extend({**issue, "path": [index, *issue["path"]]} for issue in error.issues)
        if issues:
            raise DefinitionMigrationRequired(issues)
        return result
    if isinstance(value, dict):
        call_name = value.get("instruction") if isinstance(value.get("instruction"), str) else None
        if isinstance(value.get("arguments"), dict) and isinstance(value.get("instruction"), str):
            if _post_mark_conflict(value["instruction"], value["arguments"]):
                raise DefinitionMigrationRequired([_issue(value["instruction"], "old_single_mark_arguments_conflict")])
        keys = {"value", "default", "concat"}
        result, issues = {}, []
        for key, item in value.items():
            try:
                result[key] = item if key in _REVIEW_FIELDS or key == "instruction" and call_name else _normalize_fields(
                    item,
                    prompt=key in _PROMPT_FIELDS or (prompt and key in keys),
                    script=key == "script" or (script and key in keys),
                    instruction=key == "instruction" or (instruction and key in keys),
                    script_path=script_path,
                )
            except DefinitionMigrationRequired as error:
                issues.extend({**issue, "path": [key, *issue["path"]]} for issue in error.issues)
        if issues:
            raise DefinitionMigrationRequired(issues)
        if call_name:
            name = _current_name(call_name)
            if name in _REMOVED:
                raise DefinitionMigrationRequired([_issue(call_name, "operation_moved_out_of_isa")])
            arguments = result.get("arguments", {})
            if not isinstance(arguments, dict):
                raise DefinitionMigrationRequired([_issue(call_name, "dynamic_arguments_require_owner_resolution")])
            try:
                result["instruction"], changed = contracts.rewrite(name, {key: contracts.Value(item) for key, item in arguments.items()})
                if "arguments" in result or changed:
                    result["arguments"] = {key: item.plain() for key, item in changed.items()}
            except contracts.ContractConflict as error:
                raise DefinitionMigrationRequired([_issue(call_name, str(error))]) from None
        fields = _OBJECT_ARGUMENTS.get(result.get("instruction")) if isinstance(result.get("instruction"), str) else None
        arguments = result.get("arguments")
        if fields and isinstance(arguments, dict) and fields[0] in arguments:
            old, new = fields
            if new in arguments:
                raise DefinitionMigrationRequired([_issue(result["instruction"], "old_and_new_arguments_conflict")])
            result["arguments"] = {key: item for key, item in arguments.items() if key != old}
            result["arguments"][new] = {old: arguments[old]}
        try:
            return contracts.normalize_fork_mode(result)
        except contracts.ContractConflict as error:
            raise DefinitionMigrationRequired([_issue("ForkResident", str(error), path=("fork_mode",))]) from None
    if isinstance(value, str):
        if script:
            return normalize_script(value, script_path=script_path)
        if prompt:
            return normalize_prompt(value)
        if instruction:
            name = _current_name(value)
            if name in _REMOVED:
                raise DefinitionMigrationRequired([_issue(value, "operation_moved_out_of_isa")])
            return contracts.RENAMES.get(name, name)
    return value


def normalize_component_config(config, *, script_path=None):
    _validate_script_path(script_path)
    return _normalize_fields(config, script_path=script_path)


def normalize_wire_config(config, *, script_path=None):
    _validate_script_path(script_path)
    return _normalize_fields(config, script_path=script_path)


def normalize_template(template, *, script_path=None):
    _validate_script_path(script_path)
    if isinstance(template["body"].get("program"), dict):
        return {**template, "body": normalize_definition(template["body"], script_path=script_path)}
    return {**template, "body": _normalize_fields(template["body"], script_path=script_path)}


def normalize_definition(definition, *, script_path=None):
    """Return a normalized current graph, including Family ownership checks.

    Install/restore/export callers that have a complete graph use this entry;
    isolated configs cannot establish ownership of a stock Family workflow.
    """
    _validate_definition_input(definition)
    _validate_script_path(script_path)
    result = deepcopy(definition)
    _normalize_definition(result, script_path=script_path)
    return result


def _validate_script_path(script_path):
    if script_path is not None and (not isinstance(script_path, str)
                                   or not script_path.startswith("/") or "\x00" in script_path):
        raise DefinitionMigrationRequired([_issue("Workflow", "invalid_script_path", path=("script_path",))])


def _validate_definition_input(definition):
    """Validate the portable graph boundary without resolving or inventing facts."""
    def require(condition, path, reason="incomplete_definition_graph"):
        if not condition:
            raise DefinitionMigrationRequired([_issue("Lineage", reason, path=path)])

    def text(value):
        return isinstance(value, str) and bool(value)

    require(isinstance(definition, dict), ())
    require(text(definition.get("lineage_id")), ("lineage_id",))
    program = definition.get("program")
    require(isinstance(program, dict), ("program",))
    indexes = {}
    for name, rows, path in (
        ("component", program.get("components"), ("program", "components")),
        ("wire", program.get("wires"), ("program", "wires")),
        ("function_block", definition.get("function_blocks"), ("function_blocks",)),
    ):
        require(isinstance(rows, list), path)
        index = {}
        for position, row in enumerate(rows):
            location = (*path, position)
            require(isinstance(row, dict) and text(row.get("id")), location)
            require(row["id"] not in index, (*location, "id"), "duplicate_definition_identity")
            index[row["id"]] = row
            if name != "function_block":
                require(text(row.get("kind")), (*location, "kind"))
                require("config" not in row or isinstance(row["config"], dict), (*location, "config"))
            if name == "wire":
                for key in ("source", "target"):
                    require(text(row.get(key)), (*location, key))
        indexes[name] = index
    for position, block in enumerate(definition["function_blocks"]):
        path = ("function_blocks", position)
        require(isinstance(block.get("elements"), list), (*path, "elements"))
        for offset, element in enumerate(block["elements"]):
            location = (*path, "elements", offset)
            require(isinstance(element, dict) and text(element.get("id")) and text(element.get("kind")), location)
            kind = element["kind"]
            if kind in indexes:
                require(element["id"] in indexes[kind], (*location, "id"), "missing_definition_membership_target")
        if block.get("family"):
            family = block["family"]
            require(isinstance(family, dict), (*path, "family"))
            for key in ("facade", "router", "steward"):
                require(text(family.get(key)), (*path, "family", key))
            require(isinstance(family.get("members"), list) and all(text(item) for item in family["members"]),
                    (*path, "family", "members"))
            bindings = family.get("role_bindings", {})
            require(isinstance(bindings, dict), (*path, "family", "role_bindings"))
            for key, identity in bindings.items():
                location = (*path, "family", "role_bindings", key)
                require(text(identity) and identity in indexes["function_block"], location,
                        "missing_definition_role_binding")
                role = indexes["function_block"][identity].get("role")
                require(isinstance(role, dict) and role.get("key") == key and role.get("family_id") == block["id"], location,
                        "mismatched_definition_role_binding")
        if block.get("role"):
            role = block["role"]
            require(isinstance(role, dict) and text(role.get("key")), (*path, "role"))
            require(isinstance(role.get("duties"), dict), (*path, "role", "duties"))
            for key, identity in role["duties"].items():
                location = (*path, "role", "duties", key)
                require(text(identity) and identity in indexes["function_block"], location,
                        "missing_definition_duty_binding")
                duty = indexes["function_block"][identity].get("duty")
                require(isinstance(duty, dict), location)
                parameters = duty.get("parameters", {})
                require(isinstance(parameters, dict), (*location, "parameters"))
                if parameters.get("family_task"):
                    require(isinstance(parameters.get("prompt"), str), (*location, "parameters", "prompt"))


def _family_original_context(definition, component):
    from . import family_definition, family_roles, family_tasks
    from . import family_recovery_effects, family_review_effects, personal_assistant

    source = (component.get("config") or {}).get("script")
    if not isinstance(source, str) or not family_definition.is_original_family_script(source):
        return None

    def reject(reason, path=("script",)):
        raise DefinitionMigrationRequired([_issue("Family", reason, path=path)])

    strategies = (
        (family_definition.FAMILY_ROUTER_SCRIPT, {"router"}, {"route"}),
        (family_definition.FAMILY_ROUTE_RELAY_SCRIPT, {"route_relay"}, {"route_review"}),
        (family_definition.FAMILY_SUMMARY_CHECK_SCRIPT, {"summary_check"}, {"summary"}),
        (family_definition.FAMILY_SUMMARY_ROLLUP_SCRIPT, {"summary_rollup", "memory_rollup"}, {"summary"}),
        (family_recovery_effects.RECOVERY, {"recovery_check"}, {"recovery"}),
        (family_definition.FAMILY_ROLE_AVAILABILITY_SCRIPT, {"role_availability"}, set()),
        (family_definition.FAMILY_QUEUE_NOTICE_SCRIPT, {"notify_member_queue_task"}, {"queue"}),
        (family_review_effects.NOTICE, {"notify_periodic_review_task"}, {"review"}),
        (family_review_effects.RECORD_ACTIVITY, {"review_activity"}, set()),
        (personal_assistant.REVIEW_SCRIPT, {"review_personal_activity"}, {"review"}),
    )
    if component.get("kind") != "effect":
        reject("original_workflow_role_not_supported")
    owners = []
    for block in definition.get("function_blocks", ()):
        if block.get("family"):
            elements = family_definition.family_elements(definition, block)
            matches = [item for item in elements if item["id"] == component["id"]]
            if matches:
                owners.append((block, elements, matches))
    if len(owners) != 1 or len(owners[0][2]) != 1:
        reject("original_workflow_family_owner_missing_or_ambiguous")
    block, elements, matches = owners[0]
    role = matches[0].get("key")
    if matches[0].get("kind") != "component" or not any(role in keys for _, keys, _ in strategies):
        reject("original_workflow_family_role_mismatch")
    upgraded = family_definition.migrate_original_script(source, role=role)
    match = next((tasks for script, keys, tasks in strategies if role in keys and script == upgraded), None)
    if match is None:
        reject("original_workflow_family_role_mismatch")
    tasks = match
    role_selected = family_definition.migrate_original_script(source) == source
    family = block["family"]
    lineage_id = definition["lineage_id"]
    required = {}
    if role == "recovery_check":
        required = family_recovery_effects.bindings(lineage_id, block["id"], family)
    elif role in {"notify_periodic_review_task", "review_activity"}:
        required = family_review_effects.bindings(lineage_id, block["id"], family)
    elif role == "role_availability":
        required = {"facade": {"value": family["facade"]},
                    "roles": {"value": [family["router"], family["steward"]]}}
    elif role in {"notify_member_queue_task", "review_personal_activity"}:
        required = {key: {"value": family[key]} for key in ("facade", "steward")}
    if role == "review_personal_activity" and not family.get("personal_ama_id"):
        reject("original_workflow_personal_family_required")

    def check_task(task, saved, path):
        if task not in tasks:
            return
        if not isinstance(saved, dict):
            reject("original_workflow_dynamic_task_binding", path)
        remaining = family_tasks.saved_fields(task, saved)
        defaults = family_tasks.defaults(family_tasks.TASKS[task][0]).get(task, {})
        for field, value in remaining.items():
            if value != defaults.get(field):
                reject("original_workflow_custom_task_binding", (*path, field))

    configured = family_roles.configured(definition, block)
    for task, saved in configured.items():
        check_task(task, saved, ("family_id", block["id"], "tasks", task))
    owned_ids = {item["id"] for item in elements}
    # Stage all additions. A later bad binding must not leave partial changes
    # when the read-only checker collects diagnostics and continues.
    additions = {}
    for wire in definition["program"]["wires"]:
        # Watch edges subscribe to this Effect's events; they do not invoke it.
        target = wire.get("target") == component["id"] and wire.get("kind") == "route"
        if not target and wire["id"] not in owned_ids:
            continue
        path = ("wire_id", wire["id"], "config")
        config = wire.get("config") or {}
        profiles = config.get("invoke_profiles", {})
        if not isinstance(profiles, dict) or target and not isinstance(profiles.get("effect"), dict):
            reject("original_workflow_effect_profile_missing", path)
        for profile_name, profile in profiles.items():
            if not isinstance(profile, dict) or not isinstance(profile.get("arguments", {}), dict):
                reject("original_workflow_dynamic_argument_binding", path)
            arguments = profile.get("arguments", {})
            if "family_tasks" in arguments:
                saved = arguments["family_tasks"]
                if not isinstance(saved, dict) or set(saved) != {"value"} or not isinstance(saved["value"], dict):
                    reject("original_workflow_dynamic_task_binding", (*path, "family_tasks"))
                for task, entry in saved["value"].items():
                    check_task(task, entry, (*path, "family_tasks", task))
            if target:
                expected = {**block["family"], "lineage_id": definition.get("lineage_id"),
                            "family_id": block["id"]}
                expected.update({key: binding["value"] for key, binding in required.items()})
                for key in {"facade", "steward", "router", "members", "lineage_id", "family_id", *required}:
                    if key in arguments and arguments[key] != {"value": expected.get(key)}:
                        reject("original_workflow_family_argument_mismatch", (*path, key))
                _check_custom_family_text(arguments, reject, path)
                if profile_name == "effect":
                    trigger = {"notify_member_queue_task": "queue", "notify_periodic_review_task": "review"}.get(role)
                    if trigger and (role_selected or "trigger" in arguments) and arguments.get("trigger") != {"value": trigger}:
                        reject(f"original_workflow_{trigger}_trigger_mismatch", (*path, "trigger"))
                    if role == "review_personal_activity":
                        for key in ("lineage_id", "activity_key", "review_key"):
                            binding = arguments.get(key)
                            if (not isinstance(binding, dict) or set(binding) != {"value"}
                                    or not isinstance(binding["value"], str) or not binding["value"]):
                                reject("original_workflow_personal_state_binding_missing", (*path, key))
                    values = deepcopy(required)
                    if required and tasks:
                        saved_tasks = deepcopy(arguments.get("family_tasks", {}).get("value", {}))
                        for task in tasks:
                            task_value = family_tasks.defaults(family_tasks.TASKS[task][0])[task]
                            for saved in (configured.get(task, {}), saved_tasks.get(task, {})):
                                task_value.update({key: value for key, value in saved.items()
                                                   if key not in {"guide", "prompt"}})
                                task_value.update(family_tasks.saved_fields(task, saved))
                            saved_tasks[task] = task_value
                        values["family_tasks"] = {"value": saved_tasks}
                    additions[wire["id"]] = values
        if target:
            _check_custom_family_text({key: value for key, value in config.items()
                                       if key != "invoke_profiles"}, reject, path)
    _check_custom_family_text(component.get("config", {}), reject, ())
    return upgraded, additions


def _check_custom_family_text(value, reject, path):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"script", "script_draft", "family_tasks"}:
                continue
            if key in {"guide", "prompt", "system_prompt", "instructions"} and item not in ("", None, {"value": ""}):
                reject("original_workflow_custom_text_binding", (*path, key))
            _check_custom_family_text(item, reject, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_custom_family_text(item, reject, (*path, index))


def _normalize_definition(definition, *, diagnostics=None, script_path=None):
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
    argument_additions = {}
    for component in program["components"]:
        before = component.get("config")
        if isinstance(before, dict):
            def normalize_component(config):
                context = _family_original_context(definition, component)
                prepared = {**config, "script": context[0]} if context is not None else config
                result = _normalize_fields(prepared, script_path=script_path)
                if context is not None:
                    argument_additions.update(context[1])
                return result
            component["config"] = normalize(before, {"component_id": component["id"]}, normalize_component)
        if component["kind"] == "effect":
            effect_ids.add(component["id"])
            if component.get("config") != before:
                changed_effects.add(component["id"])
    affected_wires = {}
    for wire in program["wires"]:
        before = wire.get("config")
        if isinstance(before, dict):
            def normalize_wire(value):
                prepared = deepcopy(value)
                if wire["id"] in argument_additions:
                    prepared["invoke_profiles"]["effect"].setdefault("arguments", {}).update(argument_additions[wire["id"]])
                return normalize_wire_config(prepared, script_path=script_path)
            wire["config"] = normalize(before, {"wire_id": wire["id"]}, normalize_wire)
        target = wire["target"]
        if wire.get("config") != before or target in changed_effects:
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
                "lineage_id": lineage_id, "wire_id": wire_id, **_target_identity(effect_id),
                "deliveries": [_delivery_status(delivery) for delivery in deliveries],
            })
    for operator_id, state in runtime.get("operators", {}).items():
        for pending in state.get("pending_deliveries", {}).values():
            for wire_id in sorted(wires.keys() & set(pending.get("output_wire_ids", ()))):
                blocked.append({"kind": "pending_operator_delivery", "lineage_id": lineage_id,
                                "operator_id": operator_id, "wire_id": wire_id, **_target_identity(wires[wire_id])})
    return blocked


def _target_identity(target):
    return {"effect_id" if target.startswith("effect_") else "target_id": target}


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

    from . import family_strategy_state, lineage_data_definitions, lineage_metadata_storage

    target = portal_database(schema="lineage")
    blocked = []
    # An ordinary owner store initializes its schema on connection. Use an
    # unpooled read-only snapshot so preflight is safe with the old owner live.
    with connect(target.dsn, row_factory=dict_row) as raw:
        raw.read_only = True
        raw.isolation_level = IsolationLevel.REPEATABLE_READ
        raw.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(target.schema)))
        connection = PostgresConnection(raw)
        blocked.extend(family_strategy_state.check_legacy_dispatches(connection))
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
    from . import family_definition, family_strategy_state, lineage_aggregate, lineage_state

    catalog = lineage_aggregate.lineage_catalog_store()

    def migrate(connection):
        if catalog.state_store.lock_lineage_definition(lineage_id) is None:
            return
        before = lineage_aggregate.read_lineage_state(lineage_id)
        after = deepcopy(before)
        effects, wires = _normalize_definition(after)
        if after != before:
            _require_clear(_report(_blockers(connection, lineage_id, effects, wires)))
            # Copy facts only during the exact canonical strategy cutover, on
            # the same locked transaction as the graph CAS. Subsequent starts
            # must not replay old facts over an evolved SharedState cell.
            if any(isinstance(source := (component.get("config") or {}).get("script"), str)
                   and family_definition.is_original_family_script(source)
                   for component in before["program"]["components"]):
                family_strategy_state.migrate_family_state(connection, lineage_id)
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
    from . import family_strategy_state, lineage_aggregate, resident_runtime_domain
    from .templates import store

    _require_clear(check())
    catalog = lineage_aggregate.lineage_catalog_store()
    for record in catalog.listing_records():
        _migrate_lineage(record["lineage_id"])
    resident_runtime_domain.migrate_instruction_names()
    store.migrate_instruction_names()
    family_strategy_state.retire_legacy_dispatches()
