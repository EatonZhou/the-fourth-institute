"""References to surviving historical Family inputs after the strategy cutover."""
from __future__ import annotations

from .state_store import get_lineage_data_store


# The old table froze rendered intent before admission. Only proven input
# receipts cross the cutover; the source conversation still owns the body.
LEGACY_RECEIPTS = """
    SELECT scope.lineage_id, receipt.value->>'message_id' AS message_id,
           receipt.value->>'target_id' AS target_id, receipt.value->>'facade_id' AS facade_id,
           receipt.value->>'source_message_id' AS source_message_id, receipt.value->>'trigger' AS trigger
      FROM lineage_shared_state scope
      CROSS JOIN LATERAL jsonb_each(scope.cells_json::jsonb->'cells') cell
      CROSS JOIN LATERAL jsonb_each(CASE WHEN left(cell.key,7)='family.'
                                          AND right(cell.key,18)='.dispatch_receipts'
                                          AND jsonb_typeof(cell.value)='object'
                                      THEN cell.value ELSE '{}'::jsonb END) receipt
"""

PROGRAM_RECEIPTS = """
    SELECT DISTINCT run.lineage_id, leaf.input_message_id AS message_id, leaf.node_id AS target_id,
           facade.resident_id AS facade_id, run.input_json::jsonb#>>'{payload,message_id}' AS source_message_id,
           CASE holder.role WHEN 'router' THEN 'route' ELSE 'route_review' END AS trigger
      FROM trigger_paths run
      JOIN trigger_path_steps leaf USING(trigger_path_id)
      JOIN families f ON f.lineage_id=run.lineage_id AND f.deleted_at IS NULL
      JOIN family_residents facade ON facade.family_id=f.family_id AND facade.role='facade'
      JOIN family_residents holder ON holder.family_id=f.family_id
          AND holder.resident_id=leaf.node_id AND holder.role IN ('router','steward')
     WHERE run.root_kind='event' AND leaf.kind='resident'
       AND run.input_json::jsonb->>'type'='resident.message_received'
       AND run.input_json::jsonb#>>'{payload,resident_id}'=facade.resident_id
"""
RECEIPTS = LEGACY_RECEIPTS + " UNION " + PROGRAM_RECEIPTS


def route_source(lineage_id: str, recipient: str, message_id: str) -> tuple[str, str] | None:
    with get_lineage_data_store().database.read() as connection:
        row = connection.execute(
            f"""SELECT facade_id,source_message_id FROM ({LEGACY_RECEIPTS}) receipts
                WHERE lineage_id=%s AND target_id=%s AND message_id=%s AND trigger='route'""",
            (lineage_id, recipient, message_id),
        ).fetchone()
    return (row["facade_id"], row["source_message_id"]) if row else None


def task_targets(lineage_id: str, facade_id: str, message_ids: list[str]) -> list[dict]:
    if not message_ids:
        return []
    with get_lineage_data_store().database.read() as connection:
        rows = connection.execute(
            f"""SELECT DISTINCT message_id,target_id,source_message_id FROM ({RECEIPTS}) receipts
                WHERE lineage_id=%s AND facade_id=%s AND source_message_id=ANY(%s)""",
            (lineage_id, facade_id, message_ids),
        ).fetchall()
    return [{"message_id": row["message_id"], "target": row["target_id"],
             "facts": {"message_id": row["source_message_id"]}} for row in rows]
