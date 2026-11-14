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
    # A duty is a Function Block with a duty record (function_blocks[].duty in GetLineage);
    # ApplyTemplate on a Resident creates it and DeleteFunctionBlock removes it.
    _d("CreateDuty", ON_LINEAGE, "给一个 Resident 加一条只含提示词的职责，排在它已有职责之后。", (
        R("lineage_id", description="Resident 所在的 Lineage"),
        R("holder_id", description="持有职责的 Resident"),
        R("description", description="一句话描述", max_length=280),
        R("prompt", description="职责的提示词", max_length=20000),
        P("memory_level", description="提示词所在的记忆层级：turn 每轮、session 会话开始和压缩后、on_demand 按需读取；省略为 session",
          enum=("turn", "session", "on_demand")),
        P("category", description="类别；省略为 custom", enum=("fixed", "common", "custom")),
    ), (R("function_block", "object", "新职责的 Function Block，含 duty"),), mutates=True),
    _d("UpdateDuty", ON_LINEAGE, "修改一条职责的一句话描述、类别、提示词或记忆层级。", (
        R("lineage_id", description="职责所在的 Lineage"),
        R("function_block_id", description="职责的 Function Block"),
        P("description", description="一句话描述", max_length=280),
        P("category", description="类别", enum=("fixed", "common", "custom")),
        P("prompt", description="职责的提示词；空字符串移除提示词，只含提示词的职责须保留提示词", max_length=20000),
        P("memory_level", description="提示词所在的记忆层级：trigger 只在这条职责触发持有者时、turn 每轮、session 会话开始和压缩后、on_demand 按需读取",
          enum=("trigger", "turn", "session", "on_demand")),
    ), (R("function_block", "object", "修改后的 Function Block，含 duty"),), mutates=True),
    _d("OrderDuties", ON_LINEAGE, "调整一个持有者全部职责的先后顺序。", (
        R("lineage_id", description="职责所在的 Lineage"),
        R("holder_id", description="持有职责的 Resident 或组织 Function Block"),
        R("function_block_ids", "array", "持有者的全部职责，按新顺序各列一次", items={"type": "string"}),
    ), (R("function_block_ids", "array", "新的顺序"),), mutates=True),
)
