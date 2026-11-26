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
TEMPLATE = (R("template", "object", "规范模板，含 behavior、editor 与本次施加参数说明 application_parameters"),)
SCRIPT_REVIEW_TARGET = (
    R('template_id', description='候选脚本所在的模板 ID'),
    R('effect_id', description='模板内候选脚本所属的 Effect 组件 key'),
    R('draft_hash', description='读取时取得的候选脚本草稿哈希'),
)
SCRIPT_REVIEW_RESULT = (R('template', 'object', '处理草稿后的模板及当前版本'),)
STORE_INSTALL_TARGET = (
    R("store_id", description="商店条目 store-<12hex>"),
    R("target_template_id", description="本人从该条目安装的原模板"),
)
TEMPLATE_CREATE_SCOPE = (Fixed("@public"), On("from_selection.lineage_id", "lineage", optional=True))
CREATE_TEMPLATE_ARGS = (
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
)
UPDATE_TEMPLATE_ARGS = (
    R("template_id", description="要修改的模板"),
    P("template", "object", "变更字段，也可以使用同级字段"),
    *CONTENT,
)
APPLY_TEMPLATE_ARGS = (
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
)
APPLY_TEMPLATE_RESULT = (
    R("function_block_ids", "array", "施加时建立或已有的 Function Block（含 Family）ID"),
    R("created", "object", "新建对象 ID：{resident_ids, component_ids, wire_ids}"),
    R("delivered", "array", "已投递的消息，每项 {resident_id, message_id}"),
    R("trigger_path_ids", "array", "脚本模板执行中产生的 Trigger Path ID"),
    R("summary", description="施加结果的一句话说明"),
)


DECLARATIONS = (
    _d('ConfirmTemplateScript', FIXED, '由指定确认人采纳模板中 Effect 的候选脚本，须携带模板 expected_version。',
       SCRIPT_REVIEW_TARGET, SCRIPT_REVIEW_RESULT, mutates=True),
    _d('CancelTemplateScriptDraft', FIXED, '由确认人或发起者撤销模板中 Effect 的候选脚本，须携带模板 expected_version。',
       SCRIPT_REVIEW_TARGET, SCRIPT_REVIEW_RESULT, mutates=True),
    _d("ListTemplateStore", FIXED, "浏览已上架的模板快照及当前调用者的安装状态。",
       (P("search", description="搜索标题、说明、发布者"),
        P("group", description="模板分组", enum=("duty", "role", "organization", "task"))),
       (R("items", "array", "发布快照、施加指令、版本历史与安装状态"),), target=""),
    _d("GetTemplateStoreItem", FIXED, "读取已上架的单个模板及当前调用者的安装状态。",
       (R("store_id", description="商店条目 store-<12hex>"),),
       (R("item", "object", "发布快照、施加指令、版本历史与安装状态"),), target="store_id"),
    _d("PublishTemplate", FIXED, "发布本人创建的非内置模板快照；公共内容管理员也可发布 Resident 创建的非安装件源模板，保留原发布者归属；再次发布递增版本并恢复上架。",
       (R("template_id", description="本人创建的非内置模板，或由公共内容管理员发布的 Resident 自建源模板（不含安装副本）；人类个人模板仅本人可发布"), P("release_note", description="可选的一句版本说明")),
       (R("item", "object", "发布后的商店条目"), *TEMPLATE), mutates=True),
    _d("InstallStoreTemplate", FIXED, "把上架快照安装到个人工具箱，已安装时返回已有模板。",
       (R("store_id", description="商店条目 store-<12hex>"),), TEMPLATE, mutates=True, target="store_id"),
    _d("UpdateInstalledTemplate", FIXED, "用最新快照覆盖已安装的模板。",
       STORE_INSTALL_TARGET, TEMPLATE, mutates=True, target="store_id"),
    _d("InstallStoreTemplateAsNew", FIXED, "解除原安装件关联，另装最新快照。",
       STORE_INSTALL_TARGET, (*TEMPLATE, R("detached_template_id", description="保留并解除关联的旧模板")),
       mutates=True, target="store_id"),
    _d("UnpublishTemplate", FIXED, "发布者或公共内容管理员下架条目，已安装副本保留。",
       (R("store_id", description="要下架的商店条目"),),
       (R("store_id", description="已下架条目"), R("active", "boolean", "固定为 false")), mutates=True, target="store_id"),
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
    _d("CreateTemplate", TEMPLATE_CREATE_SCOPE, "新建模板；from_selection 从选区生成结构模板。", CREATE_TEMPLATE_ARGS, TEMPLATE, mutates=True, target=""),
    _d("GetTemplateCreatePreview", TEMPLATE_CREATE_SCOPE, "校验新模板并返回规范结果。", CREATE_TEMPLATE_ARGS, TEMPLATE, target=""),
    _d("UpdateTemplate", FIXED, "修改模板并保留已有 Function Block。", UPDATE_TEMPLATE_ARGS, TEMPLATE, mutates=True, target="template_id"),
    _d("GetTemplateUpdatePreview", FIXED, "预览修改后的模板与行为。", UPDATE_TEMPLATE_ARGS, TEMPLATE, target="template_id"),
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
    _d("ApplyTemplate", ON_LINEAGE, "向当前 Lineage 施加模板。", APPLY_TEMPLATE_ARGS, APPLY_TEMPLATE_RESULT, mutates=True, target="lineage_id"),
    _d("GetTemplateApplyPreview", ON_LINEAGE,
       "预览结构（structure）、角色（role）和 Family 回顾职责（family_duty）模板的匹配与拟变更。",
       APPLY_TEMPLATE_ARGS,
       (*APPLY_TEMPLATE_RESULT, R("preview", "array", "每一目标的匹配与预计修改")), target="lineage_id"),
)
