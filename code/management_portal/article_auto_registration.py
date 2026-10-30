"""Article's discovery and classification contribution to resource registration."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from management_portal import article_discovery, article_registry
from management_portal.resources.registry_store import get_registry_store


class ArticleRegistrationSource:
    """Keep Article storage knowledge in the optional Article feature."""

    @staticmethod
    def records() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        records: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        documents = get_registry_store().documents(article_registry.ARTICLE_COLLECTION)
        for document_id, document in documents.items():
            try:
                records.append(article_registry._validated_article(document_id, document))
            except Exception as exc:
                errors.append({
                    "resource_type": "article",
                    "resource_id": str(document_id or "").strip(),
                    "path": "",
                    "operation": "read",
                    "error": str(exc)[:1000],
                })
        return records, errors

    @staticmethod
    def discover_roots(
        project_roots: Iterable[str | Path],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        return article_discovery.discover_article_roots(project_roots)

    @staticmethod
    def is_storage_path(path: str | Path) -> bool:
        return article_registry.is_article_storage_path(path)


SOURCE = ArticleRegistrationSource()
