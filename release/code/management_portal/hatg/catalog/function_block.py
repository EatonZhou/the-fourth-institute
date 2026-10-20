"""Boundaries around existing Lineage elements."""
from ..declarations import ON_LINEAGE, InstructionDeclaration as D, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False):
    return D(name=name, category="function_block", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target="lineage_id",
             decides=decides)


TARGET = (R("lineage_id", description="Function Block 所在的 Lineage"),
          R("function_block_id", description="目标 Function Block"))
POSITION = (P("x", "number", "画布横坐标"), P("y", "number", "画布纵坐标"))
# A Lineage's Function Blocks are read with GetLineage (function_blocks).
DECLARATIONS = (
    _d("Functionalize", ON_LINEAGE, "把当前 Lineage 的选区封装为 Function Block，元素保持原有行为。", (
        R("lineage_id", description="选区所在的 Lineage"),
        R("element_ids", "array", "要封装的 Resident、组件、连线或 Function Block，至少一个、不能重复",
          items={"type": "string"}),
        P("title", description="Function Block 名称；省略时为“Function Block”"), *POSITION,
    ), (R("function_block_id", description="新 Function Block 的 ID"),), mutates=True),
    _d("Unfunctionalize", ON_LINEAGE, "解除 Function Block，保留内部元素；Family 请使用 Unfamilize。", TARGET,
       (R("element_ids", "array", "原来直接属于此 Function Block 的元素 ID"),), mutates=True),
    _d("UpdateFunctionBlock", ON_LINEAGE, "修改 Function Block 的名称、位置或元素归属。", (
        *TARGET, P("title", description="新名称；Family 请使用 UpdateFamily"), *POSITION,
        P("add_element_ids", "array", "要加入的同一 Lineage 中的元素", items={"type": "string"}),
        P("remove_element_ids", "array", "要移出的元素，须属于此 Function Block", items={"type": "string"}),
    ), (P("function_block", "object", "变空自动删除时省略"),), mutates=True),
    _d("DeleteFunctionBlock", ON_LINEAGE, "删除 Function Block 及其全部内部元素；Family 请使用 DeleteFamily，保留成员则使用 Unfamilize。", TARGET,
       (R("element_ids", "array", "随之删除的内部元素 ID，含嵌套 Function Block，不含此 Function Block"),
        R("resident_ids", "array", "随之删除的 Resident ID")), mutates=True),
)
