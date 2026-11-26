"""Pure, lossless call transformations for the remaining ISA contraction.

This is migration input handling, not a runtime alias registry. A selector must
be a literal before it can choose a replacement instruction. Calls needing a
new workflow or different result handling belong to their workflow owner.
"""

from dataclasses import dataclass
import json

from . import lineage_effect_script as script


RENAMES = {
    "RelayFamilyMessage": "RelayMessage",
    "ResolveFamilyRequest": "ResolveRelayedRequest",
    "GetFamilyRoutingContext": "GetRelayContext",
    "GetFamilySummaryInputs": "GetFacadeSummaryInputs",
    "UpdateResourceType": "SetResourceTypeEnabled",
    "UpdateUserNavigation": "SetUserNavigationFavorite",
    "BindTelepusContactForUser": "BindTelepusContact",
    "UnbindTelepusContactForUser": "UnbindTelepusContact",
    "MoveResident": "MoveLineageObjects",
}
RETIRED = {
    **{name: "Use the owning Family workflow and shared state" for name in (
        "MaintainFamilyProgress", "MaintainFamilyReview", "MaintainFamilyRecovery",
        "MaintainFamilyRoleAvailability", "MaintainResidentWorkSummary",
        "PublishFamilyProgress", "DispatchFamilyTask",
    )},
    **{name: "Use ApplyLineageChanges with per-operation authorization and current result handling"
       for name in ("CreateComponent", "UpdateComponent", "DeleteComponent", "CreateWire",
                    "UpdateWire", "DeleteWire", "CreateDuty", "UpdateDuty", "DeleteResident")},
    **{name: "Use the user-owned mailbox_todo resource and its ordering contract"
       for name in ("ListMailboxTodos", "CreateMailboxTodo", "UpdateMailboxTodo",
                    "DeleteMailboxTodo", "MoveMailboxTodo")},
    "ListTemporaryCredentials": "Read GetEffectivePermissions.temporary",
    "RunGoogleDriveBackup": "Use RunScript on the backup Lineage and read its result",
    "RunResourceAction": "Use the domain's named instruction and its current result contract",
}
PREVIEWS = {
    "ApplyLineageChanges": "GetLineageChangesPreview",
    "ActivatePermissionScheme": "GetPermissionSchemeActivationPreview",
    "CreatePermissionScheme": "GetPermissionSchemeCreatePreview",
    "UpdatePermissionScheme": "GetPermissionSchemeUpdatePreview",
    "ApplyTemplate": "GetTemplateApplyPreview",
    "CreateTemplate": "GetTemplateCreatePreview",
    "UpdateTemplate": "GetTemplateUpdatePreview",
}
PROMPT_NAMES = {
    "ForwardMessages": "ForwardMessagesToResident / ForwardMessagesToChatroom",
    "SearchMessages": "SearchResidentMessages / SearchChatroomMessages",
    "ListInputHistory": "ListResidentInputHistory / ListChatroomInputHistory",
    "ReviewComponentScript": "ConfirmComponentScript / CancelComponentScriptDraft",
    "ReviewTemplateScript": "ConfirmTemplateScript / CancelTemplateScriptDraft",
    "SetMailboxItemMuted": "UpdateMailboxItem",
    "SetMailboxItemPinned": "UpdateMailboxItem",
    "SetMailboxItemRead": "UpdateMailboxItem",
}
UNKNOWN = object()


class ContractConflict(ValueError):
    """Reason codes only: no caller payload in a preflight error."""


