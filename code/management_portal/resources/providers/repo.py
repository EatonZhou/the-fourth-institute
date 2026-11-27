from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .common import canonical_record, canonical_resolution, explicit_project_ids, registration_project_ids
from ..errors import GitCheckoutRequired

if TYPE_CHECKING:
    from ..service import ResourceService


_JSON_TYPES = ["string", "number", "boolean", "array", "object", "null"]
_PATH = {"type": _JSON_TYPES, "pattern": r"\S", "not": {"enum": [None, False, 0, [], {}]}, "description": "仓库路径；文本转换后非空，实际路径由领域检查。"}
REGISTER_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
    "description": "登记 checkout 或导出目录。普通调用必须提供归属；自动发现只提交 path。保留未使用扩展字段忽略行为。",
    "required": ["path"], "additionalProperties": True,
    "properties": {
        "path": _PATH,
        "source_kind": {"type": "string", "enum": ["git_checkout", "exported_tree"], "default": "git_checkout", "description": "git_checkout（省略时默认）或 exported_tree。"},
        "project_ids": {"type": _JSON_TYPES, "description": "项目列表或逗号分隔文本；普通调用必需，可用单独 @public。自动发现可省略；其他输入沿用空列表处理后由领域判断。"},
        "initialize_git": {"type": _JSON_TYPES, "description": "原有 Python 真值规则；真值允许显式初始化 Git；exported_tree 禁止真值。"},
    },
    "allOf": [{"if": {"required": ["source_kind"], "properties": {"source_kind": {"const": "exported_tree", "description": "选择导出目录分支。"}}},
               "then": {"properties": {"initialize_git": {"description": "导出目录不初始化 Git。", "enum": [None, False, 0, "", [], {}]}}}}],
}
_RECOVERY_FIELDS = {
    "reason": {"type": _JSON_TYPES, "pattern": r"\S", "not": {"enum": [None, False, 0, [], {}]}, "description": "显式恢复原因；文本转换后非空。"},
    "evidence_address": {"type": _JSON_TYPES, "description": "原成功 ResolveResource 回执的可读资源地址；由当前 caller 读取核实。"},
    "evidence_sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$", "description": "原回执内容的 SHA256。"},
    "checkout_moves": {"type": "array", "minItems": 1, "description": "完整的历史 checkout 到现目录映射；路径存在性与身份由领域核验。",
                       "items": {"type": "object", "required": ["from", "to"], "additionalProperties": True,
                                 "properties": {"from": {**_PATH, "description": "历史 checkout 绝对路径。"},
                                                "to": {**_PATH, "description": "当前 checkout 绝对路径。"}}}},
    "revision_evidence": {"type": ["object", "null"], "required": ["address", "sha256"], "additionalProperties": True,
                          "description": "可选冻结构建回执；null 表示不提供。领域核实原 Repo 与 commit。",
                          "properties": {"address": {"type": _JSON_TYPES, "description": "冻结构建回执地址。"},
                                         "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$", "description": "冻结回执 SHA256。"}}},
    "replacements": {"type": "array", "description": "待清理的重复登记及原内容摘要；领域检查权限和 CAS；省略使用空列表。",
                     "items": {"type": "object", "required": ["resource_id", "expected_sha256"], "additionalProperties": True,
                               "properties": {"resource_id": {"type": "string", "pattern": r"^repo_[0-9a-f]{12}$", "description": "重复 Repo 的资源 ID。"},
                                              "expected_sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$", "description": "重复登记原内容 SHA256。"}}}},
}
UPDATE_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
    "description": "默认 set_remote；保留原 operation 分支及忽略非当前分支字段的兼容。恢复权限、证据及 CAS 继续由领域核验。",
    "additionalProperties": True,
    "properties": {
        "operation": {"type": _JSON_TYPES, "pattern": r"^(?:|\s*(?:set_remote|set_policy|set_ssh_key|restore_registration)\s*)$", "description": "省略、null 或空值默认 set_remote；也支持 set_policy、set_ssh_key、restore_registration。"},
        "name": {"type": _JSON_TYPES, "description": "set_remote 的 remote 名，空值默认 origin，沿用文本转换。"},
        "url": {"type": _JSON_TYPES, "description": "set_remote fetch URL；新 remote 必填，既有 remote 可以仅改 push URL。"},
        "push_url": {"type": _JSON_TYPES, "description": "set_remote push URL；沿用文本转换，空值由原领域处理。"},
        "push_enabled": {"type": _JSON_TYPES, "description": "set_policy 是否允许推送；保留 Python 真值转换，省略为 false。"},
        "ssh_key": {"type": _JSON_TYPES, "description": "set_ssh_key 的主机 SSH 密钥引用，空值清除；存在性由领域核实。"},
        **{key: {"type": _JSON_TYPES, "description": value["description"] + " 仅 restore_registration 使用。"}
           for key, value in _RECOVERY_FIELDS.items()},
    },
    "allOf": [{"if": {"required": ["operation"], "properties": {"operation": {"type": "string", "pattern": r"^\s*restore_registration\s*$", "description": "选择恢复登记分支。"}}},
               "then": {"required": ["reason", "evidence_address", "evidence_sha256", "checkout_moves"],
                        "properties": _RECOVERY_FIELDS}}],
}


