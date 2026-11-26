"""Domain operation elements of the d066 instruction contract.

These are permission elements, not callable ISA aliases. Society derives the
ApplyLineageChanges requirements from the actual graph before authorization.
"""
from management_portal.hatg.declarations import On

LINEAGE_OPERATIONS = (
    "create_component", "update_component", "delete_component", "create_wire",
    "update_wire", "delete_wire", "update_duty", "delete_resident.lineage_id",
    "delete_resident.resident_id", "lineage_id",
)


def decisions(declared):
    result = dict(declared)
    if "ApplyLineageChanges" in result:
        result["ApplyLineageChanges"] = tuple(On(name, "resident" if name == "delete_resident.resident_id"
                                                  else "lineage", optional=True) for name in LINEAGE_OPERATIONS)
        if "GetLineageChangesPreview" in result:
            result["GetLineageChangesPreview"] = result["ApplyLineageChanges"]
    if "RelayMessage" in result:
        result["RelayMessage"] = (On("relay", "resident", optional=True), On("reply", "resident", optional=True))
    return result


def arguments(instruction, values):
    from .requests import Unresolved

    if instruction == "RelayMessage":
        branch = "reply" if values.get("reply_to_message_id") else "relay"
        return {branch: values.get("resident_id")}
    if instruction not in {"ApplyLineageChanges", "GetLineageChangesPreview"}:
        return values
    requirements = values.get("_permission_operations")
    if not isinstance(requirements, list) or not requirements:
        raise Unresolved("ApplyLineageChanges 缺少领域 owner 的逐项权限需求")
    result = {}
    for item in requirements:
        if not isinstance(item, dict):
            raise Unresolved("无效的领域权限需求")
        element = item.get("element", "")
        name = element.removeprefix("ApplyLineageChanges:")
        resource = item.get("resource_id")
        if element != "ApplyLineageChanges:" + name or name not in LINEAGE_OPERATIONS or not resource:
            raise Unresolved("未知的领域权限元素或缺少目标")
        target = result
        parts = name.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target.setdefault(parts[-1], []).append(resource)
    return result
