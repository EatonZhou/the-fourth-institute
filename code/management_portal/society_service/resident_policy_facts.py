"""Trusted, content-free input facts the permission system asks for (whether an input is still open)."""

from . import resident_conversation_records


def is_resident_input_open(resident_id: str, message_id: str) -> bool:
    """Whether the Resident holds this input and it has not completed, failed or been cancelled."""
    from .resident_conversation_stream import get_repository

    try:
        state = get_repository().input_state(resident_id, message_id)
    except KeyError:
        return False
    return str(state.get("state") or "") not in resident_conversation_records.TERMINAL_INPUT_STATES


def is_resident_input_open_for_issuer(resident_id: str, message_id: str, issuer: str, *, connection=None) -> bool:
    """Verify the trusted issuer and recipient of a live prepared input."""
    from . import resident_prepared_inputs

    if connection is not None:
        return resident_prepared_inputs.open_for_issuer(connection, resident_id, message_id, issuer)
    from .state_store import get_lineage_data_store

    with get_lineage_data_store().database.read() as connection:
        return resident_prepared_inputs.open_for_issuer(connection, resident_id, message_id, issuer)
