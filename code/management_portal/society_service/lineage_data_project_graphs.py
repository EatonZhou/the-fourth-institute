"""Each Project's principal graph: its own members and the Leaders of its Lineages."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from typing import Any

from . import lineage_data_definitions, lineage_data_residents, lineage_metadata_storage


PROJECT_GRAPH_FILTER = "lineage_id IN (SELECT lineage_id FROM project_lineage_entries)"
# Projects whose principal graph a membership transaction must re-project
# when it finishes; None outside such a transaction.
_HIERARCHY_SYNC_PENDING: ContextVar[set[str] | None] = ContextVar(
    "lineage_hierarchy_sync_pending", default=None,
)


def _principal_graph_members(connection: Any, definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .principal_hierarchy import project_leaders

    lineage_id = str(definition["lineage_id"])
    members = connection.execute(lineage_data_residents.resident_select(
        'WHERE r.lineage_id=%s ORDER BY r.created_at, r.resident_id'
    ), (lineage_id,)).fetchall()
    result = [lineage_data_residents.resident_from_row(row) for row in members]
    if (definition.get("principal_hierarchy") or {}).get("level") == "project":
        if definition.get("leader_resident_id") not in {row["resident_id"] for row in result}:
            raise ValueError("Project leader must belong to its own principal graph")
        children = lineage_metadata_storage.read_many(connection)
        leaders = list(project_leaders(definition, children))
        rows = {
            str(row["resident_id"]): row
            for row in connection.execute(lineage_data_residents.resident_select(
                'WHERE r.resident_id = ANY(%s)'
            ), (leaders,)).fetchall()
        } if leaders else {}
        result.extend(
            lineage_data_residents.resident_from_row(rows[resident_id])
            for resident_id in leaders
            if resident_id in rows
        )
    return result


class ProjectGraphs:
    """Keeps every Project's principal graph equal to its members and Leaders."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def membership_transaction(self, operation: Callable[[], Any]) -> Any:
        """Validate derived Project references against the aggregate's final state."""
        def commit(connection: Any) -> Any:
            if _HIERARCHY_SYNC_PENDING.get() is not None:
                # The outermost membership transaction synchronizes.
                return operation()
            projects: set[str] = set()
            token = _HIERARCHY_SYNC_PENDING.set(projects)
            try:
                result = operation()
            finally:
                _HIERARCHY_SYNC_PENDING.reset(token)
            self.synchronize(connection, projects)
            return result

        return self.database.write(commit)

    def materialize_members(self, connection: Any, lineage_id: str) -> None:
        """Publish membership and its graph projection in the same transaction."""
        from .lineage_member_projection import project_resident_nodes

        # A write of the Project graph itself merges with this one at commit.
        connection.execute('SELECT 1 FROM lineages WHERE lineage_id=%s FOR NO KEY UPDATE', (lineage_id,))
        definition = lineage_data_definitions.definition_from_connection(connection, lineage_id)
        old_ids = {str(node["id"]) for node in definition["nodes"]}
        members = _principal_graph_members(connection, definition)
        if old_ids == {str(row["resident_id"]) for row in members}:
            if any("x" not in node or "y" not in node for node in definition["nodes"]):
                project_resident_nodes(definition, members)
                lineage_data_definitions.initialize_layout_positions(connection, lineage_id, definition)
            return
        if (definition.get("principal_hierarchy") or {}).get("level") == "project":
            removed = old_ids - {str(row["resident_id"]) for row in members}
            def references(value: object) -> bool:
                if isinstance(value, str):
                    return value in removed
                if isinstance(value, dict):
                    return any(references(item) for item in value.values())
                if isinstance(value, list):
                    return any(references(item) for item in value)
                return False
            if removed and any(references(definition.get(field)) for field in ("edges", "program")):
                raise ValueError("Project graph references a departing Lineage leader; detach its wires before changing hierarchy")
            definition["nodes"] = [node for node in definition["nodes"] if node["id"] not in removed]
        project_resident_nodes(definition, members)
        definition["revision"] += 1
        definition["graph_updated_at"] = definition["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        lineage_data_definitions.write_definition(connection, lineage_id, definition)

    def synchronize(self, connection: Any, project_ids: Iterable[str]) -> None:
        """Re-project the principal graphs of the given Projects in this transaction.

        A Project's principal graph shows its own members and the Leaders of
        the Project's ordinary Lineages.  Writers pass the Projects whose
        members or Leaders they may have changed; every other Project graph
        is unaffected and is not read.
        """
        projects = {str(item) for item in project_ids if str(item or "")}
        if not projects:
            return
        pending = _HIERARCHY_SYNC_PENDING.get()
        if pending is not None:
            pending.update(projects)
            return
        seen: set[str] = set()
        for row in connection.execute(
            "SELECT lineage_id, project_id, leader_resident_id FROM lineages "
            f"WHERE {PROJECT_GRAPH_FILTER} AND project_id = ANY(%s) ORDER BY lineage_id",
            (sorted(projects),),
        ).fetchall():
            if row["project_id"] in seen:
                raise ValueError("A Project can have only one principal graph")
            seen.add(row["project_id"])
            leader_exists = connection.execute(
                'SELECT 1 FROM residents WHERE resident_id=%s AND deleted_at IS NULL', (row["leader_resident_id"],),
            ).fetchone()
            if not leader_exists:
                continue  # Registration commits the new leader immediately after its graph.
            self.materialize_members(connection, row["lineage_id"])

    @staticmethod
    def lineage_projects(connection: Any, lineage_ids: Iterable[str]) -> set[str]:
        requested = sorted({str(item) for item in lineage_ids if str(item or "")})
        if not requested:
            return set()
        return {
            str(row["project_id"])
            for row in connection.execute(
                'SELECT project_id FROM lineages WHERE lineage_id = ANY(%s)', (requested,),
            ).fetchall()
        }
