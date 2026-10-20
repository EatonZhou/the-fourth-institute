"""Template recipes, validation previews and one-shot application."""

from ..declarations import FIXED, ON_LINEAGE, Fixed, On
from ..declarations import InstructionDeclaration as D
from ..declarations import P, R


def _d(
    name, decides, description, parameters=(), result=(), *, mutates=False, target="template_id"
):
    return D(
        name=name,
        category="template",
        owner="lineage",
        description=description,
        parameters=tuple(parameters),
        result=tuple(result),
        mutates=mutates,
        target=target,
        decides=decides,
    )


CONTENT = (
    P("kind", description="模板模块，见 ListTemplates 的 kinds；不可修改，只能与原值相同"),
    P("category", description="模板分组", enum=("apply", "structure", "team")),
    P("title", description="模板标题，不能为空"),
    P("description", description="模板说明"),
    P("targets", "array", "可施加的目标类型，须是模块支持目标的子集", items={"type": "string"}),
    P("parameters", "array", "参数说明列表，每项 {name, type, required, description, default, options}",
      items={"type": "object"}),
    P("body", "object", "模块内容，按 kind 校验"),
)
TEMPLATE = (R("template", "object", "规范模板，含 behavior 与 editor"),)
DECLARATIONS = (
    _d(
        "ListTemplates",
        FIXED,
        "列出模板及模块编辑器描述。",
        (
            P(
                "target_kind",
                description="只列出支持这种目标的模板；省略时列出全部",
                enum=("resident", "selection", "canvas"),
            ),
        ),
        (
            R("templates", "array", "规范模板列表，每项含 behavior 与 editor"),
            R("kinds", "array", "模板模块，每项 {key, title, targets, editor}"),
        ),
        target="",
    ),
    _d(
        "GetTemplate",
        FIXED,
        "读取模板内容与施加行为。",
        (R("template_id", description="要读取的模板"),),
        TEMPLATE,
    ),
    _d(
        "CreateTemplate",
        (Fixed("@public"), On("from_selection.lineage_id", "lineage", optional=True)),
        "新建模板；from_selection 从选区或 Function Block 生成结构模板，rewrite 时选区成为施加时要查找的已有结构；dry_run 只校验。",
        (
            P(
                "template",
                "object",
                "模板内容：kind、title、category、description、targets、parameters、body；"
                "与 from_selection 同用时覆盖生成的字段",
            ),
            P(
                "from_selection",
                "object",
                "{lineage_id, element_ids, rewrite}：从这些元素生成结构模板",
                properties={
                    "lineage_id": {"type": "string"},
                    "element_ids": {"type": "array", "items": {"type": "string"}},
                    "rewrite": {"type": "boolean"},
                },
            ),
            P("dry_run", "boolean", "只校验并返回规范结果，不保存"),
        ),
        TEMPLATE,
        mutates=True,
        target="",
    ),
    _d(
        "UpdateTemplate",
        FIXED,
        "修改模板；dry_run 返回规范结果与行为预览，已有 Function Block 不变。",
        (
            R("template_id", description="要修改的模板"),
            P("template", "object", "变更字段，也可以使用同级字段"),
            *CONTENT,
            P("dry_run", "boolean", "只返回规范结果与行为预览，不保存"),
        ),
        TEMPLATE,
        mutates=True,
    ),
    _d(
        "DeleteTemplate",
        FIXED,
        "删除模板，已有 Function Block 保留。",
        (R("template_id", description="要删除的模板"),),
        (R("template_id", description="被删除的模板"),),
        mutates=True,
    ),
    _d(
        "RestoreTemplate",
        FIXED,
        "恢复内置模板的代码内容，包括已删除的内置模板。",
        (R("template_id", description="要恢复的内置模板"),),
        TEMPLATE,
        mutates=True,
    ),
    _d(
        "ApplyTemplate",
        ON_LINEAGE,
        "向当前 Lineage 施加模板；dry_run 只预览结构模板会找到、删除、修改和新建的元素。",
        (
            R("lineage_id", description="施加模板的 Lineage"),
            R("template_id", description="要施加的模板"),
            R(
                "target",
                "object",
                "施加目标：resident 为一个 Resident，selection 为非空选区，canvas 为画布坐标 x、y",
                properties={
                    "kind": {
                        "type": "string",
                        "enum": ["resident", "selection", "canvas"],
                    },
                    "resident_ids": {"type": "array", "items": {"type": "string"}},
                    "element_ids": {"type": "array", "items": {"type": "string"}},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                },
            ),
            P("parameters", "object", "{参数名: 值}；省略的参数取默认值，必填参数必须给出"),
            P("title", description="施加结果的名称；省略时用模板标题，Familize 类按目标命名"),
            P("resident_settings", "object", "{role_key: {provider,account,settings}}"),
            P("match_choices", "object", "{已有元素 key: [元素 ID]}，限定该元素可以绑定的真实元素"),
            P("dry_run", "boolean", "只预览结构模板的修改，不施加"),
        ),
        (
            R("function_block_ids", "array", "施加时建立或已有的 Function Block（含 Family）ID"),
            R("created", "object", "新建对象 ID：{resident_ids, component_ids, wire_ids}"),
            R("delivered", "array", "已投递的消息，每项 {resident_id, message_id}"),
            R("trigger_path_ids", "array", "脚本模板执行中产生的 Trigger Path ID"),
            R("summary", description="施加结果的一句话说明"),
        ),
        mutates=True,
        target="lineage_id",
    ),
)
