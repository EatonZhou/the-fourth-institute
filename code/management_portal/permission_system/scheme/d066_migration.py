"""Atomic permission migration for plan_d0669e2e82de; no runtime name aliases."""
from __future__ import annotations

import copy
import hashlib
import json

MIGRATION = "isa-d066-20261126"

# One approved unresolved row is retained, not translated into new capabilities.
# This disposition belongs only to this migration; it grants no runtime access
# and does not exempt rule documents, other schemes, IDs or A elements.
_RETAINED_ENTRY = ("default", "entry_8236fdec67d3108a91b6bdb1", "UpdateUser")
_RETENTION_SOURCE = "msg_ed8c7e62384080bc8c4dceaa1f5f9b41"

# A elements, including slots, rather than a text substitution of instruction names.
MAPPING = {
    "RelayFamilyMessage": ("RelayMessage:relay",),
    "ResolveFamilyRequest": ("ResolveRelayedRequest",),
    "GetFamilyRoutingContext": ("GetRelayContext",),
    "GetFamilySummaryInputs": ("GetFacadeSummaryInputs",),
    "DeliverMessage:resident_id": ("DeliverMessage", "RelayMessage:reply"),
    "DeliverMessage:user_id": ("DeliverUserMessage",),
    "ForwardMessages:target_resident_id": ("ForwardMessagesToResident",),
    "ForwardMessages:target_chatroom_id": ("ForwardMessagesToChatroom",),
    "SearchMessages:resident_ids": ("SearchResidentMessages",),
    "SearchMessages:chatroom_id": ("SearchChatroomMessages",),
    "ListInputHistory:resident_id": ("ListResidentInputHistory",),
    "ListInputHistory:chatroom_id": ("ListChatroomInputHistory",),
    "SetIntakeOpen:resident_id": ("SetIntakeOpen",),
    "SetIntakeOpen:resident_ids": ("SetIntakeOpen",),
    "DeletePermissionEntry:entry_id": ("DeletePermissionEntry",),
    "DeletePermissionEntry:entry_ids": ("DeletePermissionEntry",),
    "CheckPermission": ("CheckPermission", "CheckInstructionPermission"),
    "CreatePermissionScheme": ("CreatePermissionScheme", "GetPermissionSchemeCreatePreview"),
    "UpdatePermissionScheme": ("UpdatePermissionScheme", "GetPermissionSchemeUpdatePreview"),
    "ActivatePermissionScheme": ("ActivatePermissionScheme", "GetPermissionSchemeActivationPreview"),
    "CreateTemplate:@public": ("CreateTemplate:@public", "GetTemplateCreatePreview:@public"),
    "CreateTemplate:from_selection.lineage_id": ("CreateTemplate:from_selection.lineage_id", "GetTemplateCreatePreview:from_selection.lineage_id"),
    "UpdateTemplate": ("UpdateTemplate", "GetTemplateUpdatePreview"),
    "ApplyTemplate": ("ApplyTemplate", "GetTemplateApplyPreview"),
    "InstallStoreTemplate": ("InstallStoreTemplate", "UpdateInstalledTemplate", "InstallStoreTemplateAsNew"),
    "ListTemplateStore": ("ListTemplateStore", "GetTemplateStoreItem"),
    "ReviewComponentScript": ("ConfirmComponentScript", "CancelComponentScriptDraft"),
    "ReviewTemplateScript": ("ConfirmTemplateScript", "CancelTemplateScriptDraft"),
    "UpdateResidentWorkSummary": ("UpdateResidentWorkSummary", "UpdateResidentSummaryConsolidation", "UpdateFacadeSummary"),
    "AnswerResidentRequest": ("AnswerResidentRequest", "CancelResidentRequest"),
    "ListMessages": ("ListMessages", "ListMessageAttempts"),
    "ReadScriptRunOutput": ("ReadScriptRunOutput", "ReadScriptRunArtifact"),
    "CreateUpload": ("CreateUpload", "CreateUploadFromFile"),
    "PublishPulseItem": ("PublishPulseItem", "UnpublishPulseItem"),
    "ClearContext": ("ClearContext", "CreateContextReset"),
    "UpdateChatroom": ("UpdateChatroom", "SetChatroomMuted"),
    "UpdatePostReadVersion": ("UpdatePostReadVersion", "SetPostUnread"),
    "UpdateResourceType": ("SetResourceTypeEnabled",),
    "UpdateUserNavigation": ("SetUserNavigationFavorite",),
    "BindTelepusContactForUser": ("BindTelepusContact",),
    "UnbindTelepusContactForUser": ("UnbindTelepusContact",),
    "SetMailboxItemMuted": ("UpdateMailboxItem",),
    "SetMailboxItemPinned": ("UpdateMailboxItem",),
    "SetMailboxItemRead": ("UpdateMailboxItem",),
    "ApplyLineageChanges": ("ApplyLineageChanges:lineage_id",),
    # The new Project slot of a move is classified by the scheme itself, never inherited from the old level.
    "UpdateLineage": ("UpdateLineage:lineage_id",),
    "CreateComponent": ("ApplyLineageChanges:create_component",),
    "UpdateComponent": ("ApplyLineageChanges:update_component",),
    "DeleteComponent": ("ApplyLineageChanges:delete_component",),
    "CreateWire": ("ApplyLineageChanges:create_wire",),
    "UpdateWire": ("ApplyLineageChanges:update_wire",),
    "DeleteWire": ("ApplyLineageChanges:delete_wire",),
    "DeleteResident:lineage_id": ("ApplyLineageChanges:delete_resident.lineage_id",),
    "DeleteResident:resident_id": ("ApplyLineageChanges:delete_resident.resident_id",),
    "CreateDuty": ("ApplyLineageChanges:update_duty",),
    "UpdateDuty": ("ApplyLineageChanges:update_duty",),
    "UpdateExecutionSettings": ("UpdateExecutionSettings", "SetPermissionSystemEnabled"),
    "UpdateUser": ("UpdateUser", "UpdateUserStatus", "UpdateUserCategory"),
}

