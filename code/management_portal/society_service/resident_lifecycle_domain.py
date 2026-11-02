"""Resident domain responsibilities: lifecycle."""
from __future__ import annotations

from . import resident_backend, resident_catalog, resident_conversation_stream, resident_record_domain, resident_execution_work, directory_execution_admission
from typing import Any, Mapping
import copy
from contextlib import contextmanager


def resident_execution_paths(record, work=None):
    """Current directory uses, from Society/Workspace projections and input state."""
    paths = [record.get('workspace_path'), record.get('default_cwd'),
             (record.get('owned_workspace') or {}).get('path')]
    paths.extend(binding.get('path') for binding in record.get('workspace_bindings') or [])
    for item in [work or {}, *((work or {}).get('queue') or [])]:
        paths.extend(item.get(key) for key in ('prompt_cwd', 'working_directory'))
    return tuple(dict.fromkeys(str(path) for path in paths if path))


def directory_execution_users(path, *other_paths, lineage_ids=()):
    """Discover Portal executors by location, never by the directory's membership.

    This is not a host-process scanner: unknown external processes are outside
    Portal's execution contract and must be stopped by their host owner.
    """
    from management_common.directory_paths import contains
    from .script_runs import store as script_store
    paths = (path, *other_paths)
    catalog = resident_catalog.get_catalog()
    residents = catalog.all()
    work_by_id = catalog.state_store.resident_execution_records([record['resident_id'] for record in residents])
    records = []
    for record in residents:
        work = work_by_id.get(record['resident_id'], {})
        # Parent bindings (notably the inherited Project root) are available
        # scope, not evidence of working inside every descendant directory.
        if any(contains(root, candidate) for root in paths
               for candidate in resident_execution_paths(record, work)):
            records.append(record)
    runs, after = [], ''
    while True:
        page = script_store.pending(after, limit=200)
        for run in page:
            if run['status'] == 'running' and (run.get('lineage_id') in lineage_ids or any(
                contains(root, run[key]) for root in paths for key in ('cwd', 'path') if run.get(key)
            )):
                runs.append(run)
        if len(page) < 200:
            break
        after = page[-1]['script_run_id']
    return records, runs


def require_idle_directory(path, *other_paths):
    """Require execution-owner terminal proof, including work awaiting Society ack."""
    from .state_store import get_lineage_data_store
    from . import execution_client
    records, runs = directory_execution_users(path, *other_paths)
    if runs:
        raise RuntimeError('Directory has active execution users; quiesce through their owner first')
    store = get_lineage_data_store()
    for record in records:
        work = store.resident_execution_record(record['resident_id'])
        if not resident_execution_work.is_active(work):
            continue
        execution_id = resident_execution_work.submitted_execution_id(work)
        # A submitted Execution may already be terminal while Society has yet
        # to consume its result. Observe its owner; never clear/ack its work here.
        terminal = execution_client.service().wait(execution_id,
            actor=resident_execution_work.request_actor(work), timeout_seconds=0) if execution_id else {}
        if terminal.get('status') not in resident_execution_work.TERMINAL_OUTCOMES:
            raise RuntimeError('Directory has active execution users; quiesce through their owner first')


@contextmanager
def directory_move_execution_scope(records=(), *, resident_ids=(), lineage_ids=(), actor=''):
    """Stop directory users behind the move's durable admission reservations.

    Intake policy is independent of a move. Do not temporarily change it:
    in-memory restoration loses the original value on crash or overlapping
    moves. Reservations are the sole temporary gate and survive both cases.
    """
    from .state_store import get_lineage_data_store
    from .script_runs import service as script_service
    from . import execution_client
    records, resident_ids = list(records), tuple(resident_ids)
    active = True
    def terminate(path):
        if not active:
            raise RuntimeError('Directory move execution scope has already closed')
        with directory_execution_admission.authority():
            selected, runs = directory_execution_users(path, lineage_ids=lineage_ids)
            # Same-filesystem moves never invoke this termination callback.
            # Resolve explicit identities only here, and only those the current
            # directory-user discovery has not already loaded.
            known = {record['resident_id'] for record in [*records, *selected]}
            extra = resident_catalog.get_catalog().get_many(rid for rid in resident_ids if rid not in known)
            selected = list({record['resident_id']: record for record in [*records, *extra, *selected]}.values())
            for record in selected:
                work = get_lineage_data_store().resident_execution_record(record['resident_id'])
                if (resident_execution_work.is_active(work)
                        and not resident_execution_work.submitted_execution_id(work)
                        and not resident_execution_work.is_execution_held(work)):
                    raise RuntimeError('Resident execution preparation is not yet stopped: ' + record['resident_id'])
                _cancel_resident_execution(record, performed_by=actor, service=execution_client.service())
            for run in runs:
                result = script_service.cancel(run['script_run_id'], run['caller_id'], lineage_id=run['lineage_id'])
                if result.get('status') not in script_service.TERMINAL:
                    raise RuntimeError('Script termination is not yet confirmed: ' + run['script_run_id'])
    try:
        yield terminate
    finally:
        active = False


