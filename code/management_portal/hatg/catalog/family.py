"""Family lifecycle instructions (decision_44cf587c1721); the steps the Family's own
Effects, Router and steward run are in ``family_internal``."""
from ..declarations import ON_RESIDENT, ON_LINEAGE, PLACEMENT, InstructionDeclaration as D, P, R


def _d(name, decides, description, parameters, result):
    return D(name=name, category="family", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=True, target="lineage_id",
             decides=decides)


DECLARATIONS = (
    _d("Familize", ON_LINEAGE, "将同一 Lineage 的 Resident 或选中的 Family 组合为 Family；新建门面、steward、Router，成员与专属工作流成为 Function Block 元素。", (
        R("lineage_id", description="要建立 Family 的 Lineage"),
        P("resident_ids", "array", "选区成员；scope=lineage 时省略", items={"type": "string"}),
        P("scope", "string", "selection（默认）或 lineage；后者覆盖整条 Lineage，可从空 Lineage 建立，后来新建的成员自动加入", enum=("selection", "lineage")),
        P("title", description="省略时：选区 Family 用随机一个成员的名称加 -family，整条 Lineage 的 Family 用 Lineage 标题；门面使用同一名称"),
        P("goal", description="Family 的共同目标，写入 steward 章程；省略时为空"),
        P("parameters", "object", "route_threshold、review_interval_seconds、auto_migrate（默认 true）"),
        P("resident_settings", "object", "新建角色的起始设置：{router|steward: {provider, account, settings}}"), *PLACEMENT,
    ), (R("family_id", description="Family 的 Function Block 键 fbi_…"), R("resource_id", description="Family 正式资源 ID：family_<12hex>；family_id 保留 Function Block 的 fbi_ 键"),
        R("facade_resident_id", description="Family 门面"), R("steward_resident_id", description="Family steward"),
        R("router_resident_id", description="Family Router"),
        R("member_resident_ids", "array", "Family 成员"), R("leader_resident_id", description="完成后 Lineage 的 leader"),
        R("rewired_wire_ids", "array", "改接到门面的外部连线"),
        R("function_block_ids", "array", "Family 的 Function Block 键"),
        R("created", "object", "本次新建的对象：resident_ids、component_ids、wire_ids；Lineage 已有 Family 时为空"),
        R("delivered", "array", "模板通用结果字段；Familize 不经此投递，为空"),
        R("trigger_path_ids", "array", "模板通用结果字段；Familize 为空"),
        R("summary", description="结果说明，例如“已创建 Family「…」”"))),
    _d("Unfamilize", ON_LINEAGE, "停止门面、steward 和 Router，交还 leader、改接外部连线，删除 Family 内部结构并保留成员与子 Family。", (
        R("lineage_id", description="Family 所在的 Lineage"),
        R("family_id", description="Family 资源 ID family_… 或现有 Function Block 键 fbi_…"),
        P("successor_resident_id", description="门面是 Lineage leader 时接任的 leader；省略时交还 Familize 前的 leader"),
        P("rewire_to_resident_id", description="门面上的外部连线改接到的成员；省略时改接到接任的 leader"),
    ), (R("family_id", description="被解除的 Family 的 Function Block 键"), R("resource_id", description="Family 正式资源 ID family_…"),
        R("member_resident_ids", "array", "保留下来的成员"), R("deleted_resident_ids", "array", "删除的门面、steward 与 Router"),
        R("leader_resident_id", description="解除后 Lineage 的 leader"), R("rewired_wire_ids", "array", "从门面改接到成员的外部连线"))),
    _d("DeleteFamily", ON_LINEAGE, "停止并删除整个 Family，包含门面、Router、Steward、所有成员、子 Family 和内部工作流；删除其 Session、自有 Workspace 与图引用，对话和运行轨迹按 Resident 删除规则保留。", (
        R("lineage_id", description="Family 所在的 Lineage"),
        R("family_id", description="Family 资源 ID family_… 或 Function Block 键 fbi_…"),
    ), (R("family_id", description="被删除的 Family 的 Function Block 键"), R("resource_id", description="Family 正式资源 ID family_…"),
        R("deleted_resident_ids", "array", "删除的全部 Resident：门面、Router、Steward、成员及子 Family 内的"),
        R("deleted_element_ids", "array", "删除的工作流元素"), R("leader_resident_id", description="删除后 Lineage 的 leader"))),
    _d("UpdateFamily", ON_LINEAGE, "更新 Family 成员名册、名称、共同目标与路由参数；移出的成员保留，自动维护名册结构，并可收纳或移出普通工作流组件与内部连线。", (
        R("lineage_id", description="Family 所在的 Lineage"),
        R("family_id", description="Family 资源 ID family_… 或现有 Function Block 键 fbi_…"),
        P("add_member_ids", "array", "要加入 Family 的 Resident", items={"type": "string"}),
        P("remove_member_ids", "array", "要移出 Family 的成员；移出后 Resident 保留", items={"type": "string"}),
        P("add_element_ids", "array", "收纳普通组件或内部连线；可从祖先 Family 移入，不接管其他 Block 或自动生成结构", items={"type": "string"}),
        P("remove_element_ids", "array", "移出直接收纳的工作流元素，回到父容器；不删除业务对象", items={"type": "string"}),
        P("title", description="新名称，门面同步改名；省略时不变"), P("goal", description="新的共同目标；省略时不变"),
        P("route_threshold", "integer", "Router 置信度低于此值时请 steward 复核；省略时不变", minimum=0, maximum=100),
        P("review_interval_seconds", "integer", "steward 周期回顾的间隔秒数；省略时不变", minimum=1),
        P("auto_migrate", "boolean", "额度不足时自动切换 Family 成员、Router 和 Steward 的账号；默认开启，关闭不绕过额度保护；省略时不变"),
        P("auto_migrate_priority", "array", "账号种类优先级，取 ListProviderAccounts 的 migration_type.id；同种类优先，再按此顺序，未列出的种类按默认顺序补在末尾；空数组恢复默认", items={"type": "string"}),
    ), (R("family_id", description="被更新的 Family 的 Function Block 键"), R("resource_id", description="Family 正式资源 ID family_…"),
        R("function_block", "object", "更新后的 Function Block：id、title、elements，以及 family（成员名册、goal、路由参数）"))),
)


DECLARATIONS += (
    D(name='GetFamilyChatPreferences', category='family', owner='lineage',
      description='读取当前调用者在指定 Family 门面的 M/S 聊天偏好。', target='resident_id', decides=ON_RESIDENT,
      parameters=(R('resident_id'),), result=(R('muted_member_ids', 'array'), R('solo_member_id'))),
    D(name='UpdateFamilyChatPreferences', category='family', owner='lineage', mutates=True,
      description='修改调用者自己的 Mute/Solo；不改变成员接单状态或其他用户偏好。多个 M、单个 S，退出 S 恢复 M。',
      target='resident_id', decides=ON_RESIDENT,
      parameters=(R('resident_id'), R('member_id'), R('control', enum=('mute','solo')), R('enabled','boolean')),
      result=(R('muted_member_ids', 'array'), R('solo_member_id'))),
)