def install_resolver(service: "ResourceService") -> None:
    from management_portal import resource_identity_backend

    service.register_resolver(
        "repo",
        lambda address, **context: canonical_resolution(
            resource_identity_backend.resolve_repo_address(address)
        ),
        read_content=resource_identity_backend.read_resolved_resource_content,
    )


def install_mutation_provider(service: "ResourceService") -> None:
    from management_portal import portal_features, resource_identity_backend

    def register(
        payload: dict[str, Any],
        *,
        actor: str = "",
        automatic: bool = False,
    ) -> dict[str, Any]:
        del actor
        path = str(payload.get("path") or "").strip()
        if not path:
            raise ValueError("repo source path required")
        source_kind = resource_identity_backend.repo_source_kind(payload)
        if source_kind == "exported_tree" and payload.get("initialize_git"):
            raise ValueError("exported_tree cannot initialize Git")
        article_source = portal_features.contribution("resource.article_registration")
        if article_source is not None and article_source.is_storage_path(path):
            raise ValueError(
                "Article storage uses article_* and cannot be registered as repo_*"
            )
        locations = payload.get("project_ids")
        options: dict[str, Any] = {
            "project_ids": registration_project_ids(locations) if automatic else explicit_project_ids(locations, "Repo"),
        }
        if source_kind == "exported_tree":
            record = resource_identity_backend.register_exported_tree(path, **options)
        else:
            try:
                record = resource_identity_backend.register_repo_checkout(path, **options)
            except GitCheckoutRequired:
                if not bool(payload.get("initialize_git")):
                    raise
                resource_identity_backend.initialize_git_checkout(path)
                record = resource_identity_backend.register_repo_checkout(path, **options)
        if not automatic:
            from management_portal import resource_auto_registration

            resource_auto_registration.clear_suppressed_path("repo", path)
        return canonical_record(str(record.get("id") or ""), record)

    def unregister(
        resource_id: str,
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        result = resource_identity_backend.unregister_repo(resource_id, actor=actor)
        return canonical_record(resource_id, result)

    def update(
        resource_id: str,
        payload: dict[str, Any],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        operation = str(payload.get("operation") or "set_remote").strip()
        if operation == "restore_registration":
            from ..repo_recovery import restore_registration

            result = restore_registration(service, resource_id, payload, actor=actor)
        elif operation == "set_remote":
            result = resource_identity_backend.update_repo_remote(
                resource_id,
                name=str(payload.get("name") or "origin"),
                url=str(payload.get("url") or ""),
                push_url=str(payload.get("push_url") or ""),
            )
        elif operation == "set_policy":
            result = resource_identity_backend.update_repo_policy(
                resource_id,
                push_enabled=bool(payload.get("push_enabled")),
            )
        elif operation == "set_ssh_key":
            result = resource_identity_backend.update_repo_ssh_key(
                resource_id,
                ssh_key=str(payload.get("ssh_key") or ""),
            )
        else:
            raise ValueError(f"unsupported repo update operation: {operation}")
        return canonical_record(resource_id, result)

    service.register_mutation_provider(
        "repo",
        register_contract=REGISTER_CONTRACT,
        update_contract=UPDATE_CONTRACT,
        register=register,
        unregister=unregister,
        update=update,
    )