REMOVED = frozenset({
    "MaintainFamilyProgress", "MaintainFamilyReview", "MaintainFamilyRecovery",
    "MaintainFamilyRoleAvailability", "MaintainResidentWorkSummary", "PublishFamilyProgress",
    "DispatchFamilyTask", "RunGoogleDriveBackup", "ListTemporaryCredentials", "MoveResident",
    "ListMailboxTodos", "CreateMailboxTodo", "UpdateMailboxTodo", "DeleteMailboxTodo", "MoveMailboxTodo",
})
NAMED_ACTIONS = {
    "CreateKnowledgeFile": "@contributor", "UpdateKnowledgeFileName": "@contributor",
    "MoveKnowledgeItems": "@contributor", "UpdateDeviceSshCredential": "@admin",
    "DeleteDeviceSshCredential": "@admin", "CheckDeviceSsh": "@viewer", "RunDeviceCommand": "@admin",
    "UpdateWorkbenchPage": "@contributor", "UpdateWorkbenchReversePlan": "@contributor",
}
# The old parameter-dependent operation cannot be reconstructed from a bare A
# grant. The owner must resolve it, not convert a contributor grant to admin.
AMBIGUOUS = frozenset({"RunResourceAction", "UpdateExecutionSettings", "UpdateUser", "ApplyLineageChanges"}) | REMOVED
MERGES = (
    ("SetMailboxItemMuted", "SetMailboxItemPinned", "SetMailboxItemRead"),
    ("SetIntakeOpen:resident_id", "SetIntakeOpen:resident_ids"),
    ("DeletePermissionEntry:entry_id", "DeletePermissionEntry:entry_ids"),
    ("CreateDuty", "UpdateDuty"),
)


def targets(element):
    name = element.split(":", 1)[0]
    if name in REMOVED:
        return ()
    if element == "RunResourceAction":
        return tuple(NAMED_ACTIONS)
    mapped = MAPPING.get(element, (element,))
    return tuple(target for value in mapped for target in (
        (value, value.replace("ApplyLineageChanges:", "GetLineageChangesPreview:", 1))
        if value.startswith("ApplyLineageChanges:") else (value,)))


def classification(element, target, value):
    from .repository import SchemeConflict
    result = copy.deepcopy(value)
    if element == "RunResourceAction":
        if result != {"a": "@contributor"}:
            raise SchemeConflict("RunResourceAction 有自定义条件分类，需方案 owner 明确九项能力后迁移")
        return {"a": NAMED_ACTIONS[target]}
    if target != element and target in {"SetPermissionSystemEnabled", "UpdateUserStatus", "UpdateUserCategory"}:
        original = {"a": "@admin", "r": "@shared"} if element == "UpdateExecutionSettings" else {"a": "@admin"}
        if result != original:
            raise SchemeConflict(f"d066 {element} 有自定义分类，需方案 owner 明确目标与能力后迁移")
        return {"a": "@admin", "r": "@portal"}
    return result


