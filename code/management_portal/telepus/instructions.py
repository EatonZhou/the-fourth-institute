"""Telepus instruction declarations (decision_692a484e9bea), executed by Portal Web.

Binding a User to a Telegram ID belongs to the user instructions;
controlling Telepus's own services belongs to the environment instructions.
(decision_6eac174d85bf); it is Telepus's part of the instruction catalog
(``isa.instructions`` in ``portal_features.py``).  Every service reads it,
Portal Web runs the handlers in ``telepus/web_isa.py``; a Portal without
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
