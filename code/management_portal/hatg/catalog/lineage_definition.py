"""Lineage definition instructions (decision_4637ca4a69e2)."""
from __future__ import annotations

from ..declarations import FIXED, ON_LINEAGE, PLACEMENT, InstructionDeclaration as D, On, P, R


def _d(name, description, parameters=(), result=(), *, mutates=False, streaming=False,
       target="lineage_id", one_of=(), decides=ON_LINEAGE):
    return D(name=name, category="lineage_definition", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates,
             streaming=streaming, target=target, one_of=one_of, decides=decides)


COMPONENT_KINDS = ("event_source", "effect", "filter", "selector", "reducer", "loop", "seed")
WIRE_KINDS = ("route", "target", "watch")
HANDOFF_ITEMS = P(
    "items", "array", "交接项，最多 24 项；每项说明来源工作区、相对范围、准备方式和目标位置",
    max_length=24, items={"type": "object"},
)

DECLARATIONS = (
    _d("CreateLineage", "创建一个新的 Lineage，并分配独立的 Lineage ID。", (
        R("title", description="Lineage 名称，超出 140 字截断"),
        P("description", description="Lineage 说明；同时建立 Family 时作为其目标"),
        R("project_id", description="新 Lineage 所属的 Project"),
        P("leader_resident_id", description="Leader Resident，须是 definition 中的成员节点；有成员时自动补齐；整条 Family 的门面即 Leader，自动任务使用其身份及权限"),
        P("definition", "object", "初始成员、组件和连线；自动任务使用当前 Leader Resident 的身份及权限；没有 Leader 时等待成员加入"),
        P("familize", "boolean", "同时建立 Lineage 范围的 Family；省略为 false"),
    ), (R("lineage_id", description="新 Lineage 的 ID"),
        P("family_id", description="同时建立的 Family；familize 为 true 时返回")), mutates=True, target="project_id", decides=(On("project_id", "project"),)),
    _d("GetLineage", "读取一个 Lineage 规范化后的完整定义。", (R("lineage_id", description="要读取的 Lineage"),), (
        R("lineage", "object", "元数据、成员节点、组件、连线、工作区交接、Function Block 实例；revision 可用作定义修改的 expected_version"),
    )),
    _d("UpdateLineage", "修改 Lineage 的名称、说明、所属项目、Leader、碎片或头像。", (
        R("lineage_id", description="要修改的 Lineage"),
        R("changes", "object", "title、description、project_id、leader_resident_id、fragments、avatar；avatar 为 {sprite_index: 0–41}、{upload_id}（CreateUpload purpose=avatar 的已完成上传）或 null（恢复为名称首字）；fragments 是碎片的完整新列表，每项 {id?, content, attachments?}，省略 id 的项是新碎片，列表中没有的碎片被删除"),
    ), (R("lineage", "object", "修改后的元数据与碎片"),), mutates=True),
    _d("UpdateLineageLayout", "保存 Lineage 画布上成员、组件和 Function Block 实例的坐标；只推进布局版本，不改定义 revision，也不重新编译。", (
        R("lineage_id", description="要保存布局的 Lineage"),
        R("positions", "array", "每项 {object_id, x, y}，最多 512 项；按对象后写覆盖，已不存在的对象列入 missing_object_ids",
          max_length=512, items={"type": "object"}),
    ), (
        R("applied", "array", "已保存的 {object_id, object_kind, x, y}"),
        R("missing_object_ids", "array", "已不存在、未保存坐标的对象 ID"),
        R("layout_revision", "integer", "保存后 Lineage 的布局版本；有坐标被保存时加一"),
        R("layout_updated_at", description="布局最近一次保存的时间"),
    ), mutates=True),
    _d("DeleteLineage", "删除整个 Lineage，其中的 Resident 和文件夹一并删除，不能恢复。", (R("lineage_id", description="要删除的 Lineage"),), (
        R("lineage_id", description="被删除的 Lineage"), R("resident_ids", "array", "随之删除的 Resident ID"),
    ), mutates=True),
    _d("ListComponentTypes", "读取当前可用的组件类型，以及每种事件的配置定义。", (), (
        R("component_types", "array", "组件类型，每项 {kind, title, description, ports}"),
        R("event_types", "array", "事件源类型，每项含 type、title、description、fields（配置字段）、ports"),
    ), target="", decides=FIXED),
    _d("CreateComponent", "在 Lineage 中创建一个组件。", (
        R("lineage_id", description="组件所在的 Lineage"),
        R("kind", description="组件类型；可用类型见 ListComponentTypes", enum=COMPONENT_KINDS),
        P("title", description="组件名称；省略时按类型取默认名，如“新 Effect”"),
        P("config", "object", "按组件类型校验；Effect 脚本的固定 Resident/组件目标须声明 endpoints 并通过 target 连线引用，不能硬编码；failure_recipients 是失败通知用户名数组，省略时默认设立用户，空数组关闭通知"),
        P("client_key", description="调用者的审计标记，不代替组件 ID"),
        *PLACEMENT,
    ), (R("component_id", description="新组件的 ID"), R("component", "object", "新组件的定义：id、kind、title、config 等")), mutates=True),
    _d("UpdateComponent", "修改一个组件的名称或配置。Effect 脚本的固定 Resident/组件目标必须通过具名端点与 target 连线声明；错误草稿可保存，但不能执行。", (
        R("lineage_id", description="组件所在的 Lineage"), R("component_id", description="要修改的组件"),
        R("changes", "object", "title、config；Effect 还可以包含 script、endpoints、failure_recipients（失败通知用户名数组，空数组关闭通知）；事件源还可以包含 watch_subject：{kind, id}，支持多选的 Resident 或 Effect 事件可用 {kind: \"resident\", ids: [...]} 或 {kind: \"effect\", ids: [...]} 同时监听多个同类对象，任一触发"),
    ), (R("component", "object", "修改后的组件定义：id、kind、title、config 等"),), mutates=True),
    _d("DeleteComponent", "删除一个组件及与它相连的连线。", (
        R("lineage_id", description="组件所在的 Lineage"), R("component_id", description="要删除的组件"),
    ), (
        R("component_id", description="被删除的组件"), R("wire_ids", "array", "随之删除的连线 ID"),
    ), mutates=True),
    _d("CreateWire", "创建激活连线或对象绑定；方向始终为发起方到接收方或被引用对象。", (
        R("lineage_id", description="连线所在的 Lineage"), R("source", description="route 的输出节点；watch 的观察事件源；target 的 Effect"),
        P("source_port", description="输出端口；watch 固定为 watch，target 为 Effect 的具名端点"),
        R("target", description="激活的接收节点或绑定对象 ID；watch 可观察 Resident、组件、投递连线、工作区交接"),
        P("target_port", description="仅 route 使用：invoke 或 Loop.done；watch/target 直接绑定对象，省略此字段"),
        R("kind", description="route 激活连线；target 绑定 Effect 端点；watch 观察对象", enum=WIRE_KINDS),
        P("config", "object", "参数映射、include_envelope（默认 true）与忙碌策略（drop、latest、queue）；指向 Invoke 端口的连线写明任务"),
        P("label", description="连线上显示的名称"),
        P("client_key", description="调用者的审计标记，不代替连线 ID"),
    ), (R("wire_id", description="新连线的 ID"),
        R("wire", "object", "新连线的定义：id、kind、source、source_port、target、target_port、config、label")), mutates=True),
    _d("UpdateWire", "修改一条连线的端点或配置。", (
        R("lineage_id", description="连线所在的 Lineage"), R("wire_id", description="要修改的连线"),
        R("changes", "object", "要改的字段：source、source_port、target、target_port、kind、config、label、client_key"),
    ), (R("wire", "object", "修改后的连线定义：id、kind、source、target、config、label 等"),), mutates=True),
    _d("DeleteWire", "删除一条连线。", (
        R("lineage_id", description="连线所在的 Lineage"), R("wire_id", description="要删除的连线"),
    ), (R("wire_id", description="被删除的连线"),), mutates=True),
    _d("CreateCandidateFork", "建立 Seed 的候选 Fork：Seed 的候选从这个 Resident 的工作出发，采纳 Seed 时 Fork 这个 Resident，"
       "这条关系随之变成到新 Resident 的继承关系。一个 Seed 只有一个候选 Fork，换成另一个 Resident 时替换原来的。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("seed_id", description="要建立候选 Fork 的 Seed 组件"),
        R("source_resident_id", description="同一 Lineage 中的 Resident"),
    ), (
        R("edge_id", description="建立的候选 Fork 关系 ID"), P("replaced_edge_id", description="被替换的候选 Fork；原来没有或来源相同时为空"),
    ), mutates=True, target="seed_id"),
    _d("DeleteCandidateFork", "删除 Seed 的候选 Fork；Seed 保留，重新建立候选 Fork 之前不能采纳。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("seed_id", description="要删除候选 Fork 的 Seed 组件"),), (
        R("edge_id", description="被删除的候选 Fork 关系 ID"),
    ), mutates=True, target="seed_id"),
    _d("CreateWorkspaceHandoff", "在同一 Lineage 的两个不同 Resident 之间创建交接关系。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("source_resident_id", description="交出工作区的 Resident"),
        R("target_resident_id", description="接收交接的 Resident；同一来源与接收方只有一个交接"),
        HANDOFF_ITEMS,
        P("explanation", description="交接说明，最多 8000 字", max_length=8000),
        P("label", description="交接关系上显示的名称"),
    ), (R("handoff_id", description="交接的 ID；同一来源与接收方已有交接时为原交接"),), mutates=True, target="source_resident_id"),
    _d("UpdateWorkspaceHandoff", "修改交接项、说明或标签。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("handoff_id", description="要修改的交接"),
        R("changes", "object", "items、explanation（空字符串清除）、label"),
    ), (R("handoff", "object", "修改后的交接：id、source、target、items、explanation、label、status"),),
       mutates=True, target="handoff_id"),
    _d("DeleteWorkspaceHandoff", "删除交接关系及其运行记录关联。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("handoff_id", description="要删除的交接"),), (
        R("handoff_id", description="被删除的交接"),
    ), mutates=True, target="handoff_id"),
    _d("PrepareWorkspaceHandoff", "准备交接材料，并为目标 Resident 建立工作区绑定。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        P("handoff_id", description="要准备的交接；省略时按来源与接收方 Resident 查找或新建"),
        P("source_resident_id", description="交出工作区的 Resident；与 target_resident_id 一起代替 handoff_id"),
        P("target_resident_id", description="接收交接的 Resident；与 source_resident_id 一起代替 handoff_id"),
    ), (
        R("handoff_id", description="被准备的交接；省略参数时为找到或新建的交接"),
        R("status", description="准备结果：preparing 进行中、ready 完成、failed 失败", enum=("preparing", "ready", "failed")),
        R("items", "array", "交接项：每项含 item_id、source、transfer、target 及准备结果 result"),
    ), mutates=True, target="handoff_id",
       one_of=(("handoff_id",), ("source_resident_id", "target_resident_id"))),
    _d("GetWorkspaceHandoff", "读取交接定义和最近一次准备的结果。", (R("handoff_id", description="要读取的交接"),), (
        R("handoff", "object", "交接定义：id、source、target、items、explanation、label、status"),
        P("status", description="交接的记录状态，如 draft、ready、failed"),
        P("items", "array", "交接项及最近一次准备的结果"),
        P("error", "string", "最近一次准备失败的原因；没有失败时为空"),
    ), target="handoff_id", decides=FIXED),
    _d("GetLineageDiagnostics", "检查当前 Lineage 定义，列出会阻止运行的错误和需要注意的警告；Effect 脚本按指令声明校验字面量参数，说明中带脚本行号。", (R("lineage_id", description="要检查的 Lineage"),), (
        R("diagnostics", "array", "代码、对象 ID、级别和说明"), R("runnable", "boolean", "没有 error 级诊断、可以运行时为 true"),
    )),
    _d("ApplyLineageChanges", "把多项修改作为一次原子提交：全部成功时版本只推进一次，任一项失败时全部不生效。", (
        R("lineage_id", description="要修改的 Lineage"),
        R("operations", "array", "每项是对组件或连线的 create、update、delete、clone，或对 Resident 的 delete；后面的操作可以引用前面创建的对象",
          items={"type": "object"}),
        *PLACEMENT,
    ), (
        R("created_ids", "array", "新建（含 clone）的组件和连线 ID"),
        R("updated_ids", "array", "被修改的组件和连线 ID"),
        R("deleted_ids", "array", "被删除的组件、连线和 Resident ID，含随之删除的对象"),
    ), mutates=True),
    _d("WatchLineage", "订阅当前 Lineage 配置失效通知；合并中间更新，收到通知后重新读取当前配置。", (
        R("lineage_id", description="要订阅的 Lineage"),
        P("after_cursor", "integer", "上次收到的 cursor；省略时从当前定义版本开始", minimum=0),
    ), (
        R("cursor", "integer", "变化后的 Lineage 修改计数；下次订阅作 after_cursor"), R("lineage_id", description="发生变化的 Lineage"),
    ), streaming=True),
)