def ambiguous(element):
    return (element == "ApplyLineageChanges"
            or element.split(":", 1)[0] in AMBIGUOUS - {"ApplyLineageChanges"})


def rules(document):
    from .repository import SchemeConflict
    result = copy.deepcopy(document)
    original_instructions = result.get("instructions", {})
    for group in MERGES:
        present = [element for element in group if element in original_instructions]
        if present and (len(present) != len(group) or any(
                original_instructions[element] != original_instructions[present[0]] for element in present)):
            raise SchemeConflict("d066 无法等价合并分类，需方案 owner 改写：" + ",".join(group))
    instructions = {}
    for element, value in result.get("instructions", {}).items():
        for target in targets(element):
            mapped = classification(element, target, value)
            if target in instructions and instructions[target] != mapped:
                raise SchemeConflict(f"d066 分类合并冲突：{target}")
            instructions[target] = mapped
    result["instructions"] = instructions
    entries = []
    original = result.get("entries", [])
    _check_merges(original, "a", ignored={"id", "title"})
    for row in original:
        element = row.get("a", "")
        if ambiguous(element):
            raise SchemeConflict(f"d066 规则显式授权需 owner 改写：{element}")
        for target in targets(element):
            updated = {**row, "a": target}
            if target != element and "id" in updated:
                updated["id"] = _id(row["id"], target)
            comparable = {k: v for k, v in updated.items() if k not in {"id", "title"}}
            if not any({k: v for k, v in e.items() if k not in {"id", "title"}} == comparable for e in entries):
                entries.append(updated)
    result["entries"] = entries
    return result


def _id(original, target):
    return original + "_" + hashlib.sha256(target.encode()).hexdigest()[:12]


def _check_merges(rows, field, *, ignored):
    from .repository import SchemeConflict
    for group in MERGES:
        sets = []
        for element in group:
            sets.append({json.dumps({k: v for k, v in row.items() if k not in ignored | {field}},
                                    sort_keys=True, default=str) for row in rows if row.get(field) == element})
        if any(sets) and any(found != sets[0] for found in sets[1:]):
            raise SchemeConflict("d066 无法等价合并授权，需 owner 改写：" + ",".join(group))


