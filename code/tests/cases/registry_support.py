"""Direct access to the resource registry for tests that simulate lost or external changes."""
from __future__ import annotations

from typing import Any

from management_portal.resources.registry_store import get_registry_store


def remove_registry_document(collection: str, document_id: str) -> None:
    with get_registry_store().writer(collection) as writer:
        assert writer.delete(collection, document_id), (collection, document_id)


def registry_document(collection: str, document_id: str) -> dict[str, Any] | None:
    return get_registry_store().get(collection, document_id)


def put_registry_document(collection: str, document_id: str, document: dict[str, Any]) -> None:
    with get_registry_store().writer(collection) as writer:
        writer.put(collection, document_id, document)


def write_projects(payload: dict[str, Any] | str) -> None:
    """Replace the Project registry with ``payload`` (a mapping or its YAML text)."""
    import yaml

    from management_portal import project_registry

    document = yaml.safe_load(payload) if isinstance(payload, str) else payload
    project_registry.replace({**project_registry.empty_registry(), **(document or {}), "schema_version": 1})


class ProjectRegistryYaml:
    """The Project registry read and written as the YAML document it used to be."""

    def read_text(self, encoding: str = "utf-8") -> str:
        from management_portal import project_registry

        return project_registry.yaml_text()

    def read_bytes(self) -> bytes:
        return self.read_text().encode("utf-8")

    def write_text(self, text: str, encoding: str = "utf-8") -> None:
        write_projects(text)
