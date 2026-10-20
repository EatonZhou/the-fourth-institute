"""Resource action catalog: each resource type's own actions (decision_ad3e21094f14).

Actions run through ``RunResourceAction``.  Registering, updating and
unregistering use the general resource instructions; this catalog lists only
deterministic domain operations those instructions cannot express.
"""
from __future__ import annotations

from functools import lru_cache

from .contracts import InstructionError
from .declarations import P, R, ResourceActionDeclaration as A


ACTIONS = (
    A("device", "ssh.test", "测试到设备的 SSH 连接。", (
        P("timeout_seconds", "integer", "超时秒数，默认 15", minimum=1, maximum=45),
    ), (R("ok", "boolean"), P("error")), mutates=False),
    A("device", "ssh.credential.save", "保存设备的 SSH 密码凭据（不回显内容）。", (
        R("password", description="SSH 密码"),
    ), (R("resource_id"), R("via_resource_id"), R("credential_saved", "boolean"))),
    A("device", "ssh.credential.delete", "删除设备的 SSH 密码凭据。", (), (
        R("resource_id"), R("via_resource_id"), R("credential_saved", "boolean"), R("deleted", "boolean"),
    )),
    A("workbench", "generation.save", "保存一次页面生成结果。", (
        R("job_id"), R("base_revision", "integer", "生成所依据的页面修订号"),
        P("title"), P("description"), R("html"),
    ), (R("revision", "integer"),)),
    A("workbench", "reverse.plan.save", "保存一份反向整理计划。", (
        R("job_id"), R("plan", "object"),
    ), (R("revision", "integer"),)),
)


@lru_cache(maxsize=1)
def resource_action_catalog() -> dict[tuple[str, str], A]:
    catalog: dict[tuple[str, str], A] = {}
    for action in ACTIONS:
        key = (action.resource_type, action.action)
        if key in catalog:
            raise RuntimeError(f"资源动作重复声明：{key}")
        catalog[key] = action
    return catalog


def resource_action(resource_type: str, action: str) -> A:
    try:
        return resource_action_catalog()[(resource_type, action)]
    except KeyError:
        raise InstructionError(
            "instruction.invalid_arguments",
            f"资源类型 {resource_type} 没有声明动作 {action}",
        ) from None


def actions_for_type(resource_type: str) -> list[A]:
    return [action for (kind, _name), action in resource_action_catalog().items() if kind == resource_type]
