"""Resource instructions (decision_ad3e21094f14), executed by ResourceService."""
from __future__ import annotations

from ..declarations import CURSOR, FIXED, NEXT_CURSOR, InstructionDeclaration as D, On, P, R


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target="", one_of=()):
    return D(name=name, category="resource", owner="resource", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates,
             target=target, one_of=one_of, decides=decides)


RESOURCE_RECORD = R("resource", "object", "资源记录")

DECLARATIONS = (
    _d("SearchResources", FIXED, "按关键词、类型、项目和状态查找调用者可以看到的资源。", (
        P("q", description="关键词"),
        P("resource_type", description="资源类型"),
        P("project_id", description="所属项目 ID"),
        P("status", description="资源状态"),
        P("limit", "integer", "每页最多返回的条数，默认 50", minimum=1, maximum=500),
        CURSOR(),
    ), (
        R("items", "array", "资源摘要：统一 ID、类型、标题、摘要、所属项目和打开地址"),
        R("total", "integer", "命中总数"),
        NEXT_CURSOR(),
    )),
    _d("ResolveResource", (On("address"),), "把一个资源地址解析为资源记录，并说明调用者可以对它执行哪些操作。", (
        R("address", description="统一 ID，或统一 ID 加内部路径"),
    ), (
        RESOURCE_RECORD,
        P("target", "object", "地址中的内部目标"),
        R("operations", "array", "调用者当前可以执行的操作"),
    ), target="address"),
    _d("ReadResource", (On("address"),), "读取资源正文，或读取文件夹资源里的文件和目录：Workspace、Resident（Resident Folder）和 Lineage（Lineage Folder）的文件都用 <资源ID>/file/<相对路径> 寻址；读目录（含 <资源ID>/file 本身）返回它的直接子项。", (
        R("address", description="统一 ID，或 <资源ID>/file/<相对路径>"),
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
        P("entries", "array", "目录的直接子项，最多 200 项：name、kind、size、modified_at、mtime_ns、path（相对路径）和 address"),
        P("truncated", "boolean", "目录子项或预览被截断"),
        P("hidden_count", "integer", "目录中按安全规则隐藏的子项数"),
        P("binary", "boolean", "preview 时文件不是文本"),
        P("allows", "object", "Workspace 文件的 UpdateWorkspaceFile：调用者能否保存这个文件"),
    ), target="address"),
    _d("GetResourceType", FIXED, "读取一个资源类型是否可以登记、修改和撤销登记，以及它提供哪些专用动作。", (
        R("resource_type", description="资源类型"),
    ), (
        R("registerable", "boolean", "提供者当前能否登记这种资源；未安装时为 false"),
        R("updatable", "boolean", "提供者当前能否修改这种资源"),
        R("unregisterable", "boolean", "提供者当前能否撤销登记这种资源"),
        R("actions", "array", "动作目录：名称、说明、参数定义、结果定义和是否改变状态"),
        R("type", description="资源类型"), R("enabled", "boolean", "类型是否已安装且已启用"),
        R("installed", "boolean", "提供该类型的功能单元是否已部署"),
        R("state", description="not_installed、enabled 或 disabled"),
        R("version", "integer", "类型启停状态的版本，可作 UpdateResourceType 的 expected_version"),
        R("instance_count", "integer", "索引中这种类型的资源数"), R("declaration", "object", "原始类型声明，独立于提供者运行能力"),
    )),
    _d("ListResourceTypes", FIXED, "列出全部资源类型声明、状态、实例数和提供者动作。", (), (
        R("types", "array", "每个资源类型的详情，各项内容同 GetResourceType 的结果"),
        R("registry", "object", "类型登记表的元数据：schema_version、ID 格式与特例 ID 规则"),
    )),
    _d("UpdateResourceType", FIXED, "启用或停用资源类型，已有实例保留。调用信封的 expected_version 给出类型状态的 version 时按它比较；省略时直接更新。", (
        R("resource_type", description="要启用或停用的资源类型"), R("enabled", "boolean", "true 启用，false 停用"),
    ), (R("type", description="被修改的资源类型"), R("enabled", "boolean", "修改后是否启用"),
        R("state", description="修改后的状态：enabled 或 disabled"),
        R("version", "integer", "修改后类型启停状态的版本，可作下一次修改的 expected_version")),
       mutates=True, target="resource_type"),
    _d("RegisterResource", (On("resource_type", place={"project": ("fields.project_id", "fields.project_ids")}),), "按资源类型的要求登记一个新资源，调用者记为它的创建者（created_by）。", (
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
        R("changes", "object", "要修改的字段；Workspace 只接受 title 和 expected_version（也可用请求顶层 expected_version）"),
    ), (
        RESOURCE_RECORD,
        R("version", description="类型提供的版本号或修改时间；未提供时为空字符串"),
    ), mutates=True, target="resource_id"),
    _d("UnregisterResource", (On("resource_id"),), "撤销一个资源的登记。", (
        R("resource_id", description="资源统一 ID"),
    ), (
        R("resource_id", description="被撤销的统一 ID"),
        P("index_projection", "object", "若索引通知未完成，保留未确认状态及原因；登记注销已提交"),
    ), mutates=True, target="resource_id"),
    # Whether an action changes state is declared by the action itself.
    _d("RunResourceAction", (On("resource_id"),), "执行资源类型在动作目录中声明的一个专用动作。", (
        R("resource_id", description="资源统一 ID"),
        R("action", description="动作名，写作“对象.动词”"),
        P("arguments", "object", "动作参数"),
    ), (
        P("result", "any", "由该动作的声明定义"),
    ), mutates=True, target="resource_id"),
    _d("CreateUpload", FIXED, "开始一次文件上传，返回上传句柄和写入字节的数据通道地址。", (
        R("purpose", description="用途", enum=(
            "conversation_attachment", "post_image", "paper_pdf", "article_file", "figure_input", "avatar",
        )),
        P("target", description="目标工作区或资源地址"),
        P("source_address", description="已有的工作区文件地址；提供时直接复制"),
        P("file_name", description="文件名"),
        P("media_type", description="媒体类型"),
        P("size", "integer", "字节数", minimum=0),
    ), (
        R("upload_id", description="上传 ID"),
        R("ready", "boolean", "上传 ID 是否已经可用"),
        P("upload_url", description="写入字节的数据通道地址"),
        P("expires_at", description="数据通道到期时间"),
    ), mutates=True, one_of=(("source_address",), ("file_name", "media_type", "size"))),
    _d("DeleteUpload", FIXED, "由上传者本人删除上传；已被引用的上传删除后，引用随之失效。", (
        R("upload_id", description="上传 ID"),
    ), (
        R("upload_id", description="被删除的上传 ID"),
    ), mutates=True, target="upload_id"),
    _d("UpdateWorkspaceFile", (On("address"),), "把文本内容写入工作区中的一个文件。", (
        R("address", description="<工作区ID>/file/<相对路径>；不接受绝对路径"),
        R("content", description="文件文本内容", allow_empty=True),
    ), (
        R("version", description="写入后的内容摘要"),
        R("modified_at", description="修改时间"),
    ), mutates=True, target="address"),
)
