from management_portal.society_service import resident_backend
from management_portal.society_service.domain_ports import execution_port


def sequence_members(store, sequence_id):
    return store.segment_members(store.sequence_segment_ids(sequence_id))


def session_transcript_ids(store, session_ref, *, agent_id=""):
    with store.database.read() as connection:
        return store._session_transcript_ids(connection, session_ref, agent_id=agent_id)


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
