"""Resource action catalog: each resource type's own actions (decision_ad3e21094f14).

Actions run through ``RunResourceAction``.  Registering, updating and
unregistering use the general resource instructions; this catalog lists only
deterministic domain operations those instructions cannot express.
"""
from __future__ import annotations

from functools import lru_cache

from .contracts import InstructionError
from .declarations import P, R, ResourceActionDeclaration as A
from management_common.knowledge_resource_types import MARKDOWN_REGISTERABLE_RESOURCE_TYPES


ACTIONS = (
    *(A(kind, "knowledge.file.rename", "重命名知识条目的 Markdown 主文件；同目录改名，保留资源 ID、标题、正文和附件位置。原根 ID 仍可读取，直接文件路径须使用返回的新名称。", (
        R("filename", description="完整新文件名，必须为不含目录的可见 .md 文件；拒绝覆盖已有文件"),
        R("expected_version", "integer", "当前知识根资源版本号；版本冲突不改文件", minimum=1),
        P("operation_id", description="稳定操作 ID；partial 后以相同参数重试，不重复推进版本"),
    ), (R("resource_id"), R("address"), R("filename"), R("version", "object"),
        R("operation_id"), R("status", description="completed 或 partial"),
        P("retryable", "boolean", "false 表示准备已撤回，须回读后用新操作 ID 发起"), P("error")))
      for kind in MARKDOWN_REGISTERABLE_RESOURCE_TYPES),
    *(A(kind, "knowledge.file.create", "在知识条目的已登记附件目录内新增文本文件；保留主文档和资源 ID，推进条目版本并同步在线文件列表。已有文件用 UpdateResource 更新。", (
        R("filename", description="不含目录的文本文件名，例如 usage.md；拒绝覆盖已有文件"),
        R("text", description="附件全文"),
        R("expected_version", "integer", "ReadResource 返回的当前版本号", minimum=1),
    ), (R("resource_id"), R("address"), R("version", "object")))
      for kind in MARKDOWN_REGISTERABLE_RESOURCE_TYPES),
    A("project", "knowledge.move", "将一批知识条目和 Skill 迁入目标 Project；保留原 ID、正文、附件和包内相对路径，统一更新归属、版本、目录登记、权限和在线索引。成功返回时可按原 ID 读取。", (
        P("operation_id", description="本批稳定操作 ID；省略时由调用者、目标及条目版本列表生成；恢复重试时保持相同参数，不能复用于不同请求"),
        R("items", "array", "待迁移知识条目或 Skill，必须提供当前 expected_version；最多 100 项；按实际登记范围迁移主文件、伴随文件或 Skill 包，限同一文件系统。含独立资源、Workspace 或保留登记的目录须由其 owner 协调；Experiment 和代码层 Core Skill 不属于此动作", items={
            "type": "object", "required": ["resource_id", "expected_version"],
            "properties": {"resource_id": {"type": "string"}, "expected_version": {
                "description": "ReadResource 返回的版本：Knowledge 为正整数或含 number 的版本对象；Skill 为 64 位小写十六进制 SHA256"}},
        }),
    ), (R("operation_id"), R("status", description="completed 或 partial；partial 须按原参数重试"),
        R("committed", "array", "已经提交的资源及当前路径、项目、版本"),
        R("pending", "array", "尚未提交的资源 ID"), R("projected", "boolean", "本批在线索引是否已同步"), P("error"))),
    A("device", "ssh.test", "测试到设备的 SSH 连接。", (
        P("timeout_seconds", "integer", "超时秒数，默认 15", minimum=1, maximum=45),
    ), (R("ok", "boolean"), P("error")), mutates=False),
    A("device", "ssh.run", "使用此设备绑定的 SSH 身份在远端执行命令（30 秒）。", (
        R("command", description="远端 shell 命令，最多 8192 个字符"),
    ), (R("exit_code", "integer"), R("output", description="合并输出，最多 32 KiB"), R("truncated", "boolean"))),
    A("device", "ssh.credential.save", "保存设备的 SSH 密码凭据（不回显内容）。", (
        R("password", description="SSH 密码"),
    ), (R("resource_id"), R("via_resource_id"), R("credential_saved", "boolean"))),
    A("device", "ssh.credential.delete", "删除设备的 SSH 密码凭据。", (), (
        R("resource_id"), R("via_resource_id"), R("credential_saved", "boolean"), R("deleted", "boolean"),
    )),
    A("workbench", "generation.save", "保存一次页面生成结果。", (
        R("job_id"), R("base_revision", "integer", "生成所依据的页面修订号"),
        P("title"), P("description"), R("html"),
    ), (R("revision", "integer"),)),
    A("workbench", "reverse.plan.save", "保存一份反向整理计划。", (
        R("job_id"), R("plan", "object"),
    ), (R("revision", "integer"),)),
)


@lru_cache(maxsize=1)
def resource_action_catalog() -> dict[tuple[str, str], A]:
    catalog: dict[tuple[str, str], A] = {}
    for action in ACTIONS:
        key = (action.resource_type, action.action)
        if key in catalog:
            raise RuntimeError(f"资源动作重复声明：{key}")
        catalog[key] = action
    return catalog


def resource_action(resource_type: str, action: str) -> A:
    try:
        return resource_action_catalog()[(resource_type, action)]
    except KeyError:
        raise InstructionError(
            "instruction.invalid_arguments",
            f"资源类型 {resource_type} 没有声明动作 {action}",
        ) from None


def actions_for_type(resource_type: str) -> list[A]:
    return [action for (kind, _name), action in resource_action_catalog().items() if kind == resource_type]
