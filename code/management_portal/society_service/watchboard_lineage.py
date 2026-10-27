"""Read-only Watchboard projection over a Lineage definition.

The components a Resident's Watchboard installed and their identities.  The
installation plan built from them is ``resident_watchboard_domain.installation_plan``.
No Watchboard-specific mutation or runtime is owned here.
"""
from __future__ import annotations

import hashlib


def root(record):
    from . import resident_record_domain
    return resident_record_domain.resident_owned_workspace_path(record) / "watchboard"


def prefix(record):
    return "watchboard." + str(record["resident_id"]) + "."


def definition(record):
    from .lineage_domain import _lineage_catalog_store
    from . import resident_lineage
    lineage_id = resident_lineage.lineage_id(record)
    if not lineage_id:
        return {"revision": 0, "program": {}}
    cache_key = "watchboard-definition:" + lineage_id
    cached = resident_lineage.cached_access_projection(cache_key)
    if cached is not None:
        return cached
    graph = _lineage_catalog_store().get_state(lineage_id)[0]
    resident_lineage.remember_access_projection(cache_key, graph)
    return graph


def components(record, graph=None):
    program = (graph if graph is not None else definition(record)).get("program") or {}
    return [item for item in program.get("components", [])
            if str(item.get("client_key") or "").startswith(prefix(record))]


def installed(record):
    return any(item.get("client_key") == prefix(record) + "view" for item in components(record))


def component_id(record, kind, key, revision):
    # A removed object's identity remains tombstoned. Each installation gets
    # identities scoped to the definition revision used by its guarded plan.
    token = hashlib.sha256((prefix(record) + str(revision) + ":" + key).encode()).hexdigest()[:20]
    return kind + "_" + token


def command_ids(record, command, graph=None):
    key = "command." + hashlib.sha256(command.encode()).hexdigest()[:20]
    current = {item.get("client_key"): item["id"] for item in components(record, graph)}
    return {"event_source_id": current.get(prefix(record) + key + ".trigger", ""),
            "effect_id": current.get(prefix(record) + key + ".effect", ""), "key": key}
