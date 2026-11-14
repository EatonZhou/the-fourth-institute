"""telepus application tool contracts and Web owner handlers."""
from __future__ import annotations

from typing import Any
from management_portal.hatg.contracts import InstructionError
from management_portal.hatg.kernel import Invocation

from management_portal.hatg.declarations import FIXED, P, R


from .page_access import PAGE_TOOLS


from management_portal.application_tools import ApplicationToolDeclaration as D

TOOL_REFS = {
    "GetTelepusWeixinNotifications": "application:telepus.weixin_notifications_read",
    "UpdateTelepusWeixinNotifications": "application:telepus.weixin_notifications_update",
    "StartTelepusWeixinBinding": "application:telepus.weixin_start",
    "GetTelepusWeixinBinding": "application:telepus.weixin_read",
    "SubmitTelepusWeixinPin": "application:telepus.weixin_pin",
    "UnbindTelepusWeixin": "application:telepus.weixin_unbind",
    "GetTelepusStatus": "application:telepus.status",
    "SetTelepusEnabled": "application:telepus.set_enabled",
    "BindTelepusContactForUser": "application:telepus.telegram_bind",
    "UnbindTelepusContactForUser": "application:telepus.telegram_unbind"
}


def _d(name, description, parameters=(), result=(), *, mutates=False, target=""):
    return D(ref=TOOL_REFS[name], name=name, description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=FIXED)


DECLARATIONS = (
    _d('GetTelepusWeixinNotifications', '读取本人或由 Portal 管理员读取指定用户的微信提醒开关。', (
        P('user_id', description='目标用户名；省略为本人，操作他人须为 Portal 管理员'),
    ), (R('preferences', 'object', 'mention 与 question 布尔开关，默认关闭'),)),
    _d('UpdateTelepusWeixinNotifications', '本人或 Portal 管理员修改微信提醒开关；开启后先建立旧项基线。', (
        P('user_id', description='目标用户名；省略为本人，操作他人须为 Portal 管理员'),
        P('mention', 'boolean', '是否提醒聊天室 @ 我'), P('question', 'boolean', '是否提醒待我回答'),
    ), (R('preferences', 'object', '修改后的 mention 与 question 开关'),), mutates=True),
    _d("StartTelepusWeixinBinding", "为本人或由 Portal 管理员为指定 User 开始微信扫码绑定。", (
        P("user_id", description="目标用户名；省略为本人，操作他人须为 Portal 管理员"),
    ),
       (R("binding", "object", "二维码图像、会话 ID 与状态；不含凭证"),), mutates=True),
    _d("GetTelepusWeixinBinding", "读取本人或由 Portal 管理员读取指定用户的绑定、轮询扫码会话。", (
        P("user_id", description="目标用户名；省略为本人，操作他人须为 Portal 管理员"),
        P("session_id", description="目标用户的扫码会话；留空读取当前绑定"),
    ), (R("binding", "object", "绑定状态"),), mutates=True),
    _d("SubmitTelepusWeixinPin", "本人或 Portal 管理员提交手机显示的数字，继续目标用户的扫码绑定。", (
        P("user_id", description="目标用户名；省略为本人，操作他人须为 Portal 管理员"),
        R("session_id", description="目标用户的扫码会话"), R("pin", description="手机显示的数字"),
    ), (R("binding", "object", "扫码状态"),), mutates=True),
    _d("UnbindTelepusWeixin", "本人或 Portal 管理员解除目标用户的微信绑定。", (
        P("user_id", description="目标用户名；省略为本人，操作他人须为 Portal 管理员"),
    ),
       (R("binding", "object", "解除后的状态"),), mutates=True),
    _d("GetTelepusStatus", "读取 Telepus 的三个用户服务的启用和运行状态。", (),
       (R("status", "object", "Telepus 服务状态、缺失的服务和错误"),)),
    _d("SetTelepusEnabled", "启用并启动或停止并禁用 Telepus 的三个用户服务；保留已有联系人和数据。", (
        R("enabled", "boolean", "是否开启 Telepus"),
    ), (R("status", "object", "切换后的 Telepus 服务状态"),), mutates=True),
    _d("BindTelepusContactForUser", "为已登记的活动 User 绑定 Telegram；替换时原 ID 必须仍属于目标用户。", (
        R("user_id", description="User 的用户名"), R("id_token", description="Telegram 数字用户 ID，5–32 位数字"),
        P("previous_id_token", description="替换目标用户的已有绑定时，填写原 Telegram ID"),
    ), (R("contact", "object", "telegram_user_id、name、portal_actor"),), mutates=True, target="user_id"),
    _d("UnbindTelepusContactForUser", "解除指定 User 与 Telegram ID 的对应关系；不删除账号，归属变化时拒绝。", (
        R("user_id", description="User 的用户名"), R("id_token", description="待解绑的 Telegram 数字用户 ID"),
    ), (R("user_id", description="解除绑定的 User"), R("telegram_user_id", description="被解绑的 Telegram ID"), R("removed", "boolean", "是否删除了现有绑定；已不存在时为 false")),
        mutates=True, target="user_id"),
)


