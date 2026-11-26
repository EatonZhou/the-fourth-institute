"""Lineage definition instructions (decision_4637ca4a69e2)."""
from __future__ import annotations

from ..declarations import FIXED, ON_LINEAGE, PLACEMENT, InstructionDeclaration as D, On, P, R


def _d(name, description, parameters=(), result=(), *, mutates=False, streaming=False,
       target="lineage_id", one_of=(), decides=ON_LINEAGE):
    return D(name=name, category="lineage_definition", owner="lineage", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates,
             streaming=streaming, target=target, one_of=one_of, decides=decides)


HANDOFF_ITEMS = P(
    "items", "array", "交接项，最多 24 项；每项说明来源工作区、相对范围、准备方式和目标位置",
    max_length=24, items={"type": "object"},
)
SCRIPT_REVIEW_TARGET = (
    R('lineage_id', description='候选脚本所在的 Lineage ID'),
    R('effect_id', description='候选脚本所属的 Effect 组件 ID'),
    R('draft_hash', description='读取时取得的候选脚本草稿哈希'),
)
SCRIPT_REVIEW_RESULT = (R('lineage', 'object', '处理草稿后的 Lineage 定义及当前 revision'),)
LINEAGE_CHANGES_ARGS = (
    R("lineage_id", description="要编辑的 Lineage"),
    R("operations", "array", "组件或连线的 create、update、delete、clone，或 Resident 的 delete；可引用前面创建的对象",
      items={"type": "object"}),
    P("function_block_id", description="限定到这一条职责；须携带 expected_version，仅可改该职责的组件、连线和元数据"),
    P("duty_changes", "object", "职责的 title、description、category、prompt、memory_level；Family 角色职责另支持 task_prompt"),
    *PLACEMENT,
)
LINEAGE_CHANGES_RESULT = (
    R("created_ids", "array", "新建（含 clone）的组件和连线 ID"),
    R("updated_ids", "array", "被修改的组件和连线 ID"),
    R("deleted_ids", "array", "被删除的组件、连线和 Resident ID，含随之删除的对象"),
    R("refs", "object", "本次变更中的引用名与对象 ID"),
    R("revision", "integer", "Lineage 定义版本"),
    P("function_block", "object", "编辑后的 Function Block"),
    P("preview", "object", "职责编辑前后的元素和职责记录"),
)

DECLARATIONS = (
    _d('ConfirmComponentScript', '由指定确认人采纳 Effect 候选脚本，须携带 Lineage expected_version。',
        SCRIPT_REVIEW_TARGET, SCRIPT_REVIEW_RESULT, mutates=True),
    _d('CancelComponentScriptDraft', '由确认人或发起者撤销 Effect 候选脚本草稿，须携带 Lineage expected_version。',
        SCRIPT_REVIEW_TARGET, SCRIPT_REVIEW_RESULT, mutates=True),
    _d("CreateLineage", "创建一个新的 Lineage，并分配独立的 Lineage ID。", (
        R("title", description="Lineage 名称，超出 140 字截断"),
        P("description", description="Lineage 说明"),
        R("project_id", description="新 Lineage 所属的 Project"),
        P("leader_resident_id", description="Leader Resident，须是 definition 中的成员节点；有成员时自动补齐；整条 Family 的门面即 Leader，自动任务使用其身份及权限"),
        P("definition", "object", "初始成员、组件和连线；自动任务使用当前 Leader Resident 的身份及权限；没有 Leader 时等待成员加入"),
    ), (R("lineage_id", description="新 Lineage 的 ID"),), mutates=True, target="project_id", decides=(On("project_id", "project"),)),
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
    _d("RunWorkspaceHandoff", "准备交接材料，并为目标 Resident 建立工作区绑定。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("handoff_id", description="CreateWorkspaceHandoff 返回的交接 ID"),
    ), (
        R("handoff_id", description="被准备的交接 ID"),
        R("status", description="准备结果：preparing 进行中、ready 完成、failed 失败", enum=("preparing", "ready", "failed")),
        R("items", "array", "交接项：每项含 item_id、source、transfer、target 及准备结果 result"),
    ), mutates=True, target="handoff_id"),
    _d("MoveWorkspaceHandoff", "将已有交接副本归位到所属 Lineage 的 handoff-workspaces，保留 Workspace ID、内容、绑定和尝试历史；活跃使用者阻止搬迁。", (
        R("lineage_id", description="交接所属 Lineage"),
        R("handoff_id", description="已有交接 ID"),
        R("item_id", description="要归位的交接项 ID"),
        R("workspace_id", description="该交接项已登记的副本 Workspace ID"),
        R("expected_workspace_version", description="原样携带 ResolveResource 返回的 resource.version"),
    ), (
        R("operation_id", description="持久移动回执 ID；无需移动时为空"),
        R("status", description="completed 已完成；pending 登记已提交、依赖修复等待重试", enum=("completed", "pending")),
        R("workspace", "object", "归位后的 Workspace 登记及版本"),
        P("error", description="依赖修复尚未完成的原因"),
    ), mutates=True, target="handoff_id", decides=(On("lineage_id", "lineage"), On("workspace_id", "workspace"))),
    _d("GetWorkspaceHandoff", "读取交接定义和最近一次准备的结果。", (R("handoff_id", description="要读取的交接"),), (
        R("handoff", "object", "交接定义：id、source、target、items、explanation、label、status"),
        P("status", description="交接的记录状态，如 draft、ready、failed"),
        P("items", "array", "交接项及最近一次准备的结果"),
        P("error", "string", "最近一次准备失败的原因；没有失败时为空"),
    ), target="handoff_id", decides=FIXED),
    _d("GetLineageDiagnostics", "检查当前 Lineage 定义，列出会阻止运行的错误和需要注意的警告；Effect 脚本按指令声明校验字面量参数，说明中带脚本行号。", (R("lineage_id", description="要检查的 Lineage"),), (
        R("diagnostics", "array", "代码、对象 ID、级别和说明"), R("runnable", "boolean", "没有 error 级诊断、可以运行时为 true"),
    )),
    _d("ApplyLineageChanges", "原子提交一组图和职责修改；成功时版本只推进一次。",
       LINEAGE_CHANGES_ARGS, LINEAGE_CHANGES_RESULT, mutates=True),
    _d("GetLineageChangesPreview", "预览一组图和职责修改；使用与提交相同的逐项权限和版本前提，不写入。",
       LINEAGE_CHANGES_ARGS, LINEAGE_CHANGES_RESULT),
    _d("WatchLineage", "订阅当前 Lineage 配置失效通知；合并中间更新，收到通知后重新读取当前配置。", (
        R("lineage_id", description="要订阅的 Lineage"),
        P("after_cursor", "integer", "上次收到的 cursor；省略时从当前定义版本开始", minimum=0),
    ), (
        R("cursor", "integer", "变化后的 Lineage 修改计数；下次订阅作 after_cursor"), R("lineage_id", description="发生变化的 Lineage"),
    ), streaming=True),
)
