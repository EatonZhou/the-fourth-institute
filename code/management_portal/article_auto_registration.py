"""Article's discovery and classification contribution to resource registration."""
from __future__ import annotations

from copy import deepcopy
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

    @staticmethod
    def prepare_restored_document(
        root: str | Path, *, article_id: str, title: str, created_at: str,
        project_id: str, recovery: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Describe a prepared partial manuscript without registering it.

        The resource owner retains its transaction, permission synchronization,
        recovery journal and retention switch. Article owns its representation.
        """
        item = article_discovery.inspect_article_root(root, primary_file='main.md')
        if item is None:
            raise ValueError('Prepared partial manuscript cannot be registered')
        item['title'] = title
        document = article_registry._merge_record({'created_at': created_at},
            article_id, [item], explicit_project_ids=[project_id])
        document['recovery'] = deepcopy(recovery)
        return document, article_registry.ARTICLE_REGISTRY.stored(document)


SOURCE = ArticleRegistrationSource()