def terminate_resident_work(record, *, performed_by='', service=None):
    """Cancel the exact submitted execution and confirm termination before deletion."""
    from .state_store import get_lineage_data_store
    store = get_lineage_data_store()
    rid = record['resident_id']
    store.database.write(lambda c: c.execute(
        'UPDATE residents SET intake_open=FALSE WHERE resident_id=%s AND deleted_at IS NULL', (rid,),
    ))
    _cancel_resident_execution(record, performed_by=performed_by, service=service)


def _cancel_resident_execution(record, *, performed_by='', service=None):
    """Execution cancellation is shared by deletion and directory movement."""
    from .state_store import get_lineage_data_store
    from .domain_ports import execution_port
    work = get_lineage_data_store().resident_execution_record(record['resident_id'])
    if not resident_execution_work.is_active(work):
        return
    execution_id = resident_execution_work.submitted_execution_id(work)
    if not execution_id:
        return
    actor = resident_execution_work.request_actor(work)
    service = service or execution_port.service()
    service.cancel(execution_id, actor=actor, **({'performed_by': performed_by} if performed_by else {}))
    terminal = service.wait(execution_id, actor=actor, timeout_seconds=30)
    if terminal.get('status') not in {'cancelled', 'failed', 'completed'}:
        raise RuntimeError('Execution termination is not yet confirmed: ' + execution_id)


def terminate_lineage_scripts(lineage_id, actor):
    from .script_runs import service as script_service, store as script_store
    while True:
        running = script_store.list_runs({'lineage_id': lineage_id, 'status': 'running', 'limit': 200})['runs']
        if not running:
            return
        for run in running:
            result = script_service.cancel(run['script_run_id'], actor, lineage_id=lineage_id)
            if result.get('status') not in script_service.TERMINAL:
                raise RuntimeError('Script termination is not yet confirmed: ' + run['script_run_id'])



def resident_aggregate_lineage_id(resident_id: object) -> str:
    """Resolve graph membership for operations that also change topology."""

    clean_id = str(resident_id or "").strip()
    if not clean_id:
        raise ValueError("resident_id is required for Resident mutation locking")
    try:
        return resident_catalog.get_catalog().target(clean_id).lineage_id
    except KeyError as exc:
        raise KeyError(
            f"Resident mutation locking requires a registered Resident: {clean_id}"
        ) from exc


def resident_has_pending_work(resident_id: object) -> bool:
    if resident_record_domain.lifecycle_operations().pending(str(resident_id or "")):
        return True
    state = resident_conversation_stream.get_repository().current_state(str(resident_id or ""))
    return str(state.get("work_state") or "idle") in {
        "queued",
        "running",
        "stopping",
    }


def resident_turn_running(resident_id: str) -> bool:
    state = resident_conversation_stream.get_repository().current_state(resident_id)
    return str(state.get("work_state") or "idle") in {"running", "stopping"}


def ready_resident_lifecycle_operations() -> list[str]:
    return resident_record_domain.lifecycle_operations().ready()


def resident_lifecycle_health() -> dict[str, Any]:
    return resident_record_domain.lifecycle_operations().health()


def observe_committed_resident_transfer(operation_id: str, resident_id: str) -> dict[str, Any]:
    """Internal Lineage port: caller has verified receipt and both read grants."""
    return resident_record_domain.lifecycle_operations().observe_committed_transfer(operation_id, resident_id)


def resident_release_step(record: dict[str, Any], actor: str) -> dict[str, Any]:
    return {
        "kind": "release", "record": {
            key: copy.deepcopy(record[key]) for key in (
                "resident_id", "lineage_id", "backend", "backend_route_id",
                "workspace_path", "owned_workspace_id", "owned_workspace", "default_cwd",
            ) if key in record
        }, "actor": actor,
        "binding": resident_backend.session_record(record),
    }


def append_conversation_message(
    resident_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Persist one complete message through the conversation stream authority."""
    from management_common.principal import message_origin

    message = copy.deepcopy(message)
    if message.get("role") == "agent":
        from management_common.async_questions import decode_question_content

        message["content_parts"] = decode_question_content(
            message.get("content_parts") or [], resident_id, message["message_id"],
        )
    if "origin" not in message and message.get("role") in {"agent", "system"}:
        sender = resident_id if message["role"] == "agent" else "portal"
        message["origin"] = message_origin(sender)
        message["author_id"] = sender
    return resident_conversation_stream.get_repository().append_message(
        resident_id,
        message,
    )


def mark_resident_input_queued(
    resident_id: str,
    message: Mapping[str, Any],
) -> None:
    resident_conversation_stream.get_repository().set_input_state(
        resident_id,
        str(message.get("message_id") or ""),
        "queued",
    )


def resident_user_message_count(resident_id: object) -> int:
    key = str(resident_id or "").strip()
    if not key:
        return 0
    return resident_conversation_stream.get_repository().user_message_counts([key]).get(key, 0)


def persist_resident_user_message(
    resident_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    return resident_conversation_stream.get_repository().commit_user_message(
        resident_id,
        message,
    )


def accept_resident_user_message(
    resident_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    return resident_conversation_stream.get_repository().accept_user_message(
        resident_id,
        message,
    )
