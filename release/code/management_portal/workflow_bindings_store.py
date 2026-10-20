from __future__ import annotations

import hashlib
import threading

from management_common.portal_database import portal_database


STEP_COLUMNS = {"event": "event_source_id", "effect": "effect_id",
                "target": "target_wire_id", "invoke": "event_wire_id"}
_STORE = None
_STORES_LOCK = threading.Lock()


def get_workflow_binding_store():
    global _STORE
    with _STORES_LOCK:
        if _STORE is None:
            _STORE = WorkflowBindingStore()
        return _STORE


class WorkflowBindingConflict(RuntimeError):
    pass


class WorkflowBindingStore:
    def __init__(self):
        self.database = portal_database(schema="workbenches")
        with self.database.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workflow_bindings ("
                "lineage_id TEXT NOT NULL, resident_id TEXT NOT NULL, name TEXT NOT NULL, "
                "event_source_id TEXT NOT NULL, effect_id TEXT NOT NULL, "
                "target_wire_id TEXT NOT NULL, event_wire_id TEXT NOT NULL, "
                "version BIGINT NOT NULL, "
                # SHA-256 of the prompt the workflow last wrote into its Effect (see WorkflowBindings.install).
                "prompt_sha256 TEXT, "
                "PRIMARY KEY(lineage_id, resident_id, name))"
            )

    def lineage_lock(self, lineage_id: str):
        digest = hashlib.sha256(("workflow-lineage:" + lineage_id).encode()).digest()
        return self.database.advisory_lock(int.from_bytes(digest[:8], "big", signed=True))

    def get(self, lineage_id: str, resident_id: str, name: str) -> dict | None:
        with self.database.read_current() as connection:
            row = connection.execute(
                'SELECT * FROM workflow_bindings WHERE lineage_id=%s AND resident_id=%s AND name=%s',
                (lineage_id, resident_id, name),
            ).fetchone()
        if row is None:
            return None
        state = {key: row[key] for key in ("lineage_id", "resident_id", "name", "version")}
        state["steps"] = {label: row[column] for label, column in STEP_COLUMNS.items() if row[column]}
        if row["prompt_sha256"]:
            state["prompt_sha256"] = row["prompt_sha256"]
        return state

    def for_resident(self, lineage_id: str, resident_id: str) -> list[dict]:
        with self.database.read_current() as connection:
            rows = connection.execute(
                'SELECT name FROM workflow_bindings WHERE lineage_id=%s AND resident_id=%s',
                (lineage_id, resident_id),
            ).fetchall()
        return [state for row in rows if (state := self.get(lineage_id, resident_id, row['name']))]

    def save(self, state: dict) -> None:
        identity = tuple(state[key] for key in ("lineage_id", "resident_id", "name"))
        steps = state.get("steps", {})
        if any(not isinstance(value, str) or not value for value in identity):
            raise ValueError("Workflow binding identity is required")
        if set(steps) - STEP_COLUMNS.keys() or any(
            not isinstance(value, str) or not value or len(value) > 128 for value in steps.values()
        ):
            raise ValueError("Workflow steps must contain object IDs")
        prompt_sha256 = state.get("prompt_sha256") or None
        if prompt_sha256 is not None and (not isinstance(prompt_sha256, str) or len(prompt_sha256) != 64):
            raise ValueError("Workflow prompt digest must be a SHA-256 hex digest")
        version = state.get("version", 0)
        values = tuple(steps.get(label, "") for label in STEP_COLUMNS) + (prompt_sha256, version + 1)
        with self.database.transaction() as connection:
            if version == 0:
                cursor = connection.execute(
                    'INSERT INTO workflow_bindings (lineage_id, resident_id, name, event_source_id, effect_id, target_wire_id, event_wire_id, prompt_sha256, version) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(lineage_id, resident_id, name) DO NOTHING', identity + values,
                )
            else:
                cursor = connection.execute(
                    'UPDATE workflow_bindings SET event_source_id=%s, effect_id=%s, target_wire_id=%s, event_wire_id=%s, prompt_sha256=%s, version=%s WHERE lineage_id=%s AND resident_id=%s AND name=%s AND version=%s',
                    values + identity + (version,),
                )
            if cursor.rowcount != 1:
                raise WorkflowBindingConflict("Workflow binding changed; reload before continuing")
        state["version"] = version + 1

    def follow_moved_resident(self, lineage_id: str, resident_id: str, name: str, graph: dict) -> dict | None:
        """Rebind a complete workflow only after its original objects moved with its Resident."""
        with self.database.transaction() as connection:
            rows = connection.execute(
                'SELECT * FROM workflow_bindings WHERE resident_id=%s AND name=%s FOR UPDATE',
                (resident_id, name),
            ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise WorkflowBindingConflict("Resident has ambiguous workflow bindings")
            row = rows[0]
            program = graph.get("program") or {}
            components = {item["id"] for item in program.get("components", [])}
            wires = {item["id"]: item for item in program.get("wires", [])}
            target = wires.get(row["target_wire_id"], {})
            event = wires.get(row["event_wire_id"], {})
            if (graph.get("lineage_id") != lineage_id
                    or resident_id not in {node["id"] for node in graph.get("nodes", [])}
                    or not {row["event_source_id"], row["effect_id"]}.issubset(components)
                    or target.get("kind") != "target" or target.get("source") != row["effect_id"]
                    or target.get("target") != resident_id
                    or event.get("kind") != "route" or event.get("source") != row["event_source_id"]
                    or event.get("target") != row["effect_id"]):
                raise WorkflowBindingConflict("Move the complete workflow with its Resident before rebinding")
            connection.execute(
                'UPDATE workflow_bindings SET lineage_id=%s, version=version+1 '
                'WHERE lineage_id=%s AND resident_id=%s AND name=%s',
                (lineage_id, row["lineage_id"], resident_id, name),
            )
        return self.get(lineage_id, resident_id, name)

    def forget_residents(self, resident_ids: list[str]) -> int:
        """Drop deleted Residents' bindings; their graph objects went with them."""
        with self.database.transaction() as connection:
            return connection.execute(
                'DELETE FROM workflow_bindings WHERE resident_id = ANY(%s)', (list(resident_ids),),
            ).rowcount
