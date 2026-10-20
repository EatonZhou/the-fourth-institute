"""Closed publishing read surface, with no publishing-specific ACL.

These reads are release business operations. They do not change the ordinary
resource/ISA authorization policy or accept a caller-supplied principal.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .content import registered_content_path
from .database import read_snapshot
from .sanitize import private_path, scrub


def directories(arguments):
    path = Path(arguments.get("path") or "/").expanduser().resolve(strict=True)
    if not path.is_dir() or private_path(path):
        raise ValueError("Not an available release directory")
    after = str(arguments.get("after") or "")
    items = [{"name": child.name, "path": str(child)} for child in sorted(path.iterdir())
             if child.name > after and not private_path(child.name) and not child.is_symlink() and child.is_dir()]
    return {"path": str(path), "parent": str(path.parent) if path != path.parent else None,
            "directories": items[:200], "next_after": items[199]["name"] if len(items) > 200 else ""}


def _json_times(value):
    if isinstance(value, datetime):
        from management_common.database_time import iso_time
        return iso_time(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_times(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_times(item) for item in value]
    return value


def candidate(operation, arguments):
    return _json_times(_candidate(operation, arguments))


def _candidate(operation, arguments):
    if operation in {'ListPermissionSchemes', 'GetPermissionScheme', 'ListPermissionEntries'}:
        from .permissions import read_candidate
        return read_candidate(operation, arguments)
    if operation == 'release_features':
        from .features import resolve_features, structural_requirements
        with read_snapshot() as source:
            from .resource_dependencies import resolve
            dependencies = resolve(arguments.get('snapshot') or {}, source)
            resolution = resolve_features(arguments.get('snapshot') or {}, source=source, dependencies=dependencies,
                                          required=set(dependencies['features']) - set(structural_requirements(arguments.get('snapshot') or {})))
            resolution['resource_dependencies'] = dependencies
        return {**resolution.pop('catalog'), 'resolution': resolution}
    if operation == 'release_targets':
        from management_common.distribution_targets import target_catalog, resolve_targets
        return {**target_catalog(), 'resolution': resolve_targets(arguments.get('snapshot') or {}, arguments.get('features') or [])}
    if operation == "directories":
        return directories(arguments)
    allowed = {"SearchResources", "ResolveResource", "GetProject", "ListResourceTypes", "GetLineage",
               "ListTemplates", "GetTemplate", "ListPermissionSchemes", "GetPermissionScheme", "ListPermissionEntries",
               "resolve", "resolve_many", "read"}
    if operation not in allowed:
        raise ValueError("Operation is not part of release candidate reads")
    with read_snapshot() as source:
        if operation == "SearchResources":
            kind, query, project = arguments.get("resource_type"), str(arguments.get("q") or "").casefold(), arguments.get("project_id")
            index = source.read_index(resource_type=kind, query=query)
            values = [value for value in index.values() if (not kind or value["resource_type"] == kind)
                      and (not query or query in (value["resource_id"] + " " + value["title"]).casefold())
                      and (not project or project in value.get("attributes", {}).get("project_ids", []))]
            values.sort(key=lambda value: value["resource_id"])
            offset, limit = int(arguments.get("cursor") or 0), min(max(int(arguments.get("limit") or 30), 1), 200)
            return {"items": values[offset:offset + limit], "next_cursor": str(offset + limit) if offset + limit < len(values) else "", "total": len(values)}
        if operation == "resolve_many":
            index = source.read_index(resource_ids=[address.split('/', 1)[0] for address in arguments.get('addresses', [])[:200]])
            return {"results": [{"address": address, "ok": address.split("/", 1)[0] in index,
                                   "result": {"resource": index.get(address.split("/", 1)[0]), "readable": address.split("/", 1)[0] in index}}
                                  for address in arguments.get("addresses", [])[:200]]}
        if operation in {"ResolveResource", "resolve", "GetProject"}:
            address = arguments.get("address") or arguments.get("project_id")
            index = source.read_index(resource_ids=[address.split('/', 1)[0]])
            record = index.get(address.split("/", 1)[0])
            if not record:
                raise KeyError("Release candidate not found")
            return {"project" if operation == "GetProject" else "resource": record, "readable": True}
        if operation == "ListResourceTypes":
            root = Path(__file__).resolve().parents[2] / "config/resource-types"
            # Source checkout, not a guessed external registry.
            types = []
            for path in sorted(root.glob("*.json")):
                if path.name.startswith("_"):
                    continue
                declaration = json.loads(path.read_text())
                types.append({**declaration, "type": declaration.get("type", path.stem)})
            return {"types": types}
        if operation == "GetLineage":
            from management_portal.society_service.state_store import get_lineage_data_store
            identity = arguments.get("lineage_id") or "lineage_ed619225d417"
            if identity not in source.read_index(resource_ids=[identity]):
                raise KeyError('Release Lineage candidate not found')
            return {"lineage": get_lineage_data_store().lineage_definition(identity)}
        if operation in {"ListTemplates", "GetTemplate"}:
            values = source.rows("templates", "templates", predicates=None if operation == "ListTemplates" else {"template_id": [arguments["template_id"]]})
            if operation == "GetTemplate":
                if not values:
                    raise KeyError("Template not found")
                return {"template": scrub(values[0])}
            return {"templates": scrub(values)}
        if operation == "read":
            address = arguments["address"]
            identity, separator, child = address.partition("/file/")
            index = source.read_index(resource_ids=[identity])
            record = index.get(identity)
            if not record:
                raise KeyError("Release content not found")
            if record["resource_type"] == "workbench" and not separator:
                from management_portal.workbench_projections.store import Store
                # Read the authoritative version without Store.get's unrelated Workbench ACL.
                from management_portal.workbench_projections import tables
                with Store().connect() as connection:
                    row = connection.execute("SELECT * FROM apps WHERE id=%s", (identity,)).fetchone()
                    if not row:
                        raise KeyError("Workbench not found")
                    value = tables.manifest(connection, row, row["published"] or row["revision"])
                return {"encoding": "utf-8", "content": json.dumps(scrub(value), ensure_ascii=False, default=str)}
            path = registered_content_path(record)
            if path is None or not path.is_absolute() or private_path(path):
                raise ValueError("Resource does not expose release-readable content")
            root = path.resolve(strict=True)
            path = root
            if separator:
                if Path(child).is_absolute() or ".." in Path(child).parts or private_path(child):
                    raise ValueError("Content path escapes the resource")
                path = (root / child).resolve(strict=True)
                if not path.is_relative_to(root):
                    raise ValueError("Content path escapes the resource")
            if private_path(path):
                raise ValueError("Resource does not expose release-readable content")
            if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Candidate preview must be a text file of at most 4 MiB")
            text = scrub(path.read_text(encoding="utf-8"))
            return {"encoding": "utf-8", "content": text}
    raise ValueError("Unsupported candidate read")
