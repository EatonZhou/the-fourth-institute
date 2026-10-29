import importlib

from management_portal.execution import agent_data_contexts
from management_portal.society_service import resident_backend
from management_portal.society_service.domain_ports import execution_port


# The record families of AgentDataStore that stamp their rows with the clock.
STORE_CLOCK_MODULES = (
    "agent_data_chat_writes", "agent_data_contexts", "agent_data_executions", "agent_data_forks",
    "agent_data_segments", "agent_data_sessions", "agent_data_trace_edits", "agent_data_traces",
)


def patch_store_clock(monkeypatch, clock):
    """Make AgentDataStore read the current time from ``clock``."""
    for name in STORE_CLOCK_MODULES:
        monkeypatch.setattr(importlib.import_module(f"management_portal.execution.{name}"), "utc_now", clock)


def sequence_members(store, sequence_id):
    return store.segment_members(store.sequence_segment_ids(sequence_id))


def session_transcript_ids(store, session_ref, *, agent_id=""):
    with store.database.read() as connection:
        return agent_data_contexts.session_transcript_ids(connection, session_ref, agent_id=agent_id)


def session_route_id(record):
    return str(resident_backend.session_record(record).get("backend_route_id") or "")


def register_session(record, backend_session_id, *, backend_route_id=""):
    clean_session = resident_backend.session_id(backend_session_id)
    if not clean_session:
        raise ValueError("Agent session ID is required")
    execution_port.sessions().bind_session(
        resident_backend.execution_binding_id(record),
        provider=resident_backend.execution_provider(record),
        backend_route_id=str(backend_route_id or "").strip(),
        backend_session_id=clean_session,
    )
