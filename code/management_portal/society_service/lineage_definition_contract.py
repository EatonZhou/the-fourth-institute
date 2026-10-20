from __future__ import annotations

import copy
from typing import Any, Mapping

from . import identity_contract, lineage_state_domains


LINEAGE_DEFINITION_SCHEMA = "lineage-folder-v3"
LINEAGE_MEMBERSHIP_VALUES = frozenset({"formal", "personal"})
LINEAGE_DEFINITION_FIELDS = frozenset({
    "schema",
    "schema_version",
    "lineage_id",
    "lineage_membership",
    "project_id",
    "title",
    "description",
    "leader_resident_id",
    "principal_hierarchy",
    "run_as_principal",
    "nodes",
    "edges",
    "program",
    "function_blocks",
    "fragments",
    "revision",
    "layout_revision",
    "created_at",
    "updated_at",
    "graph_updated_at",
    "layout_updated_at",
    "created_by",
    "deleted_at",
    "deleted_by",
})
LINEAGE_DEFINITION_REQUIRED_FIELDS = frozenset({
    "schema",
    "schema_version",
    "lineage_id",
    "lineage_membership",
    "project_id",
    "title",
    "description",
    "leader_resident_id",
    "nodes",
    "program",
    "revision",
    "layout_revision",
    "created_at",
    "updated_at",
    "graph_updated_at",
    "layout_updated_at",
    "created_by",
})


def validate_definition(
    raw: Mapping[str, Any],
    *,
    path: str | None = None,
) -> dict[str, Any]:
    """Validate the one canonical persisted LineageDefinition representation."""

    definition = copy.deepcopy(dict(raw))
    unknown = set(definition) - LINEAGE_DEFINITION_FIELDS
    if unknown:
        raise ValueError(
            "Lineage definition contains unsupported fields"
            + (f" at {path}" if path is not None else "")
            + ": "
            + ", ".join(sorted(unknown))
        )
    missing = LINEAGE_DEFINITION_REQUIRED_FIELDS - set(definition)
    if missing:
        raise ValueError(
            "Lineage definition is missing required fields"
            + (f" at {path}" if path is not None else "")
            + ": "
            + ", ".join(sorted(missing))
        )
    lineage_id = str(definition.get("lineage_id") or "").strip()
    identity_contract.validate_id(lineage_id, "lineage")
    if str(definition.get("schema") or "") != LINEAGE_DEFINITION_SCHEMA:
        raise ValueError(
            "Lineage definition does not match current contract"
            + (f": {path}" if path is not None else "")
        )
    if "principal_hierarchy" in definition:
        from .principal_hierarchy import lineage_scope

        lineage_scope(definition["principal_hierarchy"], project_id=str(definition.get("project_id") or ""))
    if definition.get("schema_version") != 1:
        raise ValueError("Lineage definition schema_version must be 1")
    if "run_as_principal" in definition:
        from .lineage_automation_identity import normalize
        if normalize(definition["run_as_principal"]) != definition["run_as_principal"]:
            raise ValueError("Lineage run_as_principal must use a canonical User identity")
    membership = str(definition.get("lineage_membership") or "").strip()
    if membership not in LINEAGE_MEMBERSHIP_VALUES:
        raise ValueError("Lineage membership is invalid")
    if lineage_state_domains.definition_projection(definition) != definition:
        raise ValueError(
            "Lineage definition contains runtime state or empty optional fields"
        )
    return definition
