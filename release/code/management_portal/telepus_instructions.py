"""Telepus instruction declarations (decision_692a484e9bea), executed by Portal Web.

Binding a User to a Telegram ID belongs to the user instructions;
controlling Telepus's own services belongs to the environment instructions.
(decision_6eac174d85bf); it is Telepus's part of the instruction catalog
(``isa.instructions`` in ``portal_features.py``).  Every service reads it,
Portal Web runs the handlers in ``telepus_web_isa.py``; a Portal without
Telepus has none of these instructions.
"""
from __future__ import annotations

from management_portal.hatg.declarations import FIXED, InstructionDeclaration as D, P, R


PAGE_INSTRUCTIONS = {"GetTelepusStatus": {}, "SetTelepusEnabled": {"enabled": True}}


def _d(name, description, parameters=(), result=(), *, mutates=False, target="", category="user"):
    return D(name=name, category=category, owner="web", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=FIXED)


DECLARATIONS = (
    _d("GetTelepusStatus", "读取 Telepus 的三个用户服务的启用和运行状态。", (),
       (R("status", "object", "Telepus 服务状态、缺失的服务和错误"),), category="environment"),
    _d("SetTelepusEnabled", "启用并启动或停止并禁用 Telepus 的三个用户服务；保留已有联系人和数据。", (
        R("enabled", "boolean", "是否开启 Telepus"),
    ), (R("status", "object", "切换后的 Telepus 服务状态"),), mutates=True, category="environment"),
    _d("BindTelepusContactForUser", "为已登记的活动 User 绑定 Telegram；替换时原 ID 必须仍属于目标用户。", (
        R("user_id", description="User 的用户名"), R("id_token", description="Telegram 数字用户 ID，5–32 位数字"),
        P("previous_id_token", description="替换目标用户的已有绑定时，填写原 Telegram ID"),
    ), (R("contact", "object", "telegram_user_id、name、portal_actor"),), mutates=True, target="user_id"),
    _d("UnbindTelepusContactForUser", "解除指定 User 与 Telegram ID 的对应关系；不删除账号，归属变化时拒绝。", (
        R("user_id", description="User 的用户名"), R("id_token", description="待解绑的 Telegram 数字用户 ID"),
    ), (R("user_id", description="解除绑定的 User"), R("telegram_user_id", description="被解绑的 Telegram ID"), R("removed", "boolean", "是否删除了现有绑定；已不存在时为 false")),
        mutates=True, target="user_id"),
)
