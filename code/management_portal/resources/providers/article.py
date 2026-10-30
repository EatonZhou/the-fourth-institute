from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .common import canonical_record, canonical_resolution, registration_project_ids

if TYPE_CHECKING:
    from ..service import ResourceService


def install_resolver(service: "ResourceService") -> None:
    from management_portal import article_sections, resource_identity_backend

    service.register_resolver(
        "article",
        lambda address, **context: canonical_resolution(
            article_sections.resolve_article_address(address)
        ),
        read_content=resource_identity_backend.read_resolved_resource_content,
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
        del actor
        result = article_registration.unregister_article(resource_id)
        from management_portal import resource_auto_registration

        resource_auto_registration.suppress_paths(
            "article",
            resource_id,
            result.get("manuscript_paths") or [],
        )
        return canonical_record(resource_id, result)

    service.register_mutation_provider(
        "article",
        register=register,
        unregister=unregister,
    )
