from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from management_portal.test_framework.catalog import ROOT, CatalogRepository

from .common import canonical_record, canonical_resolution
from ..portal import portal_url_for_address

if TYPE_CHECKING:
    from ..service import ResourceService
    from management_portal.test_framework.models import TestCase


def catalog_repository() -> CatalogRepository:
    return CatalogRepository(verify_sources=False)


def test_record(case: "TestCase") -> dict[str, Any]:
    item = case.public()
    return canonical_record(
        case.id,
        {
            "resource_type": "test",
            "title": item["title"],
            "summary": item["description"],
            "project_ids": ["AI员工"],
            "read_scope": "global",
            "status": item["lifecycle"],
            "key": item["key"],
            "module": item["module"],
            "module_path": item["module_path"],
            "importance": item["importance"],
            "aspects": item["aspects"],
            "is_design_point": item["is_design_point"],
            "design_refs": item["design_refs"],
            "design_point_ref": item.get("design_point_ref") or "",
            "implementation_count": len(item["implementations"]),
        },
    )


def _case(resource_id: str) -> "TestCase":
    return catalog_repository().get(resource_id)


def resolve_test_address(address: str, **_context: Any) -> dict[str, Any]:
    resource_id, separator, _suffix = str(address or "").strip("/").partition("/")
    if separator:
        raise KeyError(f"Test resources expose no nested address: {address}")
    case = _case(resource_id)
    return canonical_resolution(
        {
            "address": resource_id,
            "root_id": resource_id,
            "resource_type": "test",
            "target_kind": "test_case",
            "record": test_record(case),
            "detail": case.public(),
            "content_kind": "json",
            "filename": f"{resource_id}.json",
            # The code that declares the case: its first test source or its JSON file.
            "resolved_path": str(ROOT / case.implementations[0].source) if case.implementations else str(ROOT / "tests" / "cases"),
            "portal_url": portal_url_for_address(resource_id),
            "exists": True,
        }
    )


def read_test_content(resolved: dict[str, Any], **_context: Any):
    detail = resolved.get("detail")
    if not isinstance(detail, dict):
        raise ValueError("resolved Test resource has no detail")
    content = json.dumps(detail, ensure_ascii=False, indent=2).encode("utf-8")
    return content, "application/json", {"filename": resolved.get("filename")}


def install_resolver(service: "ResourceService") -> None:
    service.register_resolver(
        "test",
        resolve_test_address,
        read_content=read_test_content,
    )


__all__ = [
    "catalog_repository",
    "install_resolver",
    "read_test_content",
    "resolve_test_address",
    "test_record",
]
