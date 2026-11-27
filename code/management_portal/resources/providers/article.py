from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .common import canonical_record, canonical_resolution, registration_project_ids

if TYPE_CHECKING:
    from ..service import ResourceService


_TEXT_TYPES = ["string", "number", "boolean", "array", "object", "null"]
REGISTER_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
    "description": "登记 Project 内的 Article 目录。自动发现仅提交 path；普通调用必须声明实际项目归属，领域检查位置及权限。未使用扩展字段仍忽略。",
    "required": ["path"], "additionalProperties": True,
    "properties": {
        "path": {"type": _TEXT_TYPES, "pattern": r"\S", "not": {"enum": [None, False, 0, [], {}]},
                 "description": "Article 目录路径；文本转换后非空，实际位置必须在已登记 Project 内。"},
        "project_ids": {"type": _TEXT_TYPES, "description": "普通调用必需的项目列表或逗号分隔文本，须与实际目录归属相同；自动发现可省略。@public 仅可单独出现。"},
        "primary_file": {"type": _TEXT_TYPES, "description": "相对目录的主文件；省略由领域选择，必须是目录内已有文件。沿用文本转换。"},
        "compiler": {"type": _TEXT_TYPES, "description": "TeX 主文件可用 pdflatex、xelatex、lualatex；其他格式沿用忽略该字段的行为。省略使用领域默认。"},
    },
}


def install_resolver(service: "ResourceService") -> None:
    from management_portal import article_sections

    service.register_resolver(
        "article",
        lambda address, **context: canonical_resolution(
            article_sections.resolve_article_address(address)
        ),
        read_content=article_sections.read_resolved_resource_content,
    )


def install_mutation_provider(service: "ResourceService") -> None:
    from management_portal import article_registration, resource_identity_backend

    def register(
        payload: dict[str, Any],
        *,
        actor: str = "",
        automatic: bool = False,
    ) -> dict[str, Any]:
        del actor
        path = str(payload.get("path") or "").strip()
        if not path:
            raise ValueError("article directory path required")
        located = resource_identity_backend.project_ids_for_path(path)
        if not located:
            raise ValueError("Article 目录必须位于已登记的 Project 内")
        projects = registration_project_ids(payload.get("project_ids"))
        if not automatic and sorted(set(projects)) != sorted(set(located)):
            # The location a caller asks permission for is where the directory really is.
            raise ValueError(f"Article 需要在 project_ids 中写明目录所在的 Project：{'、'.join(sorted(set(located)))}")
        options: dict[str, Any] = {
            "project_ids": projects,
            "primary_file": str(payload.get("primary_file") or "").strip(),
            "compiler": str(payload.get("compiler") or "").strip(),
        }
        record = article_registration.register_article(path, **options)
        if not automatic:
            from management_portal import resource_auto_registration

            resource_auto_registration.clear_suppressed_path("article", path)
        return canonical_record(str(record.get("id") or ""), record)

    def unregister(
        resource_id: str,
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        result = article_registration.unregister_article(resource_id, actor=actor)
        return canonical_record(resource_id, result)

    service.register_mutation_provider(
        "article",
        register_contract=REGISTER_CONTRACT,
        register=register,
        unregister=unregister,
    )
