"""pulse application tool contracts and Web owner handlers."""
from __future__ import annotations
from typing import Any

from management_portal.hatg.declarations import FIXED, ON_USER, P, R
from management_portal.hatg.kernel import Invocation
from management_portal.application_tools import ApplicationToolDeclaration as D

TOOL_REFS = {
    "GetPulse": "application:pulse.read",
    "PublishPulseItem": "application:pulse.publish"
}


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target=""):
    return D(ref=TOOL_REFS[name], name=name, description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=decides)


PULSE = R("pulse", "object", "Pulse 视图：periods 中各周期的公共摘要 public 与个人动态 mine（GetPulse 为 user_id 的，"
                             "PublishPulseItem 为调用者的）、已公开条目 publications、生成状态 status")

DECLARATIONS = (
    _d("GetPulse", ON_USER, "读取按日、周、月整理的知识动态，以及一位 User 的个人动态条目。", (
        R("user_id", description="User 的用户名，决定返回谁的个人动态"),
    ), (PULSE,), target="user_id"),
    _d("PublishPulseItem", FIXED, "公开或撤回调用者自己的一条个人动态条目。", (
        R("source_period", description="条目所在摘要的周期：daily、weekly 或 monthly"),
        R("source_key", description="条目所在摘要的 key，取 GetPulse 返回的值"),
        R("item_id", description="条目 ID，取 GetPulse 返回的个人动态条目 id"),
        R("published", "boolean", "true 公开，false 撤回已公开的条目"),
    ), (P("publication", "object", "公开时：公开记录"), PULSE), mutates=True),
)


def _pulse():
    from management_portal import portal_pulse_backend

    return portal_pulse_backend


def get_pulse(inv: Invocation) -> dict[str, Any]:
    from management_portal.web_isa import _named_user

    return {"pulse": _pulse().get_home_payload(_named_user(inv))}


def publish_pulse_item(inv: Invocation) -> dict[str, Any]:
    published = bool(inv.argument("published"))
    result = _pulse().update_publication(inv.caller, {
        "action": "publish" if published else "unpublish",
        "source_period": inv.argument("source_period"),
        "source_key": inv.argument("source_key"),
        "item_id": inv.argument("item_id"),
    })
    payload = {"pulse": _pulse().get_home_payload(inv.caller)}
    if published:
        payload["publication"] = result.get("publication") or {}
    return payload


HANDLERS = {
    "GetPulse": get_pulse,
    "PublishPulseItem": publish_pulse_item,
}
