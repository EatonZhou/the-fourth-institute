"""Pass the complete, ordered delivery snapshot to a stateless Model API."""
from management_portal.execution.codex_history import response_items_from_segments

from .domain_ports import execution_port


def api_history(resident_id: str, message_id: str) -> list[dict]:
    snapshot = execution_port.delivery_context_snapshot(resident_id, message_id=message_id)
    return response_items_from_segments(snapshot["segments"])
