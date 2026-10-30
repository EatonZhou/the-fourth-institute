"""Article owns validation and projection of its registered resource records."""
import hashlib
from pathlib import Path

from management_portal import article_registry
from management_portal.resource_index_backend import resource_record
from management_common.text_values import stripped_text

# Snapshot the loaded projection; later disk edits take effect after reload.
PROJECTION_CODE_FINGERPRINT = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class ArticleRegistryProjection:
    resource_type = "article"
    collection = article_registry.ARTICLE_COLLECTION

    def validate(self, resource_id, raw):
        return article_registry._validated_article(resource_id, raw)

    def project(self, item, *, adapter):
        resource_id = stripped_text(item.get("resource_id") or item.get("id"))
        projects = [stripped_text(value) for value in item.get("project_ids") or [] if stripped_text(value)]
        roots = [value for value in item.get("roots") or [] if isinstance(value, dict)]
        return resource_record(
            resource_id=resource_id, resource_type="article",
            title=item.get("title") or resource_id, summary=item.get("summary"),
            adapter=adapter, modified_at=item.get("updated_at") or item.get("created_at"),
            facets=["article"],
            attributes={
                "project_ids": projects, "canonical_root": stripped_text(item.get("canonical_root")),
                "primary_file": stripped_text(item.get("primary_file")), "status": stripped_text(item.get("status")),
            },
            search_terms=[*projects, item.get("canonical_root"), item.get("primary_file"),
                          *[stripped_text(value.get("path")) for value in roots]],
        )


REGISTRY_PROJECTION = ArticleRegistryProjection()
