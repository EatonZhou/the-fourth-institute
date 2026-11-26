"""Read the Society-owned inputs needed to build an authorization snapshot."""

from management_common.portal_database import portal_database


def list_permission_resident_workspaces() -> dict[str, str]:
    """Map each live Resident to its own Workspace without initializing Society state."""
    with portal_database(schema="lineage").readonly_snapshot() as connection:
        rows = connection.execute(
            "SELECT resident_id, owned_workspace_id FROM residents "
            "WHERE deleted_at IS NULL AND owned_workspace_id IS NOT NULL ORDER BY resident_id",
        ).fetchall()
    return {row["resident_id"]: row["owned_workspace_id"] for row in rows}
