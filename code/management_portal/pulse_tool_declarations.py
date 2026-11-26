"""Application tool declarations for Pulse."""

from management_portal.application_tool_contracts import ApplicationToolDeclaration as D
from management_portal.hatg.declarations import FIXED, ON_USER, R


TOOL_REFS = {
    "GetPulse": "application:pulse.read",
    "PublishPulseItem": "application:pulse.publish",
    "UnpublishPulseItem": "application:pulse.unpublish",
}


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target=""):
    return D(ref=TOOL_REFS[name], name=name, description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=decides)


PULSE = R("pulse", "object", "Pulse 视图：periods 中各周期的公共摘要 public 与个人动态 mine（GetPulse 为 user_id 的，"
                             "PublishPulseItem 为调用者的）、已公开条目 publications、生成状态 status")

ITEM = (
    R("source_period", description="条目所在摘要的周期：daily、weekly 或 monthly"),
    R("source_key", description="条目所在摘要的 key，取 GetPulse 返回的值"),
    R("item_id", description="条目 ID，取 GetPulse 返回的个人动态条目 id"),
)

DECLARATIONS = (
    _d("GetPulse", ON_USER, "读取按日、周、月整理的知识动态，以及一位 User 的个人动态条目。", (
        R("user_id", description="User 的用户名，决定返回谁的个人动态"),
    ), (PULSE,), target="user_id"),
    _d("PublishPulseItem", FIXED, "公开调用者自己的一条个人动态条目。", ITEM,
       (R("publication", "object", "公开记录"), PULSE), mutates=True),
    _d("UnpublishPulseItem", FIXED, "撤回调用者自己的一条已公开个人动态条目。", ITEM,
       (PULSE,), mutates=True),
)
