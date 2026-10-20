from __future__ import annotations

from management_common.resource_turn_artifacts import content_part, segment_id
from management_portal.resources import get_resource_service


def display_outputs(repository, resident_id: str, execution_id: str) -> list[dict]:
    try:
        source = repository.agent_data.operation_record(segment_id(execution_id, resident_id))
    except KeyError:
        changes = get_resource_service().execution_content_changes(execution_id, actor=resident_id)
        if not changes:
            return []
        part = content_part(changes, execution_id=execution_id, actor=resident_id)
        created_at = changes[-1]["created_at"]
        repository.agent_data.put_operation_record(
            segment_id=part["segment_id"], segment_type="artifact", content=part,
            content_type="application/json", created_at=created_at,
            created_by_agent_id=resident_id, created_by_actor_ref=resident_id,
            performed_by_actor_ref=resident_id, execution_id=execution_id,
        )
    else:
        part, created_at = source["content"], source["created_at"]
    return [{"output_id": "msg_" + part["segment_id"].removeprefix("segment_"),
             "created_at": created_at, "phase": "resource_changes", "content_parts": [part]}]