@dataclass(frozen=True)
class Value:
    raw: object
    parsed: bool = False

    def constant(self):
        if not self.parsed:
            return self.raw
        if isinstance(self.raw, script.Word):
            return self.raw.text if self.raw.text is not None else UNKNOWN
        if isinstance(self.raw, script.JsonValue) and all(isinstance(s, str) for s in self.raw.segments):
            return json.loads("".join(self.raw.segments))
        return UNKNOWN

    def json_source(self):
        if not self.parsed:
            if isinstance(self.raw, list):
                return "[" + ",".join(v.json_source() if isinstance(v, Value)
                                      else json.dumps(v, ensure_ascii=False) for v in self.raw) + "]"
            return json.dumps(self.raw, ensure_ascii=False)
        value = self.raw
        if isinstance(value, script.JsonValue):
            return "".join(part if isinstance(part, str) else "${" + ".".join(part.path) + "}"
                           for part in value.segments)
        if isinstance(value, script.Word):
            if value.text is not None:
                return json.dumps(value.text, ensure_ascii=False)
            if len(value.parts) == 1 and isinstance(value.parts[0], script.Ref) and value.parts[0].span is None:
                return "${" + ".".join(value.parts[0].path) + "}"
        raise ContractConflict("argument_requires_intermediate_variable")

    def argument(self, name):
        if self.parsed and isinstance(self.raw, script.Word):
            parts = []
            for part in self.raw.parts:
                if isinstance(part, str):
                    parts.append(script.render_template_value(part))
                else:
                    path = ".".join(part.path)
                    if part.span is not None:
                        offset, length = part.span
                        path += ":" + str(offset) + (":" + str(length) if length is not None else "")
                    parts.append("${" + path + "}")
            return name + '="' + "".join(parts) + '"'
        return name + ":=" + self.json_source()

    def plain(self):
        if self.parsed:
            raise TypeError("Parsed values are not configuration values")
        if isinstance(self.raw, list):
            return [v.plain() if isinstance(v, Value) else v for v in self.raw]
        return self.raw