def migrate(store):
    from .repository import SchemeConflict, source_digest
    from .compiler import rules_digest
    with store.read() as con:
        if store.execute(con, "SELECT 1 FROM {s}.scheme_migrations WHERE migration_id=%s", (MIGRATION,)).fetchone():
            return False
    with store.transaction() as con:
        store.execute(con, "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (store.schema + ":" + MIGRATION,))
        if store.execute(con, "SELECT 1 FROM {s}.scheme_migrations WHERE migration_id=%s", (MIGRATION,)).fetchone():
            return False
        store.execute(con, "LOCK TABLE {s}.schemes,{s}.scheme_instances,{s}.scheme_sources,{s}.scheme_nodes,"
                      "{s}.scheme_entries,{s}.user_authorizations IN SHARE ROW EXCLUSIVE MODE")
        history = []
        def keep(table, row):
            history.append({"table": table, "row": dict(row)})
        for table, column in (("schemes", "document"), ("scheme_instances", "rules"), ("scheme_sources", "rules")):
            for row in store.execute(con, f"SELECT * FROM {{s}}.{table} WHERE {column} IS NOT NULL").fetchall():
                document = rules(row[column])
                if document == row[column]:
                    continue
                keep(table, row)
                encoded = json.dumps(document, ensure_ascii=False)
                if table == "schemes":
                    store.execute(con, "UPDATE {s}.schemes SET document=%s::jsonb,version=version+1 WHERE scheme_id=%s", (encoded, row["scheme_id"]))
                elif table == "scheme_instances":
                    digest = rules_digest(document) if rules_digest(row[column]) == row["rules_digest"] else row["rules_digest"]
                    store.execute(con, "UPDATE {s}.scheme_instances SET rules=%s::jsonb,rules_digest=%s WHERE scheme_id=%s", (encoded, digest, row["scheme_id"]))
                else:
                    store.execute(con, "UPDATE {s}.scheme_sources SET rules=%s::jsonb,digest=%s WHERE scheme_id=%s", (encoded, source_digest(document), row["scheme_id"]))
        rows = [dict(row) for row in store.execute(con, "SELECT * FROM {s}.scheme_entries").fetchall()]
        _check_merges(rows, "a_node", ignored={"entry_id"})
        desired = {}
        retained = set()
        for row in rows:
            element = row["a_node"]
            target = targets(element)
            if (row["scheme_id"], row["entry_id"], element) == _RETAINED_ENTRY:
                target = (element,)
                retained.add((row["scheme_id"], row["entry_id"]))
                history.append({"table": "scheme_entries", "row": dict(row),
                                "disposition": "retained_unmodified", "source_message_id": _RETENTION_SOURCE})
            elif ambiguous(element):
                raise SchemeConflict(f"d066 表项 {row['scheme_id']}/{row['entry_id']} 的 {element} 需 owner 明确等价能力")
            if len(target) > 1 and row["expires_at"] is not None:
                raise SchemeConflict(f"d066 临时凭证 {row['entry_id']} 需拆分；须先按原任务生命周期收回，不能扩大期限或破坏单ID撤回")
            for index, value in enumerate(target):
                new = {**row, "a_node": value}
                if value != element:
                    new["version"] += 1
                if index:
                    new["entry_id"] = _id(row["entry_id"], value)
                key = (row["scheme_id"], row["effect"], row["p_node"], value, row["r_node"], row["entry_id"] if row["expires_at"] else None)
                if key in desired:
                    prior = desired[key]
                    if any(prior[k] != new[k] for k in new if k != "entry_id"):
                        raise SchemeConflict(f"d066 目标表项冲突：{value}")
                else:
                    desired[key] = new
        changed_rows = [row for row in rows if (row["scheme_id"], row["entry_id"]) not in retained
                        and targets(row["a_node"]) != (row["a_node"],)]
        for row in changed_rows:
            keep("scheme_entries", row)
            store.execute(con, "DELETE FROM {s}.scheme_entries WHERE scheme_id=%s AND entry_id=%s", (row["scheme_id"], row["entry_id"]))
        originals = {(r["scheme_id"], r["entry_id"]): r for r in rows if r not in changed_rows}
        for row in desired.values():
            if (row["scheme_id"], row["entry_id"]) in originals:
                continue
            columns = tuple(row)
            store.execute(con, "INSERT INTO {s}.scheme_entries (" + ",".join(columns) + ") VALUES (" + ",".join("%s" for _ in columns) + ")", tuple(row[c] for c in columns))
        nodes = [dict(row) for row in store.execute(con, "SELECT * FROM {s}.scheme_nodes WHERE tree='A'").fetchall()]
        desired_nodes = {}
        for row in nodes:
            if any(targets(part) != (part,) for part in row["path"][:-1]):
                raise SchemeConflict(f"d066 A 元素仍有自定义子树：{row['id']}")
            for target in targets(row["id"]):
                new = {**row, "id": target, "path": [*row["path"][:-1], target]}
                if row["id"] == "RunResourceAction":
                    level = NAMED_ACTIONS[target]
                    path = ["@actions", "@admin"]
                    if level != "@admin": path.append("@contributor")
                    if level == "@viewer": path.append("@viewer")
                    new.update(parent=level, path=path + [target])
                if target in {"SetPermissionSystemEnabled", "UpdateUserStatus", "UpdateUserCategory"}:
                    new.update(parent="@admin", path=["@actions", "@admin", target])
                key = (row["scheme_id"], target)
                if key in desired_nodes and desired_nodes[key] != new:
                    raise SchemeConflict(f"d066 A 节点冲突：{target}")
                desired_nodes[key] = new
        changed_nodes = [row for row in nodes if targets(row["id"]) != (row["id"],)]
        for row in changed_nodes:
            keep("scheme_nodes", row)
            store.execute(con, "DELETE FROM {s}.scheme_nodes WHERE scheme_id=%s AND tree='A' AND id=%s", (row["scheme_id"], row["id"]))
        unchanged = {(r["scheme_id"], r["id"]) for r in nodes if r not in changed_nodes}
        for key, row in desired_nodes.items():
            if key in unchanged: continue
            store.execute(con, "INSERT INTO {s}.scheme_nodes(scheme_id,tree,id,parent,kind,path) VALUES (%s,%s,%s,%s,%s,%s)", tuple(row[k] for k in ("scheme_id", "tree", "id", "parent", "kind", "path")))
        if history: store.bump(con)
        store.execute(con, "INSERT INTO {s}.scheme_migration_history(migration_id,records) VALUES (%s,%s::jsonb)", (MIGRATION, json.dumps(history, ensure_ascii=False, default=str)))
        store.execute(con, "INSERT INTO {s}.scheme_migrations(migration_id) VALUES (%s)", (MIGRATION,))
    return True
