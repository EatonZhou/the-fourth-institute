"""Each Resident's current execution: its Job, queue and release work."""
from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from management_common.database_time import iso_time
from management_common.json_columns import json_text, json_value
from . import lineage_data_mailbox, resident_delivery_storage, resident_execution_storage
from .lineage_data_columns import now_iso_millis


def _with_queues(connection: Any, states: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    queues = lineage_data_mailbox.mailbox_queues(connection, list(states)) if states else {}
    for resident_id, state in states.items():
        if state or resident_id in queues:
            state["queue"] = queues.get(resident_id, [])
    return states


class ResidentExecutions:
    """Reads and writes of Resident execution records."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def resident_execution_records(self, resident_ids):
        requested = list(dict.fromkeys(resident_ids))
        with self.database.read_transaction() as connection:
            values = resident_execution_storage.read(connection, requested) if requested else {}
            return _with_queues(connection, {rid: values.get(rid,(0,{}))[1] for rid in requested})

    def resident_execution_record_with_revision(self, resident_id):
        with self.database.read_transaction() as connection:
            revision,state = resident_execution_storage.read(connection,[resident_id]).get(resident_id,(0,{}))
            _with_queues(connection,{resident_id:state})
        return {'revision':revision,'state':state}

    def resident_execution_control(self, resident_id):
        from .runtime_read_models import execution_control_state
        with self.database.read_transaction() as connection:
            revision,state = resident_execution_storage.read(connection,[resident_id],content=False).get(resident_id,(0,{}))
            _with_queues(connection,{resident_id:state})
        return {'revision':revision,'state':execution_control_state(state)}

    def resident_execution_revision(self, resident_id):
        with self.database.read_current() as connection:
            row = connection.execute('SELECT revision FROM resident_execution_work WHERE resident_id=%s',(resident_id,)).fetchone()
        return int(row['revision']) if row else 0

    def request_resident_execution_stop(self, resident_id, execution_id, message_id, actor, now):
        """Commit exact-input control without loading prompt, Workspace or Conversation.

        The parent Resident row is the same serialization boundary as normal Job
        writes. Their merge rule preserves this intent on stale progress writes.
        """
        def write(connection):
            resident = connection.execute(
                'SELECT resident_id FROM residents WHERE resident_id=%s AND deleted_at IS NULL FOR UPDATE',
                (resident_id,),
            ).fetchone()
            if resident is None:
                raise KeyError("resident not found")
            row = connection.execute(
                'SELECT execution_id,input_message_id,progress_json FROM resident_execution_work WHERE resident_id=%s FOR UPDATE',
                (resident_id,),
            ).fetchone()
            if row is None or row['execution_id'] != execution_id or row['input_message_id'] != message_id:
                raise ValueError("message is not the active Resident input")
            progress = json_value(row['progress_json'])
            if progress.get('outcome') in resident_execution_storage.TERMINAL or progress.get('finished_at'):
                return False
            progress.setdefault('stop_requested_at', now)
            progress.setdefault('stop_requested_by', actor)
            progress['updated_at'] = now
            revision = int(connection.execute("SELECT nextval('resident_execution_revision')").fetchone()[0])
            connection.execute(
                'UPDATE resident_execution_work SET progress_json=%s,revision=%s WHERE resident_id=%s',
                (resident_execution_storage.encode(progress),revision,resident_id),
            )
            resident_delivery_storage.enqueue_state(connection, resident_id, revision)
            return True
        return self.database.write(write)

    def advance_resident_execution_cursor(self, resident_id, execution_id, cursor):
        self.database.write(lambda connection: connection.execute(
            "UPDATE resident_execution_work SET event_cursor=%s,revision=nextval('resident_execution_revision') "
            "WHERE resident_id=%s AND execution_id=%s AND event_cursor < %s",
            (cursor,resident_id,execution_id,cursor),
        ))

    def put_resident_execution_record(self, resident_id, value, *, return_state=False, delivery_message_id=""):
        payload = deepcopy(dict(value))
        queue = payload.pop('queue', [])
        def write(connection):
            resident = connection.execute('SELECT r.resident_id,r.backend,c.backend_route_id FROM residents r LEFT JOIN resident_execution_configuration c USING(resident_id) WHERE r.resident_id=%s AND r.deleted_at IS NULL FOR UPDATE OF r', (resident_id,)).fetchone()
            if resident is None:
                raise KeyError(resident_id)
            if payload.get("execution_id"):
                from management_portal.execution import execution_lifecycle
                from . import resident_backend, resident_execution_links
                execution_lifecycle.reserve(connection, execution_id=payload["execution_id"], agent_id=resident_id,
                    actor=str(payload.get("actor") or ""), provider=resident_backend.execution_provider(dict(resident)),
                    created_at=str(payload.get("enqueued_at") or payload.get("started_at") or now_iso_millis()))
                resident_execution_links.link_inputs(resident_id, payload, connection=connection)
            revision,previous = resident_execution_storage.read(connection,[resident_id]).get(resident_id,(0,{}))
            same_execution = previous.get('execution_id') and previous.get('execution_id') == payload.get('execution_id')
            same_input = previous.get('user_message_id') and previous.get('user_message_id') == payload.get('user_message_id')
            if (same_execution or same_input) and previous.get('intake_admitted_at'):
                # Progress-only writes cannot undo admission of the same input.
                payload['intake_admitted_at'] = previous['intake_admitted_at']
            if (same_execution or same_input) and previous.get('stop_requested_at'):
                payload['stop_requested_at'] = previous['stop_requested_at']
                if previous.get('stop_requested_by'):
                    payload['stop_requested_by'] = previous['stop_requested_by']
            if same_execution:
                if previous.get('execution_acknowledged'):
                    payload['execution_acknowledged'] = True
                payload['execution_event_cursor'] = max(int(previous.get('execution_event_cursor') or 0), int(payload.get('execution_event_cursor') or 0))
            if previous.get('execution_id') and not same_execution:
                self._release_execution(resident_id, previous)
            revision = int(connection.execute("SELECT nextval('resident_execution_revision')").fetchone()[0])
            if resident_execution_storage.pending(payload):
                resident_execution_storage.put(connection,resident_id,revision,payload)
            else:
                if payload.get('execution_id'):
                    self._release_execution(resident_id, payload)
                connection.execute('DELETE FROM resident_execution_work WHERE resident_id=%s',(resident_id,))
            lineage_data_mailbox.sync_mailbox_queue(connection,resident_id,queue)
            if delivery_message_id:
                resident_delivery_storage.enqueue(connection, resident_id, delivery_message_id, revision)
            return revision,{**deepcopy(payload),'queue':deepcopy(queue)}
        revision,committed = self.database.write(write)
        return (revision,committed) if return_state else revision

    def _release_execution(self, resident_id, value):
        identity = str(value.get('execution_id') or '')
        if not identity:
            return
        def write(connection):
            if value.get("outcome") == "failed" and value.get("error") and value.get("user_message_id"):
                error = dict(value.get("runtime_error") or {"code": "execution_failed", "message": str(value["error"])})
                connection.execute(
                    "UPDATE conversation_input_states SET error_json=%s WHERE resident_id=%s AND message_id=%s "
                    "AND state IN ('queued','running')",
                    (json_text(error, ensure_ascii=False), resident_id, value["user_message_id"]),
                )
            if value.get('submitted_at'):
                if not value.get('execution_acknowledged'):
                    connection.execute(
                        "INSERT INTO resident_execution_release_work VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                        (identity,resident_id,str(value['actor'])),
                    )
            else:
                from management_common.portal_database import _database_schema
                table = '"' + _database_schema('agent') + '".executions'
                status = value.get("outcome") if value.get("outcome") in resident_execution_storage.TERMINAL else "cancelled"
                connection.execute(f"UPDATE {table} SET status=%s,finished_at=%s "
                                   "WHERE execution_id=%s AND status IN ('preparing','queued')",
                                   (status,str(value.get("finished_at") or now_iso_millis()),identity))
        self.database.write(write)

    def terminal_input_receipts(self, inputs: Iterable[tuple[str,str]]) -> dict[tuple[str,str],dict[str,Any]]:
        pairs = list(dict.fromkeys(inputs))
        if not pairs:
            return {}
        from management_common.portal_database import _database_schema
        agent = '"' + _database_schema('agent') + '"'
        with self.database.read() as connection:
            rows = connection.execute(
                "WITH requested AS (SELECT * FROM unnest(%s::text[],%s::text[]) AS p(resident_id,message_id)) "
                "SELECT input.resident_id,input.message_id,e.execution_id,e.status,e.finished_at,e.actor_ref "
                "FROM requested JOIN conversation_input_states input USING(resident_id,message_id) "
                f"JOIN LATERAL (SELECT e.* FROM {agent}.execution_inputs i "
                f"JOIN {agent}.executions e USING(execution_id) "
                "WHERE i.message_id=input.message_id AND e.agent_id=input.resident_id "
                "ORDER BY e.created_at DESC,e.execution_id DESC LIMIT 1) e ON TRUE "
                "WHERE input.state IN ('queued','running') AND e.status IN ('completed','failed','cancelled') "
                "AND e.finished_at>=input.updated_at "
                "AND NOT EXISTS (SELECT 1 FROM resident_execution_work active WHERE active.resident_id=input.resident_id "
                "AND active.input_message_id=input.message_id) "
                "AND NOT EXISTS (SELECT 1 FROM resident_mailbox mailbox WHERE mailbox.resident_id=input.resident_id "
                "AND mailbox.message_id=input.message_id)",
                ([rid for rid,_ in pairs],[mid for _,mid in pairs]),
            ).fetchall()
        return {(row['resident_id'],row['message_id']): {
            'execution_id':row['execution_id'],'outcome':row['status'],
            'finished_at':iso_time(row['finished_at']),'actor':row['actor_ref'],
        } for row in rows}

    def acknowledge_resident_execution(self,resident_id,execution_id):
        def write(connection):
            connection.execute('SELECT resident_id FROM residents WHERE resident_id=%s FOR UPDATE', (resident_id,))
            revision,value = resident_execution_storage.read(connection,[resident_id]).get(resident_id,(0,{}))
            if value.get('execution_id') != execution_id:
                return
            value['execution_acknowledged'] = True
            if resident_execution_storage.pending(value):
                revision = int(connection.execute("SELECT nextval('resident_execution_revision')").fetchone()[0])
                resident_execution_storage.put(connection,resident_id,revision,value)
            else:
                self._release_execution(resident_id,value)
                connection.execute('DELETE FROM resident_execution_work WHERE resident_id=%s',(resident_id,))
        self.database.write(write)

    def delete_resident_execution_record(self, resident_id):
        def write(connection):
            connection.execute('SELECT resident_id FROM residents WHERE resident_id=%s FOR UPDATE', (resident_id,))
            _revision,value = resident_execution_storage.read(connection,[resident_id]).get(resident_id,(0,{}))
            if value.get('execution_id'):
                self._release_execution(resident_id,value)
            connection.execute('DELETE FROM resident_execution_work WHERE resident_id=%s',(resident_id,))
            lineage_data_mailbox.sync_mailbox_queue(connection,resident_id,[])
        self.database.write(write)
