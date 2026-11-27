"""Rewrite ISA instruction names and argument contracts in prompts, scripts and configs.

Name-only and argument-compatible changes share this owner. Operations moved
out of ISA cannot be substituted with asynchronous ScriptRun handles: their
owners must supply an equivalent workflow, or normalization stops with locations.
``isa_definition_normalization`` adds the checks that need a whole definition,
and ``instruction_name_migration`` runs the migration over stored definitions.
"""

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


def definition_issue(instruction, reason, *, line=None, path=()):
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
    validate_script_path(script_path)
    if not source:
        return source
    from .family_definition import is_original_family_script

    if is_original_family_script(source):
        raise DefinitionMigrationRequired([definition_issue(
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
            issues.append(definition_issue(call.instruction, "old_single_mark_arguments_conflict", line=call.line))
            continue
        if name in _REMOVED:
            issues.append(definition_issue(call.instruction, "operation_moved_out_of_isa", line=call.line))
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
            issues.append(definition_issue(call.instruction, str(error), line=call.line))
            continue
        fields = _OBJECT_ARGUMENTS.get(name)
        if fields is None:
            continue
        old, new = fields
        if old not in arguments:
            continue
        if new in arguments:
            issues.append(definition_issue(call.instruction, "old_and_new_arguments_conflict", line=call.line))
            continue
        argument = arguments[old]
        try:
            replacements[argument.offset] = lineage_effect_script.object_argument(new, old, argument.value)
        except ValueError:
            issues.append(definition_issue(call.instruction, "argument_requires_intermediate_variable", line=call.line))
    if issues:
        raise DefinitionMigrationRequired(issues)
    return lineage_effect_script.rewrite_call_source(
        source, calls, lambda call: names.get(call.offset, _current_name(call.instruction)), normalize_prompt,
        arguments=lambda _call, argument: replacements.get(argument.offset),
    )


def normalize_fields(value, *, prompt=False, script=False, instruction=False, script_path=None):
    if isinstance(value, list):
        result, issues = [], []
        for index, item in enumerate(value):
            try:
                result.append(normalize_fields(item, prompt=prompt, script=script, instruction=instruction,
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
                raise DefinitionMigrationRequired([definition_issue(value["instruction"], "old_single_mark_arguments_conflict")])
        keys = {"value", "default", "concat"}
        result, issues = {}, []
        for key, item in value.items():
            try:
                result[key] = item if key in _REVIEW_FIELDS or key == "instruction" and call_name else normalize_fields(
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
                raise DefinitionMigrationRequired([definition_issue(call_name, "operation_moved_out_of_isa")])
            arguments = result.get("arguments", {})
            if not isinstance(arguments, dict):
                raise DefinitionMigrationRequired([definition_issue(call_name, "dynamic_arguments_require_owner_resolution")])
            try:
                result["instruction"], changed = contracts.rewrite(name, {key: contracts.Value(item) for key, item in arguments.items()})
                if "arguments" in result or changed:
                    result["arguments"] = {key: item.plain() for key, item in changed.items()}
            except contracts.ContractConflict as error:
                raise DefinitionMigrationRequired([definition_issue(call_name, str(error))]) from None
        fields = _OBJECT_ARGUMENTS.get(result.get("instruction")) if isinstance(result.get("instruction"), str) else None
        arguments = result.get("arguments")
        if fields and isinstance(arguments, dict) and fields[0] in arguments:
            old, new = fields
            if new in arguments:
                raise DefinitionMigrationRequired([definition_issue(result["instruction"], "old_and_new_arguments_conflict")])
            result["arguments"] = {key: item for key, item in arguments.items() if key != old}
            result["arguments"][new] = {old: arguments[old]}
        try:
            return contracts.normalize_fork_mode(result)
        except contracts.ContractConflict as error:
            raise DefinitionMigrationRequired([definition_issue("ForkResident", str(error), path=("fork_mode",))]) from None
    if isinstance(value, str):
        if script:
            return normalize_script(value, script_path=script_path)
        if prompt:
            return normalize_prompt(value)
        if instruction:
            name = _current_name(value)
            if name in _REMOVED:
                raise DefinitionMigrationRequired([definition_issue(value, "operation_moved_out_of_isa")])
            return contracts.RENAMES.get(name, name)
    return value


def normalize_component_config(config, *, script_path=None):
    validate_script_path(script_path)
    return normalize_fields(config, script_path=script_path)


def normalize_wire_config(config, *, script_path=None):
    validate_script_path(script_path)
    return normalize_fields(config, script_path=script_path)


def validate_script_path(script_path):
    if script_path is not None and (not isinstance(script_path, str)
                                   or not script_path.startswith("/") or "\x00" in script_path):
        raise DefinitionMigrationRequired([definition_issue("Workflow", "invalid_script_path", path=("script_path",))])
