"""Explicit application configuration; applications do not resolve arbitrary duties.

These targets select where an application's work goes. Authentication and
the permission system still govern the actual invocation. Summary submission
additionally requires the configured workflow executor and its claimed job.

Every application keeps one row in ``application_targets``, keyed by its
name; the row holds a Resident or a Lineage, as the application declares.
Core declares its own applications here.  An installed feature declares its
own with the ``application.targets`` hook: ``{name: {"target": "resident" |
"lineage", "admission": bool}}``; ``admission`` says the feature checks the
admission pause before it accepts new work.  The table has no vocabulary of
its own, so leaving a feature out changes no schema; its row stays unread.
"""
from __future__ import annotations

from management_common.operation_errors import OperationError
from management_common.portal_database import portal_database


SEARCH_SURFACES = ("knowledge", "skill", "repo", "article", "experiment", "paper", "wall")
CHAT_TYPES = ("knowledge", "skill", "repo", "article", "experiment", "device")
CORE_APPLICATIONS = {
    "resident_discovery": {"target": "resident"},
    "resident_summary": {"target": "resident"},
    **{"semantic_search:" + surface: {"target": "resident"} for surface in SEARCH_SURFACES},
    **{"resource_chat:" + kind: {"target": "lineage"} for kind in CHAT_TYPES},
}
CONFIGURATION_COLUMNS = ("revision", "admission_paused", "reason", "updated_by", "updated_at")
CONFIGURATION_DDL = ("revision INTEGER NOT NULL DEFAULT 1,admission_paused BOOLEAN NOT NULL DEFAULT FALSE,"
                     "reason TEXT NOT NULL DEFAULT '',updated_by TEXT NOT NULL DEFAULT '',updated_at TIMESTAMPTZ")
COLUMNS = {"application_targets": ("application", "resident_id", "lineage_id", *CONFIGURATION_COLUMNS)}


def _declaration(application: str) -> dict:
    declared = CORE_APPLICATIONS.get(application)
    if declared is None:
        # Only a feature's own application loads the features' declarations.
        from . import portal_features

        for _feature, applications in portal_features.contributions("application.targets"):
            declared = applications.get(application) or declared
    if declared is None:
        raise ValueError("Unknown application target")
    return declared


def target_field(application: str) -> str:
    """The column holding the application's target: ``resident_id`` or ``lineage_id``."""
    return _declaration(application)["target"] + "_id"


def supports_admission_pause(application: str) -> bool:
    return bool(_declaration(application).get("admission"))


class ApplicationUnconfigured(OperationError, LookupError):
    code = "instruction.not_found"
    http_status = 404
    commit_state = "not_committed"


def create_schema(connection) -> None:
    connection.execute(
        "CREATE TABLE application_targets (application TEXT PRIMARY KEY,"
        "resident_id TEXT REFERENCES residents(resident_id),lineage_id TEXT REFERENCES lineages(lineage_id),"
        f"{CONFIGURATION_DDL},CHECK((resident_id IS NULL) <> (lineage_id IS NULL)))"
    )


def _target_record(application: str, field: str) -> dict[str, str]:
    if target_field(application) != field:
        raise ValueError(f"{application} does not give its work to a {field.removesuffix('_id')}")
    # Readers of application configuration do not initialize or migrate the
    # entire Lineage schema. Long-lived callers can span an owner deployment.
    with portal_database(schema="lineage").read() as connection:
        if field == "lineage_id":
            query = ("SELECT i.lineage_id FROM application_targets t JOIN lineages i ON i.lineage_id=t.lineage_id "
                     "WHERE t.application=%s AND i.deleted_at IS NULL")
        else:
            query = ("SELECT i.resident_id, n.lineage_id FROM application_targets t "
                     "JOIN residents i ON i.resident_id=t.resident_id "
                     "JOIN lineage_nodes n ON n.node_id=i.resident_id JOIN lineages l ON l.lineage_id=n.lineage_id "
                     "WHERE t.application=%s AND i.deleted_at IS NULL "
                     "AND n.node_type='resident' AND n.deleted_at IS NULL AND l.deleted_at IS NULL")
        row = connection.execute(query, (application,)).fetchone()
    if not row:
        raise ApplicationUnconfigured(f"Application target is not configured: {application}")
    return {key: str(value) for key, value in dict(row).items()}


def resident(application: str) -> dict[str, str]:
    """The application's Resident and the Lineage it currently belongs to."""
    return _target_record(application, "resident_id")


def lineage(application: str) -> str:
    return _target_record(application, "lineage_id")["lineage_id"]


def discovery_resident() -> dict[str, str]:
    return resident("resident_discovery")


def summary_resident() -> str:
    return resident("resident_summary")["resident_id"]


def search_resident(surface: str) -> dict[str, str]:
    if surface not in SEARCH_SURFACES:
        raise ValueError("Unknown semantic search surface")
    return resident("semantic_search:" + surface)


def resource_chat_lineage(resource_type: str) -> str:
    if resource_type not in CHAT_TYPES:
        raise ValueError("Unknown resource chat type")
    return lineage("resource_chat:" + resource_type)
