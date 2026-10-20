"""Organization and duty instructions (decision_6591e7793116)."""
from __future__ import annotations

from ..declarations import FIXED, InstructionDeclaration as D, On, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target="", owner="lineage"):
    return D(name=name, category="organization", owner=owner, description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=decides)


ON_PROJECT = (On("project_id", "project"),)


PROJECT_TYPES = ("company", "academic", "personal", "other")
PROJECT = R("project", "object", "Project 的资源记录：resource_id、title、summary、url、capabilities 等")
TAG_ITEM = {"type": "object", "properties": {
    "id": {"type": "string"}, "folder": {"type": "string"},
    "description": {"type": "string"}, "param": {"type": "string"},
}, "required": ["id", "description"]}

DECLARATIONS = (
    _d("CreateProject", FIXED, "创建一个 Project，建立项目目录、种子文件及 Project 层 Lineage 与 Leader。", (
        R("project_id", description="同时作为项目名称"),
        R("project_type", description="公司、学术、个人或其他", enum=PROJECT_TYPES),
        P("description", description="项目概述，写入种子文件；省略时为“[待补充]”"),
        P("status", description="项目状态，默认 active；配置了状态词表时须是其中之一"),
        P("ddl", description="截止日期"),
        P("tags", "array", "项目标签，重复项去除", items={"type": "string"}),
        P("language", description="新项目种子文档的语言，默认 zh-CN；仅用于初始化，不改写既有正文", enum=("zh-CN", "en")),
    ), (PROJECT,), mutates=True, owner="resource"),
    _d("GetProject", ON_PROJECT, "读取 Project 信息、摘要与 Project State。", (R("project_id", description="要读取的 Project"),), (
        PROJECT, R("summary", "object", "summary.md 的内容（不存在时标明）"),
        R("state", "object", "project_state/PROJECT_STATE.yaml 的解析视图：status、source 与各章节；文件不存在时 status 为 missing"),
    ), target="project_id", owner="resource"),
    _d("UpdateProject", ON_PROJECT, "修改 Project 的状态、截止日期、类型、摘要或头像。", (
        R("project_id", description="要修改的 Project"), R("changes", "object", "status、ddl、project_type、summary 与 avatar；avatar 为 {sprite_index: 0–41}、{upload_id}（CreateUpload purpose=avatar 的已完成上传）或 null（恢复为名称首字）"),
    ), (PROJECT,), mutates=True, target="project_id", owner="resource"),
    _d("DeleteProject", ON_PROJECT, "删除一个 Project 及其下的全部内容：资源、Lineage、Resident 与项目目录下的全部文件。"
       "其他功能的共享库中的对象（如论文库中的论文）不删除，只去掉它们与该 Project 的关联。Project 在权限树中的节点和指向它的权限表项随之删除，"
       "之后用同一 ID 新建的 Project 不继承它们。项目目录内有其他独立 Workspace 时拒绝，且不做任何修改。", (
        R("project_id", description="要删除的 Project"),
    ), (
        R("project_id", description="被删除的 Project"),
        R("resource_ids", "array", "注销的资源 ID，最后一项是 Project 本身"),
        R("lineage_ids", "array", "随之删除的 Lineage ID"), R("resident_ids", "array", "随之删除的 Resident ID"),
        R("unlinked", "object", "去掉了与该 Project 关联的共享库对象，按功能分组的资源 ID 数组（如 paper：论文）"),
    ), mutates=True, target="project_id", owner="resource"),
    _d("RenameProject", ON_PROJECT, "修改 Project 的 ID，并把项目目录移到 /share/Projects/<新 ID>；Portal 中的引用一起改："
       "登记、权限、Lineage、Resident、资源索引，目录内文件、git worktree、符号链接与执行环境里的旧路径。"
       "期间项目中的 Resident 暂停接单，有 Resident 正在执行、或目录被 Portal 之外的挂载与服务使用时拒绝。"
       "历史消息与执行记录保留原文。", (
        R("project_id", description="要改名的 Project"),
        R("new_project_id", description="新 ID，1–80 个字母（含中文）、数字或 _ . @ + -，不能已存在"),
        P("dry_run", "boolean", "只检查并报告将要修改的内容，不做修改"),
    ), (
        R("project_id", description="新的 Project ID"), R("previous_project_id", description="改名前的 Project ID"),
        R("path", description="新的项目目录"), R("previous_path", description="改名前的项目目录"),
        R("resident_ids", "array", "暂停接单的 Resident"),
        R("database", "object", "各表改写的行数（tables）与合计（rows），包括权限树中的节点路径"),
        R("files", "object", "改写的文件、链接与执行环境状态，以及失败项"),
        R("relations", "object", "改用新 ID 的共享库对象，按功能分组的资源 ID 数组（如 paper：论文）；dry_run 时为将要改的对象"),
        R("external_references", "array", "提到旧目录的服务脚本；Portal 不修改，需人工检查"),
        P("busy_resident_ids", "array", "dry_run：正在执行的 Resident"),
        P("blockers", "array", "dry_run：阻止改名的挂载与服务"),
    ), mutates=True, target="project_id", owner="resource"),
    _d("GetKnowledgeTags", FIXED, "读取全部 Project 共用的知识标签：每个标签的 ID、knowledge_base 子目录、说明与可选参数。", (), (
        R("tags", "array", "id、folder、description 与可选的 param", items=TAG_ITEM),
        R("version", description="标签列表的版本，用作 UpdateKnowledgeTags 的 expected_version"),
    )),
    _d("UpdateKnowledgeTags", FIXED, "用一份完整列表替换全部 Project 共用的知识标签，用于新增、改名和删除标签。"
       "新建的 Project 按其中的 folder 建立 knowledge_base 子目录；已有项目的目录由调用者迁移。", (
        R("tags", "array", "完整的标签列表；folder 缺省与 id 相同", items=TAG_ITEM),
    ), (R("tags", "array", "保存后的完整标签列表", items=TAG_ITEM),
        R("version", description="保存后标签列表的版本，可作下一次修改的 expected_version")), mutates=True),
    _d("MoveResident", (On("lineage_id", "lineage"), On("resident_id", "resident"), On("target_lineage_id", "lineage")), "把一个没有被流程引用的 Resident 迁移到目标 Lineage，保留其身份和会话；Family 成员随之离开 Family，继承等关系断开。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("resident_id", description="要迁移的 Resident"),
        R("target_lineage_id", description="迁入的目标 Lineage，须与源不同"),
        P("allow_empty_source", "boolean", "允许迁走源 Lineage 中唯一的 Resident，默认 false"),
    ), (
        R("operation_id", description="这次迁移的操作 ID，用 GetMoveOperation 跟踪工作区迁移"),
        P("family_id", description="成员离开的 Family；不是成员时为空"),
        P("detached", "array", "被断开的关系"),
    ), mutates=True, target="resident_id"),
    _d("MoveLineageObjects", (On("lineage_id", "lineage"), On("target_lineage_id", "lineage", optional=True)), "把一组对象及其中的 Resident 原子迁移到目标 Lineage，保留身份、会话和运行游标。", (R("lineage_id", description="对象当前所属的源 Lineage"),
        R("object_ids", "array", "要迁移的 Resident、组件或 Function Block，至少一个；所在 Function Block 整体迁移",
          items={"type": "string"}),
        P("target_lineage_id", description="省略时迁入一个新 Lineage"),
    ), (
        R("operation_id", description="这批迁移的操作 ID，用 GetMoveOperation 跟踪"),
        R("source_lineage_id", description="迁出的源 Lineage"),
        R("target_lineage_id", description="迁入的目标 Lineage；省略参数时为新建的 Lineage"),
        R("detached", "array", "被断开的关系"),
    ), mutates=True, target="object_ids"),
    _d("GetMoveOperation", FIXED, "读取一次迁移操作的阶段和终态。", (
        R("operation_id", description="MoveResident 或 MoveLineageObjects 返回的操作 ID"),
    ), (
        R("phase", description="当前步骤，如 workspace_move、finalizing；结束后为 completed"),
        P("status", description="pending 进行中、retrying 出错重试中、completed 已完成"),
        P("error", "object", "最近一次出错 {message, retryable}；没有出错时为 null"),
    ), target="operation_id"),
)
