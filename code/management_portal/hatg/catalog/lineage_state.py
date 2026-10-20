"""Lineage shared state instructions (decision_71ba0494b93e)."""
from __future__ import annotations

from ..declarations import ON_LINEAGE, InstructionDeclaration as D, P, R


def _d(name, description, parameters=(), result=(), *, mutates=False):
    return D(name=name, category="lineage_state", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates,
             target="lineage_id", decides=ON_LINEAGE)


DECLARATIONS = (
    _d("GetSharedState", "读取一个 Lineage 的全部共享状态，或其中一个状态单元。", (
        R("lineage_id", description="共享状态所属的 Lineage"), P("key", description="省略时读取全部"),
        P("fields", "array", "只读取这个状态单元中的这些顶层字段；需要同时指定 key", items={"type": "string"}),
    ), (R("state", "object", "状态值"),
        P("value", "any", "指定 key 时这个状态单元的值（不存在时为 null），与 state 中的相同；脚本按计算出的 key 读取时用它"))),
    _d("UpdateSharedState", "写入一个状态单元；附带期望值时，只在当前值等于期望值时写入（不存在的状态单元按 null 比较）。", (
        R("lineage_id", description="共享状态所属的 Lineage"),
        R("key", description="状态单元名，1–160 字符，不含控制字符"),
        R("value", "any", "要写入的 JSON 值，不超过 64 KiB"),
        P("expected_value", "any", "写入前要求的当前值；省略时直接写入"),
    ), (
        R("key", description="被写入的状态单元"), R("value", "any", "写入后的当前值；期望值不符时是未改变的当前值"),
        R("previous_value", "any", "写入前的值；原来不存在时为 null"),
        R("matched", "boolean", "期望值是否相符；没有期望值时为 true"),
        R("changed", "boolean", "值是否真的改变；期望值不符或新值与原值相同时为 false"),
    ), mutates=True),
    _d("DeleteSharedState", "删除一个状态单元；可以附带期望值，只在当前值匹配时删除。", (
        R("lineage_id", description="共享状态所属的 Lineage"), R("key", description="要删除的状态单元"),
        P("expected_value", "any", "删除前要求的当前值；省略时直接删除"),
    ), (
        R("deleted", "boolean", "状态单元是否被删除；不存在或期望值不符时为 false"),
        R("matched", "boolean", "期望值是否相符；没有期望值时为 true"),
        R("changed", "boolean", "共享状态是否改变，与 deleted 相同"),
    ), mutates=True),
)
