"""Optional execution usage and provenance on already-readable Chat messages.

Content, identity, permissions and reply placement belong to the required Chat
read. Failures here mark only the annotations unavailable; they cannot reject
that read or leave its caller's PostgreSQL transaction aborted.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from management_common.json_columns import json_value

from .scheduler_usage import normalize_token_usage


LOGGER = logging.getLogger(__name__)


def annotate_messages(connection: Any, messages: Sequence[dict[str, Any]]) -> None:
    by_execution: dict[str, list[dict[str, Any]]] = {}
    for message in messages:
        if message.get("execution_id"):
            by_execution.setdefault(str(message["execution_id"]), []).append(message)
    if not by_execution:
        return
    try:
        # Chat reads can join a snapshot or a business write transaction. A
        # nested transaction gives this optional SQL a savepoint: catching a
        # database error alone would poison all subsequent statements.
        with connection.raw.transaction():
            _annotate_executions(connection, by_execution)
    except Exception:
        LOGGER.exception("Chat execution annotations unavailable")
        for related in by_execution.values():
            for message in related:
                message["execution_annotations_status"] = "unavailable"


def _annotate_executions(connection: Any, by_execution: dict[str, list[dict[str, Any]]]) -> None:
    endings = connection.execute(
        "SELECT e.execution_id,e.usage_json,e.final_message_id AS message_id,e.provider,s.backend_route_id "
        "FROM executions e LEFT JOIN provider_sessions s USING(provider_session_ref) "
        "WHERE e.execution_id=ANY(%s) AND e.status IN ('completed','failed','cancelled')",
        (list(by_execution),),
    ).fetchall()
    for ending in endings:
        related = [message for message in by_execution[str(ending["execution_id"])]
                   if message.get("message_id") == ending["message_id"]]
        if not related:
            continue
        try:
            raw_usage = json_value(ending["usage_json"])
            source = {
                "execution_id": str(ending["execution_id"]), "provider": str(ending["provider"]),
                "account_id": str(ending["backend_route_id"] or ""),
                **({"model": raw_usage["model"], "model_evidence": "provider_reported"} if raw_usage.get("model") else {}),
            }
            usage = {
                **normalize_token_usage(raw_usage.get("accounted") or raw_usage),
                **{key: raw_usage[key] for key in ("api_id", "model")
                   if isinstance(raw_usage.get(key), str) and raw_usage[key]},
            }
        except Exception:
            LOGGER.exception("Chat execution annotations invalid for %s", ending["execution_id"])
            for message in related:
                message["execution_annotations_status"] = "unavailable"
            continue
        for message in related:
            try:
                combined_source = {**dict(message.get("generation_source") or {}), **source}
            except Exception:
                LOGGER.exception("Chat generation source invalid for %s", message["message_id"])
                message["execution_annotations_status"] = "unavailable"
                continue
            message.update(usage=usage, generation_source=combined_source)
