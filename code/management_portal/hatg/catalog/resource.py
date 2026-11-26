"""Resource instructions (decision_ad3e21094f14), executed by ResourceService."""
from __future__ import annotations

from ..declarations import CURSOR, FIXED, NEXT_CURSOR, InstructionDeclaration as D, On, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target=""):
    return D(name=name, category="resource", owner="resource", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates,
             target=target, decides=decides)


RESOURCE_RECORD = R("resource", "object", "资源记录")

DECLARATIONS = (
    _d("SearchResources", FIXED, "按关键词、类型、项目和状态查找调用者可以看到的资源。", (
        P("q", description="关键词"),
        P("resource_type", description="资源类型"),
        P("user_id", description="mailbox_todo 搜索必填的所属 User；其他资源类型不得提供。User只查本人，Resident须获目标User的admin授权"),
        P("project_id", description="所属项目 ID"),
        P("status", description="资源状态"),
        P("limit", "integer", "每页最多返回的条数，默认 50", minimum=1, maximum=500),
        CURSOR(),
    ), (
        R("items", "array", "资源摘要：统一 ID、类型、标题、摘要、所属项目和打开地址"),
        R("total", "integer", "命中总数"),
        P("pending_count", "integer", "mailbox_todo 同一筛选下的未完成数；items返回Todo资源记录及attributes正文和version"),
        NEXT_CURSOR(),
    )),
    _d("ResolveResource", (On("address"),), "把一个资源地址解析为资源记录，并说明调用者可以对它执行哪些操作。", (
        R("address", "any", description="字符串资源地址，或 {workspace_id|repo_id|article_id, relative_path} 文件引用；上传文件推荐 {upload_id: 原上传句柄}，也接受 /api/uploads/<句柄> 或本实例正式 public/internal origin 或显式登记的 public_origin_aliases 的完整URL（可加 download=1，不接受其他参数或片段）。上传句柄不是资源ID，沿用所有已认证User/Resident可读规则；relative_path 不接受绝对路径或 .."),
    ), (
        RESOURCE_RECORD,
        P("target", "object", "地址中的内部目标"),
        R("operations", "array", "调用者当前可以执行的操作"),
    ), target="address"),
    _d("ReadResource", (On("address"),), "读取资源正文，或读取文件夹资源里的文件和目录：Workspace、Resident（Resident Folder）和 Lineage（Lineage Folder）的文件都用 <资源ID>/file/<相对路径> 寻址；读目录（含 <资源ID>/file 本身）返回它的直接子项。", (
        R("address", "any", description="字符串资源地址，或 {workspace_id|repo_id|article_id, relative_path} 文件引用；上传文件推荐 {upload_id: 原上传句柄}，也接受 /api/uploads/<句柄> 或本实例正式 public/internal origin 或显式登记的 public_origin_aliases 的完整URL（可加 download=1，不接受其他参数或片段）。上传文件对所有已认证User/Resident可读，支持meta_only、preview和超限下载；普通资源权限保持不变"),
        P("max_bytes", "integer", "直接返回内容的上限，默认 4 MiB；preview 时为预览长度", minimum=1),
        P("meta_only", "boolean", "只返回文件的名称、种类、大小和修改时间，不读内容；只用于文件地址"),
        P("preview", "boolean", "返回不超过 max_bytes 的文本预览：更长的文件截断并标记 truncated，图片、PDF 和其他二进制文件不返回内容，只标记 binary；只用于文件地址"),
    ), (
        R("content_type", description="内容类型；目录为 application/vnd.tfi.directory+json"),
        P("version", description="根资源的版本号或修改时间；内部文件、目录及下载结果为内容摘要；meta_only 和截断的预览不返回"),
        P("text", description="文本内容"),
        P("base64", description="base64 编码的字节"),
        P("download_url", description="内容超过 max_bytes 时的有时效下载地址，只对本次调用者有效，支持 Range 分段读取"),
        P("expires_at", description="下载地址的到期时间"),
        R("size", "integer", "内容大小"),
        P("name", description="文件或目录名"),
        P("kind", description="文件种类：directory、markdown、json、jsonl、text、image、pdf 或 binary"),
        P("modified_at", description="修改时间"),
        P("mtime_ns", description="纳秒修改时间，十进制字符串"),
        P("content_url", description="Portal 页面显示和下载这个文件的地址，支持 Range；加 &download=1 作为附件下载"),
        P("file_reference", "object", "可持久保存的 {workspace_id|repo_id|article_id, relative_path} 或上传文件 {upload_id} 引用"),
        P("entries", "array", "目录的直接子项，最多 200 项：name、kind、size、modified_at、mtime_ns、path（相对路径）和 address"),
        P("truncated", "boolean", "目录子项或预览被截断"),
        P("hidden_count", "integer", "目录中按安全规则隐藏的子项数"),
        P("binary", "boolean", "preview 时文件不是文本"),
        P("allows", "object", "Workspace 文件的 UpdateWorkspaceFile：调用者能否保存这个文件"),
    ), target="address"),
    _d("GetResourceType", FIXED, "读取一个资源类型是否可以登记、修改和撤销登记。", (
        R("resource_type", description="资源类型"),
    ), (
        R("registerable", "boolean", "提供者当前能否登记这种资源；未安装时为 false"),
        R("updatable", "boolean", "提供者当前能否修改这种资源"),
        R("unregisterable", "boolean", "提供者当前能否撤销登记这种资源"),
        R("type", description="资源类型"), R("enabled", "boolean", "类型是否已安装且已启用"),
        R("installed", "boolean", "提供该类型的功能单元是否已部署"),
        R("state", description="not_installed、enabled 或 disabled"),
        R("version", "integer", "类型启停状态的版本，可作 SetResourceTypeEnabled 的 expected_version"),
        R("instance_count", "integer", "索引中这种类型的资源数"), R("declaration", "object", "原始类型声明，独立于提供者运行能力"),
    )),
    _d("ListResourceTypes", FIXED, "列出全部资源类型声明、状态、实例数和提供者能力。", (), (
        R("types", "array", "每个资源类型的详情，各项内容同 GetResourceType 的结果"),
        R("registry", "object", "类型登记表的元数据：schema_version、ID 格式与特例 ID 规则"),
    )),
    _d("SetResourceTypeEnabled", FIXED, "启用或停用资源类型，已有实例保留。调用信封的 expected_version 给出类型状态的 version 时按它比较；省略时直接更新。", (
        R("resource_type", description="要启用或停用的资源类型"), R("enabled", "boolean", "true 启用，false 停用"),
    ), (R("type", description="被修改的资源类型"), R("enabled", "boolean", "修改后是否启用"),
        R("state", description="修改后的状态：enabled 或 disabled"),
        R("version", "integer", "修改后类型启停状态的版本，可作下一次修改的 expected_version")),
       mutates=True, target="resource_type"),
    _d("RegisterResource", (On("resource_type", place={"project": ("fields.project_id", "fields.project_ids"),
                                                     "resident": ("$caller",), "personal_space": ("$caller",)}),), "按资源类型的要求登记一个新资源，调用者记为它的创建者（created_by）。汇报归属认证创建者：Resident 自身或 User 个人空间，调用方不能指定他人归属。", (
        R("resource_type", description="资源类型"),
        P("fields", "object", "该类型要求的字段，例如知识条目的 project_id、title 与 body。"
          "放在 Project 下的类型必须给出位置：知识条目与实验写 project_id，repo 与 Workbench 写 project_ids，公共内容写 [\"@public\"]",
          properties={"project_id": {"type": "string", "description": "资源所属 Project ID"},
                      "project_ids": {"type": "array", "items": {"type": "string"},
                                      "description": "repo、Workbench、文章所属的 Project；公共内容写 [\"@public\"]"}}),
    ), (
        R("resource_id", description="新资源的统一 ID"),
        RESOURCE_RECORD,
    ), mutates=True),
    _d("UpdateResource", (On("resource_id"),), "按资源类型修改正文或元数据；知识正文与 Skill 支持 expected_version 检查（知识正文用版本号，Skill 用 ReadResource 返回的 sha256）。Workspace 只允许修改非空 title，必须原样携带 ResolveResource 返回的 resource.version。", (
        R("resource_id", description="资源统一 ID"),
        R("changes", "object", "要修改的字段；Knowledge 的 text（全文）与 body（仅 Markdown 主文件正文，保留元数据）必须且只能提供一个；body 可为空字符串，不可用于 Skill、伴随文件或版本恢复。可附 expected_version 防止覆盖。Knowledge 改变所属项目须使用 MoveKnowledgeItems，不能只改全文中的 project。Workspace 只接受 title 和 expected_version（也可用请求顶层 expected_version）"),
    ), (
        RESOURCE_RECORD,
        R("version", description="类型提供的版本号或修改时间；未提供时为空字符串"),
    ), mutates=True, target="resource_id"),
    _d("UnregisterResource", (On("resource_id"),), "以 @admin 权限撤销资源登记。按资源归属先注销身份、嵌套登记和绑定，再在后台删除该资源拥有的文件；共享目录中的其他文件保留。区域内外位置不决定删除方式，文件清理失败不恢复已注销身份。", (
        R("resource_id", description="资源统一 ID"),
    ), (
        R("resource_id", description="被撤销的统一 ID"),
        P("index_projection", "object", "若索引通知未完成，保留未确认状态及原因；登记注销已提交"),
    ), mutates=True, target="resource_id"),
    _d("CreateKnowledgeFile", (On("resource_id"),), "在知识条目的已登记附件目录新增文本文件，保留主文档及资源 ID；拒绝覆盖已有文件。", (
        R("resource_id", description="附件所属知识条目的统一 ID"), R("filename", description="不含目录的文本文件名"),
        R("text", description="附件全文"),
        R("expected_version", "integer", "当前知识根资源版本", minimum=1),
    ), (R("resource_id", description="附件所属知识条目的统一 ID"), R("address", description="新建附件的资源地址"),
        R("version", "object", "创建附件后知识根资源的版本信息")), mutates=True, target="resource_id"),
    _d("UpdateKnowledgeFileName", (On("resource_id"),), "重命名知识条目的 Markdown 主文件，保留 ID、标题、正文和附件位置；partial 后使用原参数重试。", (
        R("resource_id", description="要重命名主文件的知识条目统一 ID"), R("filename", description="不含目录的可见 .md 文件名，不覆盖已有文件"),
        R("expected_version", "integer", "当前知识根资源版本", minimum=1),
        P("operation_id", description="稳定操作 ID，恢复时保持原值"),
    ), (R("resource_id", description="保持不变的知识条目统一 ID"), R("address", description="重命名后知识条目的资源地址"),
        R("filename", description="重命名后的主文件名"), R("version", "object", "重命名后知识根资源的版本信息"),
        R("operation_id", description="本次重命名操作 ID，续办时原样传回"), R("status", description="completed 或 partial"),
        P("retryable", "boolean", "false 时应重新读取详情后发起新操作"),
        P("error", description="操作未全部完成的原因；已提交的重命名不会因该字段自动回滚")), mutates=True, target="resource_id"),
    _d("MoveKnowledgeItems", (On("project_id", "project"),), "将知识条目或 Skill 迁入目标 Project，保留 ID、内容和相对路径；逐项重核源和目标权限。", (
        R("project_id", description="目标 Project"),
        R("items", "array", "1–100 个待迁移条目及各自当前版本", items={"type": "object", "required": ["resource_id", "expected_version"],
            "properties": {"resource_id": {"type": "string"}, "expected_version": {"description": "知识根版本或Skill内容SHA256"}}}),
        P("operation_id", description="稳定操作 ID，partial 时保持原参数重试"),
    ), (R("operation_id", description="本次迁移操作 ID，续办时原样传回"),
        R("status", description="completed 表示迁移及投影完成；partial 表示尚未全部完成"),
        R("committed", "array", "已提交迁移的逐项结果，包含资源 ID、地址和版本信息"),
        R("pending", "array", "尚未提交迁移的资源 ID 列表"),
        R("projected", "boolean", "已提交迁移的索引等投影是否完成"),
        P("error", description="本次迁移或投影未全部完成的原因")),
       mutates=True, target="project_id"),
    _d("CheckDeviceSsh", (On("resource_id", "device"),), "测试设备 SSH 连接；需要设备 viewer，不保存凭据。", (
        R("resource_id", description="要检测 SSH 连接的设备统一 ID"), P("timeout_seconds", "integer", "超时秒数，默认15", minimum=1, maximum=45),
    ), (R("ok", "boolean", "SSH 连接检测是否成功"), P("error", description="连接检测失败的原因")), target="resource_id"),
    _d("RunDeviceCommand", (On("resource_id", "device"),), "使用设备绑定 SSH 身份执行远端命令（30秒），需要设备 admin。", (
        R("resource_id", description="执行远端命令的设备统一 ID"), R("command", description="远端命令，最多8192字符", max_length=8192),
    ), (R("exit_code", "integer", "远端命令的退出码"), R("output", description="合并输出，最多32KiB"),
        R("truncated", "boolean", "输出是否因长度限制被截断")), mutates=True, target="resource_id"),
    _d("UpdateDeviceSshCredential", (On("resource_id", "device"),), "保存设备 SSH 密码，需要设备 admin，结果不回显凭据。", (
        R("resource_id", description="要保存 SSH 凭据的设备统一 ID"), R("password", description="SSH密码"),
    ), (R("resource_id", description="本次请求的设备统一 ID"), R("via_resource_id", description="实际承载 SSH 凭据的设备资源 ID"),
        R("credential_saved", "boolean", "操作后是否已保存 SSH 密码凭据，不含密码内容")), mutates=True, target="resource_id"),
    _d("DeleteDeviceSshCredential", (On("resource_id", "device"),), "删除设备 SSH 密码凭据，需要设备 admin。", (
        R("resource_id", description="要删除 SSH 凭据的设备统一 ID"),
    ), (R("resource_id", description="本次请求的设备统一 ID"), R("via_resource_id", description="实际承载 SSH 凭据的设备资源 ID"),
        R("credential_saved", "boolean", "操作后是否仍保存 SSH 密码凭据"),
        R("deleted", "boolean", "本次是否删除了已有密码凭据")), mutates=True, target="resource_id"),
    _d("UpdateWorkbenchPage", (On("resource_id", "workbench"),), "保存页面生成结果，保留生成任务归属及页面基线检查。", (
        R("resource_id", description="目标工坊资源的统一 ID"), R("job_id", description="产生本次页面结果的生成任务 ID"),
        R("base_revision", "integer", "生成任务所依据的页面版本，用于防止覆盖并发修改"),
        P("title", description="页面标题；省略时保留原值"), P("description", description="页面描述；省略时保留原值"),
        R("html", description="要保存的完整页面 HTML"),
    ), (R("revision", "integer", "保存成功后的工坊版本号"),), mutates=True, target="resource_id"),
    _d("UpdateWorkbenchReversePlan", (On("resource_id", "workbench"),), "保存反向整理计划，核验原任务与提交者。", (
        R("resource_id", description="目标工坊资源的统一 ID"), R("job_id", description="产生本次反向整理计划的任务 ID"),
        R("plan", "object", "反向整理计划，按工坊领域定义校验并保存"),
    ), (R("revision", "integer", "保存成功后的工坊版本号"),), mutates=True, target="resource_id"),
    _d("CreateUpload", FIXED, "开始一次文件上传，返回上传句柄和写入字节的数据通道地址。", (
        R("purpose", description="已登记的上传用途标识；应用用途由已安装领域声明，未知用途拒绝"),
        P("target", description="目标工作区或资源地址"),
        R("file_name", description="文件名"),
        R("media_type", description="媒体类型"),
        R("size", "integer", "字节数", minimum=0),
    ), (
        R("upload_id", description="上传 ID"),
        R("ready", "boolean", "上传 ID 是否已经可用"),
        P("upload_url", description="写入字节的数据通道地址"),
        P("expires_at", description="数据通道到期时间"),
    ), mutates=True),
    _d("CreateUploadFromFile", FIXED, "按调用者当前权限读取已有资源文件，复制为已完成的上传；不创建字节上传通道。", (
        R("purpose", description="已登记的上传用途标识；应用用途由已安装领域声明，未知用途拒绝"),
        R("source_address", description="已有资源文件的地址"),
        P("target", description="目标工作区或资源地址"),
    ), (R("upload_id", description="上传 ID"), R("ready", "boolean", "上传已完成，固定 true")), mutates=True),
    _d("UpdateWorkspaceFile", (On("address"),), "把文本内容写入工作区中的一个文件。", (
        R("address", description="<工作区ID>/file/<相对路径>；不接受绝对路径，也不接受以 . 开头或 secrets、credentials 等敏感路径"),
        R("content", description="文件文本内容", allow_empty=True),
    ), (
        R("version", description="写入后的内容摘要"),
        R("modified_at", description="修改时间", replay=True),
    ), mutates=True, target="address"),
)
