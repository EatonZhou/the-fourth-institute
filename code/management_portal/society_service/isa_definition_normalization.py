"""Normalize whole definitions and templates, including Family ownership checks.

Install/restore/export callers that have a complete graph use these entries;
isolated configs cannot establish ownership of a stock Family workflow.
"""

from copy import deepcopy

from . import family_definition, family_recovery_effects, family_review_effects, family_roles, family_tasks
from .instruction_normalization import (
    DefinitionMigrationRequired, definition_issue, normalize_fields, normalize_prompt, normalize_wire_config,
    validate_script_path,
)


def normalize_template(template, *, script_path=None):
    validate_script_path(script_path)
    if isinstance(template["body"].get("program"), dict):
        return {**template, "body": normalize_definition(template["body"], script_path=script_path)}
    return {**template, "body": normalize_fields(template["body"], script_path=script_path)}


def normalize_definition(definition, *, script_path=None):
    """Return a normalized current graph, including Family ownership checks.

    Install/restore/export callers that have a complete graph use this entry;
    isolated configs cannot establish ownership of a stock Family workflow.
    """
    _validate_definition_input(definition)
    validate_script_path(script_path)
    result = deepcopy(definition)
    _normalize_definition(result, script_path=script_path)
    return result


def _validate_definition_input(definition):
    """Validate the portable graph boundary without resolving or inventing facts."""
    def require(condition, path, reason="incomplete_definition_graph"):
        if not condition:
            raise DefinitionMigrationRequired([definition_issue("Lineage", reason, path=path)])

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
    source = (component.get("config") or {}).get("script")
    if not isinstance(source, str) or not family_definition.is_original_family_script(source):
        return None

    def reject(reason, path=("script",)):
        raise DefinitionMigrationRequired([definition_issue("Family", reason, path=path)])

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
        (family_review_effects.PERSONAL_REVIEW, {"review_personal_activity"}, {"review"}),
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
                result = normalize_fields(prepared, script_path=script_path)
                if context is not None:
                    argument_additions.update(context[1])
                return result
            component["config"] = normalize(before, {"component_id": component["id"]}, normalize_component)
        if component["kind"] == "effect":
            effect_ids.add(component["id"])
            if component.get("config") != before:
                changed_effects.add(component["id"])
    from .templates.family_reconcile import router_steward_bindings
    for identity, bindings in router_steward_bindings(definition).items():
        argument_additions.setdefault(identity, {}).update(bindings)
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
