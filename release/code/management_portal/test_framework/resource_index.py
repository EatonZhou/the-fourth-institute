"""Test-owned resource index adapter; loaded only by the Test feature hook (``resource.index_adapters``)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from management_portal.resource_index_backend import _PathsAdapter, _timestamp_iso, resource_record
from management_portal.test_framework.catalog import ROOT
from management_portal.test_framework.catalog_source import catalog_paths

# Snapshot the loaded projection; later disk edits take effect after reload.
PROJECTION_CODE_FINGERPRINT = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class TestCatalogAdapter(_PathsAdapter):
    name = "test"
    resource_types = frozenset({"test"})

    def __init__(self) -> None:
        # The catalog is built from the test sources (catalog_source).
        self.cases_root = ROOT / "tests" / "cases"
        self.roots = (self.cases_root,)

    def source_paths(self) -> list[Path]:
        return catalog_paths(ROOT)

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from management_portal.resources.providers.test import catalog_repository

        records: list[dict[str, Any]] = []
        cases, resource_errors = catalog_repository().projection()
        modified: dict[Path, str] = {}

        def source_of(item: dict[str, Any]) -> Path:
            implementations = item["implementations"]
            return self.cases_root.parents[1] / implementations[0]["source"] if implementations else self.cases_root

        for case in cases:
            item = case.public()
            path = source_of(item)
            if path not in modified:
                try:
                    modified[path] = _timestamp_iso(path.stat().st_mtime)
                except OSError:
                    modified[path] = ""
            try:
                records.append(
                    resource_record(
                        resource_id=case.id,
                        resource_type="test",
                        title=item["title"],
                        summary=item["description"],
                        adapter=self.name,
                        path=path,
                        modified_at=modified[path],
                        facets=["test", *item["aspects"]],
                        attributes={
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
                        search_terms=[
                            item["key"],
                            item["module"],
                            *item["design_refs"],
                            item.get("design_point_ref") or "",
                        ],
                    )
                )
            except Exception as exc:
                resource_errors.append(
                    {
                        "resource_id": case.id,
                        "path": str(path),
                        "error": str(exc)[:1000],
                    }
                )
        return records, {
            "test_count": len(records),
            "resource_errors": resource_errors,
        }


ADAPTERS = (TestCatalogAdapter,)
