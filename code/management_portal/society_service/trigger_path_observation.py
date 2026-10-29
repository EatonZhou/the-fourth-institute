"""Paged graph observations join execution and effect owners without copying facts."""
from __future__ import annotations

from management_common.database_time import decode_times
from management_common.json_columns import json_value
from typing import Any
from . import trigger_paths


def _decode_step(row):
    step = decode_times(row, {"created_at", "started_at", "finished_at"})
    step["checkpoint"] = json_value(step.pop("checkpoint_json"))
    step["result"] = json_value(step.pop("result_json"))
    return step


def observe(lineage_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    identity = str(payload.get("trigger_path_id") or "")
    offset = int(payload.get("offset", 0))
    limit = int(payload.get("limit", 50))
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("Invalid Trigger Path page")
    if not identity:
        paths, total = trigger_paths.list_paths(lineage_id, limit=limit, offset=offset,
                                                resident_id=str(payload.get("resident_id") or ""))
        return {"paths": paths, "total": total, "next_offset": offset + len(paths) if offset + len(paths) < total else None}
    artifact_key = str(payload.get("artifact_activation_key") or "")
    # One read snapshot keeps the pages, owner states and watch cursor consistent.
    with trigger_paths.data_store().database.snapshot() as connection:
        row = connection.execute("SELECT * FROM trigger_paths WHERE trigger_path_id=%s AND lineage_id=%s",
                                 (identity, lineage_id)).fetchone()
        if row is None:
            raise KeyError(identity)
        path = decode_times(row, {"occurred_at", "finished_at"})
        path["input"] = json_value(path.pop("input_json"))
        agent = trigger_paths.agent_executions_table()
        linked = (f"SELECT DISTINCT e.execution_id,e.agent_id,e.status,e.created_at,e.started_at,e.finished_at,e.final_message_id "
                  f"FROM {agent} e JOIN trigger_path_execution_links l USING(execution_id) "
                  "JOIN trigger_path_steps s USING(step_id) WHERE s.trigger_path_id=%s")
        rank = connection.execute(
            "SELECT coalesce(sum(CASE status WHEN 'preparing' THEN 0 WHEN 'queued' THEN 1 "
            "WHEN 'running' THEN 2 ELSE 3 END),0) value FROM (" + linked + ") e", (identity,),
        ).fetchone()["value"]
        path["cursor"] = path["revision"] + rank
        result = {"path": path}
        queries = {
            "steps": ("SELECT * FROM trigger_path_steps WHERE trigger_path_id=%s ORDER BY created_at,step_id", _decode_step),
            "edges": ("SELECT source_step_id,target_step_id,wire_id FROM trigger_path_step_edges "
                      "WHERE trigger_path_id=%s ORDER BY source_step_id,target_step_id,wire_id", dict),
            "execution_links": ("SELECT l.* FROM trigger_path_execution_links l JOIN trigger_path_steps s USING(step_id) "
                                "WHERE s.trigger_path_id=%s ORDER BY l.step_id,l.execution_id", dict),
            "executions": (linked + " ORDER BY e.created_at,e.execution_id", lambda row: decode_times(row, {"created_at", "started_at", "finished_at"})),
        }
        for field, (query, decode) in queries.items():
            rows = connection.execute(query + " LIMIT %s OFFSET %s", (identity, limit + 1, offset)).fetchall()
            result[field] = {"items": [decode(row) for row in rows[:limit]],
                             "next_offset": offset + limit if len(rows) > limit else None}
        steps = result["steps"]["items"]
        keys = [s["activation_key"] for s in steps if s["kind"] == "effect" and s["status"] not in trigger_paths.TERMINAL]
        current = {r["activation_key"]: json_value(r["checkpoint_json"]) for r in connection.execute(
            "SELECT activation_key,checkpoint_json FROM effect_work WHERE lineage_id=%s AND activation_key=ANY(%s)",
            (lineage_id, keys),
        ).fetchall()} if keys else {}
        if artifact_key:
            artifact = connection.execute(
                "SELECT result_json FROM trigger_path_steps WHERE trigger_path_id=%s AND kind='effect' AND activation_key=%s",
                (identity, artifact_key),
            ).fetchone()
            if artifact is None:
                raise KeyError(artifact_key)
            outputs = json_value(artifact["result_json"]).get("outputs") or {}
    from .effect_disposition import can_resolve
    for step in steps:
        if step["activation_key"] in current:
            step["current"] = current[step["activation_key"]]
            step["resolution_required"] = can_resolve(lineage_id, step["activation_key"], step["current"])
    if artifact_key:
        from .effect_artifacts import ArtifactStore
        result["artifact_output"] = ArtifactStore().read(lineage_id, artifact_key, outputs["artifact"]) if "artifact" in outputs else outputs
    return result
