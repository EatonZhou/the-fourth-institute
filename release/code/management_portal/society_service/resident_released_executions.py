"""Durable cleanup for an Execution whose Resident no longer holds the active slot."""
from __future__ import annotations

import logging
from .domain_ports import execution_port
from .state_store import get_lineage_data_store

_log = logging.getLogger(__name__)
_TERMINAL = {"completed","failed","cancelled"}


def reconcile(*, limit: int = 128) -> int:
    store = get_lineage_data_store()
    with store.database.read() as connection:
        rows = connection.execute(
            "SELECT * FROM resident_execution_release_work release "
            "WHERE NOT EXISTS (SELECT 1 FROM resident_execution_work active "
            "WHERE active.execution_id=release.execution_id) ORDER BY execution_id LIMIT %s",
            (limit,),
        ).fetchall()
    if not rows:
        return 0
    service = execution_port.service()
    settled = 0
    for row in rows:
        execution_id, actor = row["execution_id"], row["actor"]
        try:
            try:
                snapshot = service.status(execution_id, actor=actor,include_events=False,include_result=False)
            except KeyError:
                snapshot = {"status":"absent"}
            status = snapshot.get("status")
            if status not in _TERMINAL | {"absent"}:
                if snapshot.get("scheduler",{}).get("agent_id") not in {None,row["resident_id"]}:
                    raise ValueError("Released Execution belongs to a different Resident")
                if not snapshot.get("scheduler",{}).get("cancel_requested"):
                    service.cancel(execution_id,actor=actor,reason="Resident released this Execution")
                continue
            if status in _TERMINAL:
                service.acknowledge(execution_id,actor=actor)
            store.database.write(lambda connection: connection.execute(
                "DELETE FROM resident_execution_release_work WHERE execution_id=%s", (execution_id,),
            ))
            settled += 1
        except Exception:
            _log.exception("Could not finish released Resident Execution %s",execution_id)
    return settled