def rewrite(instruction, original):
    """Return the new name and Values, preserving every untouched argument."""
    args = dict(original)
    name = RENAMES.get(instruction, instruction)

    def reject(reason):
        raise ContractConflict(reason)

    def literal(key, default=UNKNOWN):
        value = args[key].constant() if key in args else default
        if value is UNKNOWN:
            reject("dynamic_selector_requires_owner_resolution")
        return value

    def rename(old, new):
        if old in args:
            if new in args:
                reject("old_and_new_arguments_conflict")
            args[new] = args.pop(old)

    def remove_literal(key, allowed):
        if key in args:
            value = literal(key)
            if not any(type(value) is type(item) and value == item for item in allowed):
                reject("removed_mode_requires_owner_resolution")
            del args[key]

    def target_split(left, right, first, second):
        if (left in args) == (right in args):
            reject("ambiguous_target")
        return first if left in args else second

    if instruction in RETIRED:
        reject("operation_requires_owning_workflow")
    if instruction == "RelayFamilyMessage":
        if set(args) & {"quota_reviewed", "scope_gap", "scope_reviewed", "recover"}:
            reject("relay_policy_requires_owning_workflow")
    elif instruction == "ResolveFamilyRequest":
        rename("steward_message_id", "review_message_id")
    elif instruction == "MoveResident":
        # Its old singular result is different from an aggregate move receipt.
        reject("move_result_requires_owner_resolution")
    elif instruction == "InstallStoreTemplate":
        mode = literal("mode", "install")
        names = {"install": "InstallStoreTemplate", "overwrite": "UpdateInstalledTemplate", "copy": "InstallStoreTemplateAsNew"}
        if not isinstance(mode, str) or mode not in names:
            reject("invalid_legacy_mode")
        if mode == "install" and "target_template_id" in args or mode != "install" and "target_template_id" not in args:
            reject("ambiguous_install_target")
        name = names[mode]
        args.pop("mode", None)
    elif instruction == "RunWorkspaceHandoff":
        if "handoff_id" not in args or set(args) & {"source_resident_id", "target_resident_id"}:
            reject("handoff_requires_explicit_create_and_run")
    elif instruction == "ForwardMessages":
        name = target_split("target_resident_id", "target_chatroom_id", "ForwardMessagesToResident", "ForwardMessagesToChatroom")
        if name.endswith("Chatroom") and "activation_policy" in args:
            reject("chatroom_has_resident_policy")
        if "activation_policy" in args and literal("activation_policy") == "inject_active_turn":
            reject("inject_requires_mailbox_workflow")
    elif instruction in {"SearchMessages", "ListInputHistory"}:
        plural = instruction == "SearchMessages"
        name = target_split("resident_ids" if plural else "resident_id", "chatroom_id",
                            "SearchResidentMessages" if plural else "ListResidentInputHistory",
                            "SearchChatroomMessages" if plural else "ListChatroomInputHistory")
    elif instruction == "UpdateResidentWorkSummary":
        if "consolidation" in args:
            if set(args) & {"summary", "source_versions", "message_ids"}:
                reject("conflicting_summary_modes")
            name = "UpdateResidentSummaryConsolidation"
        elif "source_versions" in args:
            if "summary" not in args or "message_ids" in args:
                reject("conflicting_summary_modes")
            name = "UpdateFacadeSummary"
    elif instruction == "ForkResident" and "fork_mode" in args:
        mode = literal("fork_mode")
        if mode in ("recent-5", "recent-10"):
            if "recent_rounds" in args:
                reject("old_and_new_arguments_conflict")
            args["fork_mode"] = Value("recent")
            args["recent_rounds"] = Value(int(mode.split("-")[1]))
    elif instruction == "UpdateUser":
        if set(args) & {"status", "category"}:
            reject("invalid_legacy_user_field_level")
        if "changes" in args:
            changes = literal("changes")
            if not isinstance(changes, dict):
                reject("invalid_legacy_user_changes")
            moved = set(changes) & {"status", "category"}
            if moved:
                if len(changes) != 1:
                    reject("mixed_user_fields_require_separate_calls")
                key = next(iter(moved))
                name = "UpdateUserStatus" if key == "status" else "UpdateUserCategory"
                del args["changes"]
                args[key] = Value(changes[key])
    elif instruction == "UpdateChatroom" and "muted" in args:
        if set(args) != {"chatroom_id", "muted"}:
            reject("mixed_chatroom_fields_require_separate_calls")
        name = "SetChatroomMuted"
    elif instruction == "UpdateExecutionSettings" and "permission_system_enabled" in args:
        if set(args) != {"permission_system_enabled"}:
            reject("permission_switch_requires_separate_call")
        name = "SetPermissionSystemEnabled"
        rename("permission_system_enabled", "enabled")
    elif instruction == "AnswerResidentRequest" and "answers" in args:
        if set(args) & {"decision", "content"}:
            reject("old_and_new_arguments_conflict")
        answers = literal("answers")
        if not isinstance(answers, dict):
            reject("answer_shape_requires_owner_resolution")
        # Match the old dispatcher exactly; other dictionaries are answers to
        # questions, even if a question happened to be named action/content.
        action = answers.get("action")
        if set(answers) <= {"action", "content"} and action in ("accept", "decline", "cancel"):
            if "content" in answers and (action != "accept" or not isinstance(answers["content"], dict)):
                reject("approval_content_requires_owner_resolution")
            del args["answers"]
            if action == "cancel":
                name = "CancelResidentRequest"
            else:
                args["decision"] = Value(action)
                if "content" in answers:
                    args["content"] = Value(answers["content"])
    elif instruction == "ListMessages" and "view" in args:
        view = literal("view")
        if view == "attempts":
            name = "ListMessageAttempts"
        elif view != "messages":
            reject("list_view_requires_owner_resolution")
        del args["view"]
    elif instruction == "RestartSession":
        remove_literal("context_strategy", ("preserve",))
        remove_literal("active_execution_policy", ("wait",))
        if "operation" in args:
            operation = literal("operation")
            routing = bool(set(args) & {"account_id", "account_selection", "provider"})
            if operation == "switch_account" and routing or operation == "restart" and not routing:
                del args["operation"]
            else:
                reject("restart_lifecycle_requires_owner_resolution")
    elif instruction == "CreateLineage":
        remove_literal("familize", (False,))
    elif instruction in {"SetIntakeOpen", "DeletePermissionEntry"}:
        singular = "resident_id" if instruction == "SetIntakeOpen" else "entry_id"
        if singular in args:
            if singular + "s" in args:
                reject("old_and_new_arguments_conflict")
            args[singular + "s"] = Value([args.pop(singular)])
    elif instruction == "UpdatePostReadVersion" and "unread" in args:
        if "notification_revision" in args:
            reject("conflicting_post_read_modes")
        name = "SetPostUnread"
    elif instruction in {"ReviewComponentScript", "ReviewTemplateScript"}:
        decision = literal("decision")
        if decision not in ("confirm", "cancel"):
            reject("invalid_review_decision")
        target = "Component" if instruction == "ReviewComponentScript" else "Template"
        name = "Confirm" + target + "Script" if decision == "confirm" else "Cancel" + target + "ScriptDraft"
        del args["decision"]
    elif instruction == "CheckPermission" and "instruction" in args:
        if set(args) & {"a", "resource_id"}:
            reject("conflicting_permission_modes")
        name = "CheckInstructionPermission"
    elif instruction == "CreateUpload" and "source_address" in args:
        if set(args) - {"purpose", "target", "source_address"}:
            reject("upload_shape_requires_owner_resolution")
        name = "CreateUploadFromFile"
    elif instruction == "ReadScriptRunOutput" and "stream" in args:
        stream = literal("stream")
        if stream == "artifact":
            if "name" not in args:
                reject("artifact_name_required")
            name = "ReadScriptRunArtifact"
            del args["stream"]
    elif instruction == "PublishPulseItem" and "published" in args:
        published = literal("published")
        if type(published) is not bool:
            reject("invalid_publish_selector")
        name = "PublishPulseItem" if published else "UnpublishPulseItem"
        del args["published"]
    elif instruction == "ClearContext" and "active_execution_policy" in args:
        policy = literal("active_execution_policy")
        if policy not in ("reject", "wait"):
            reject("clear_lifecycle_requires_owner_resolution")
        name = "CreateContextReset" if policy == "wait" else "ClearContext"
        del args["active_execution_policy"]
    elif instruction == "ListTemplateStore" and "store_id" in args:
        # The old branch returned items=[...]; the new reader returns item.
        # Updating the call alone would break downstream result references.
        reject("store_item_result_requires_owner_resolution")
    elif instruction == "DeliverMessage":
        if set(args) & {"temporary_credentials", "reply_to_message_id"}:
            reject("delivery_requires_explicit_credentials_or_relay")
        if "activation_policy" in args and literal("activation_policy") == "inject_active_turn":
            reject("inject_requires_mailbox_workflow")
        if "user_id" in args:
            if "resident_id" in args:
                reject("ambiguous_target")
            if set(args) - {"user_id", "message", "business_ref"}:
                reject("user_delivery_has_resident_policy")
            name = "DeliverUserMessage"
    elif instruction.startswith("SetMailboxItem") and instruction in {"SetMailboxItemMuted", "SetMailboxItemPinned", "SetMailboxItemRead"}:
        name = "UpdateMailboxItem"
        mark = {"SetMailboxItemMuted": "muted", "SetMailboxItemPinned": "pinned", "SetMailboxItemRead": "unread"}[instruction]
        if set(args) - {"key", mark}:
            reject("old_single_mark_arguments_conflict")
        if instruction == "SetMailboxItemRead" and "unread" not in args:
            args["unread"] = Value(False)
    if instruction in PREVIEWS and "dry_run" in args:
        preview = literal("dry_run")
        if type(preview) is not bool:
            reject("invalid_preview_selector")
        name = PREVIEWS[instruction] if preview else instruction
        del args["dry_run"]
    return name, args


def normalize_fork_mode(config):
    """Template and transition metadata use the same explicit recent count."""
    mode = config.get("fork_mode")
    if mode not in ("recent-5", "recent-10"):
        return config
    if "recent_rounds" in config:
        raise ContractConflict("old_and_new_arguments_conflict")
    return {**config, "fork_mode": "recent", "recent_rounds": int(mode.split("-")[1])}
