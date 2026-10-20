from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .common import canonical_record, canonical_resolution, explicit_project_ids, registration_project_ids
from ..errors import GitCheckoutRequired

if TYPE_CHECKING:
    from ..service import ResourceService


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
            raise ValueError("repo checkout path required")
        article_source = portal_features.contribution("resource.article_registration")
        if article_source is not None and article_source.is_storage_path(path):
            raise ValueError(
                "Article storage uses article_* and cannot be registered as repo_*"
            )
        locations = payload.get("project_ids")
        options: dict[str, Any] = {
            "project_ids": registration_project_ids(locations) if automatic else explicit_project_ids(locations, "Repo"),
        }
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
        del actor
        result = resource_identity_backend.unregister_repo(resource_id)
        from management_portal import resource_auto_registration

        resource_auto_registration.suppress_paths(
            "repo",
            resource_id,
            result.get("checkout_paths") or [],
        )
        return canonical_record(resource_id, result)

    def update(
        resource_id: str,
        payload: dict[str, Any],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        del actor
        operation = str(payload.get("operation") or "set_remote").strip()
        if operation == "set_remote":
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
        register=register,
        unregister=unregister,
        update=update,
    )