def _require_admin(inv: Invocation) -> None:
    from management_portal import permission_system

    if permission_system.user_category(inv.caller) != "portal_admin":
        raise InstructionError("instruction.forbidden", "只有 Portal 管理员可以管理 Telepus 服务")


def get_telepus_status(inv: Invocation) -> dict[str, Any]:
    from management_portal.telepus.runtime import TelepusRuntime

    _require_admin(inv)
    return {"status": TelepusRuntime().status()}


def set_telepus_enabled(inv: Invocation) -> dict[str, Any]:
    from management_portal.telepus.runtime import TelepusRuntime

    _require_admin(inv)
    return {"status": TelepusRuntime().set_enabled(inv.argument("enabled"))}


def bind_telepus_contact_for_user(inv: Invocation) -> dict[str, Any]:
    from management_portal.watchboards.telepus import bind_contact

    return bind_contact(inv.argument("user_id"), inv.argument("id_token"), inv.argument("previous_id_token", ""))


def unbind_telepus_contact_for_user(inv: Invocation) -> dict[str, Any]:
    from management_portal.watchboards.telepus import unbind_contact

    result = unbind_contact(inv.argument("user_id"), inv.argument("id_token"))
    return {"user_id": result["username"], "telegram_user_id": result["telegram_user_id"], "removed": result["removed"]}


def weixin_binding(inv: Invocation) -> dict[str, Any]:
    from .weixin_binding import WeixinBinding
    binding = WeixinBinding()
    name = inv.name
    actor = binding.target(inv.caller, inv.argument("user_id", ""), require_active=name != "UnbindTelepusWeixin")
    if name == "StartTelepusWeixinBinding":
        result = binding.start(actor)
    elif name == "UnbindTelepusWeixin":
        result = binding.unbind(actor)
    else:
        result = binding.get(actor, inv.argument("session_id", ""), inv.argument("pin", ""))
    return {"binding": result}


def weixin_notifications(inv: Invocation) -> dict[str, Any]:
    from .notifications import settings
    changes = None
    if inv.name == 'UpdateTelepusWeixinNotifications':
        changes = {k: inv.argument(k) for k in ('mention', 'question') if inv.argument(k) is not None}
    return {'preferences': settings(inv.caller, inv.argument('user_id', ''), changes)}


HANDLERS = {
    'GetTelepusWeixinNotifications': weixin_notifications,
    'UpdateTelepusWeixinNotifications': weixin_notifications,
    **{name: weixin_binding for name in ("StartTelepusWeixinBinding", "GetTelepusWeixinBinding",
                                       "SubmitTelepusWeixinPin", "UnbindTelepusWeixin")},
    "GetTelepusStatus": get_telepus_status,
    "SetTelepusEnabled": set_telepus_enabled,
    "BindTelepusContactForUser": bind_telepus_contact_for_user,
    "UnbindTelepusContactForUser": unbind_telepus_contact_for_user,
}


def version_of(inv: Invocation, object_id: str) -> Any:
    """The User record's version token: the access store's revision."""
    from management_portal.web_isa import _access

    if object_id == inv.argument("user_id"):
        return int(_access()._revision())
    return None
