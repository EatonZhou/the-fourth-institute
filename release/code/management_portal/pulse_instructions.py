"""Pulse instruction declarations (decision_865ac8ebef25), executed by Portal Web: the
knowledge activity summarised by day, week and month, and the personal items a
User publishes.

Pulse's part of the instruction catalog (``isa.instructions`` in
``portal_features.py``); every service reads it, Portal Web runs the handlers
in ``pulse_web_isa.py``.  A Portal without Pulse has none of these instructions.
"""
from __future__ import annotations

from management_portal.hatg.declarations import FIXED, ON_USER, InstructionDeclaration as D, P, R

CATEGORIES = {"pulse": "Pulse"}


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target=""):
    return D(name=name, category="pulse", owner="web", description=description,
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
