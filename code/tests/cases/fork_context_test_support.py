"""Canonical PostgreSQL history fixtures for Fork contracts (not Chat mocks)."""
from tests.cases.agent_data_test_support import register_session, session_route_id
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import resident_backend


def record_session_turn(record, *, question='source question', answer='source answer'):
    """One completed turn of the Resident's Provider session, in its trace."""

    store = get_agent_data_store()
    resident_id = record['resident_id']
    session_id = resident_backend.record_session_id(record)
    if not session_id:
        session_id = f'01a00000-0000-7000-8000-{resident_id[-12:]}'
        register_session(record, session_id, backend_route_id='zero')
    session = store.ensure_provider_session(
        provider='codex', backend_route_id=session_route_id(record),
        backend_session_id=session_id, agent_id=resident_id, actor_ref='CASE-A',
    )
    execution_id = f'execution-fork-fixture-{resident_id}'
    store.begin_execution(execution_id=execution_id, agent_id=resident_id,
                          actor_ref='CASE-A', provider='codex', started_at='2026-09-08T00:00:00Z')
    store.bind_execution_provider_session(execution_id, provider_session_ref=session['provider_session_ref'])
    ids = [store.put_segment(
        segment_type=kind, content=content, execution_id=execution_id,
        metadata={'role': role}, created_by_agent_id=resident_id,
    ) for kind, content, role in [('input', question, 'user'), ('model', answer, 'assistant')]]
    store.append_agent_segments(resident_id, ids, actor_ref='CASE-A')
    store.finish_execution(execution_id, status='completed', provider_turn_id='01a00000-0000-7000-8000-000000000111')
    return ids
