"""Segments: immutable captured content, its payloads and the relations between Segments."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from management_common.database_time import instant, iso_time
from management_common.json_columns import json_value

from .agent_data_store import canonical_json, in_batches, json_document, new_identifier, utc_now


SEGMENT_TYPES = frozenset({
    "input",
    "model",
    "tool",
    "compaction",
    "harness",
})
RELATION_TYPES = frozenset({
    "derived_from",
    "transformed_from",
    "compressed_from",
    "extracted_from",
    "merged_from",
})


def _content_bytes(content: object, content_type: str) -> bytes:
    if isinstance(content, bytes):
        return content
    if content_type == "application/json" or not isinstance(content, str):
        return canonical_json(content).encode("utf-8")
    return content.encode("utf-8")


def _decode_content(raw: bytes, content_type: str) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if content_type == "application/json":
        return json.loads(text)
    return text


class SegmentIdentityConflict(ValueError):
    def __init__(self, segment_id: str) -> None:
        self.segment_id = segment_id
        super().__init__(f"Segment identity refers to different immutable content: {segment_id}")


def _insert_payload(
    connection: Any,
    raw: bytes,
    content_type: str,
    created_at: str,
) -> tuple[str, str]:
    from .harness_retention import protect_artifact_references
    protect_artifact_references(connection, raw)
    sha256 = hashlib.sha256(raw).hexdigest()
    payload_ref = f"payload_{sha256}"
    connection.execute(
        '\n            INSERT INTO payloads(\n                payload_ref, sha256, byte_length, content_type, body, created_at\n            ) VALUES (%s, %s, %s, %s, %s, %s)\n            ON CONFLICT DO NOTHING\n            ',
        (payload_ref, sha256, len(raw), content_type, raw, created_at),
    )
    return payload_ref, sha256


def _segment_record(row: Any, content: Any) -> dict[str, Any]:
    return {
        "segment_id": str(row["segment_id"]),
        "segment_type": str(row["segment_type"]),
        "started_at": (iso_time(row["started_at"]) or ""),
        "finished_at": (iso_time(row["finished_at"]) or ""),
        "status": str(row["status"]),
        "created_by_actor_ref": str(row["created_by_actor_ref"]),
        "performed_by_actor_ref": str(row["performed_by_actor_ref"]),
        "content": content,
        "content_ref": str(row["content_ref"]),
        "content_sha256": str(row["content_sha256"]),
        "content_type": str(row["content_type"]),
        "metadata": json_value(row["metadata_json"] or "{}"),
        "created_by_agent_id": str(row["created_by_agent_id"]),
        "execution_id": str(row["execution_id"]),
    }


def insert_segment(
    connection: Any,
    *,
    segment_id: str,
    segment_type: str,
    content: object,
    started_at: str,
    finished_at: str,
    status: str,
    created_by_actor_ref: str,
    performed_by_actor_ref: str,
    content_type: str,
    metadata: Mapping[str, Any],
    created_by_agent_id: str,
    execution_id: str,
) -> str:
    if segment_type not in SEGMENT_TYPES:
        raise ValueError(f"invalid Segment type: {segment_type}")
    clean_id = str(segment_id or "").strip()
    if not clean_id:
        raise ValueError("Segment identity is required")
    at = str(finished_at or started_at or utc_now())
    clean_content_type = str(content_type or "text/plain; charset=utf-8")
    raw = _content_bytes(content, clean_content_type)
    payload_ref, sha256 = _insert_payload(
        connection, raw, clean_content_type, at
    )
    forbidden = {"hidden_from_chat", "show_in_chat", "send_to_ai", "native_id"}
    if forbidden.intersection(metadata):
        raise ValueError("Segment metadata cannot store consumer eligibility or transport aliases")
    metadata_json = json_document(dict(metadata))
    existing = connection.execute(
        'SELECT * FROM segments WHERE segment_id = %s', (clean_id,)
    ).fetchone()
    if existing is not None:
        content_identity = (
            str(existing["segment_type"]),
            str(existing["content_ref"]),
            str(existing["content_type"]),
            json_value(existing["metadata_json"]),
        )
        if content_identity != (
            segment_type,
            payload_ref,
            clean_content_type,
            json_value(metadata_json),
        ):
            raise SegmentIdentityConflict(clean_id)
        return clean_id
    connection.execute(
        '\n            INSERT INTO segments(\n                segment_id, segment_type, started_at, finished_at,\n                status, created_by_actor_ref, performed_by_actor_ref,\n                content_ref, content_sha256, content_type,\n                metadata_json, created_by_agent_id, execution_id\n            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)\n            ',
        (
            clean_id,
            segment_type,
            instant(str(started_at or "")),
            instant(str(finished_at or "")),
            str(status or "completed"),
            str(created_by_actor_ref or ""),
            str(performed_by_actor_ref or ""),
            payload_ref,
            sha256,
            clean_content_type,
            metadata_json,
            str(created_by_agent_id or ""),
            str(execution_id or ""),
        ),
    )
    return clean_id


def read_segment(
    connection: Any,
    segment_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        '\n            SELECT segment.*, payload.body AS payload_body\n            FROM segments AS segment\n            JOIN payloads AS payload ON payload.payload_ref = segment.content_ref\n            WHERE segment.segment_id = %s\n            ',
        (segment_id,),
    ).fetchone()
    if row is None:
        raise KeyError(segment_id)
    raw = bytes(row["payload_body"])
    return _segment_record(
        row, _decode_content(raw, str(row["content_type"]))
    )


def read_segments(
    connection: Any,
    segment_ids: Sequence[str],
) -> list[dict[str, Any]]:
    requested = list(dict.fromkeys(
        str(item or "").strip()
        for item in segment_ids
        if str(item or "").strip()
    ))
    if not requested:
        return []
    found: dict[str, dict[str, Any]] = {}
    for chunk in in_batches(requested):
        placeholders = ",".join("%s" for _ in chunk)
        for row in connection.execute(
            f"SELECT segment.*, payload.body AS payload_body "
            f"FROM segments AS segment JOIN payloads AS payload "
            f"ON payload.payload_ref = segment.content_ref "
            f"WHERE segment.segment_id IN ({placeholders})",
            chunk,
        ).fetchall():
            raw = bytes(row["payload_body"])
            found[str(row["segment_id"])] = _segment_record(
                row,
                _decode_content(raw, str(row["content_type"])),
            )
    missing = next((item for item in requested if item not in found), "")
    if missing:
        raise KeyError(missing)
    return [found[item] for item in requested]


def existing_segment_ids(
    connection: Any,
    segment_ids: Sequence[str],
) -> set[str]:
    found: set[str] = set()
    for chunk in in_batches(segment_ids):
        placeholders = ",".join("%s" for _ in chunk)
        found.update(
            str(row["segment_id"])
            for row in connection.execute(
                f"SELECT segment_id FROM segments "
                f"WHERE segment_id IN ({placeholders})",
                chunk,
            ).fetchall()
        )
    return found


def read_segment_members(connection: Any, ids: list[str]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for chunk in in_batches(ids):
        placeholders = ",".join("%s" for _ in chunk)
        for row in connection.execute(
            f"SELECT segment_id, segment_type, started_at, finished_at, metadata_json, execution_id "
            f"FROM segments WHERE segment_id IN ({placeholders})",
            chunk,
        ).fetchall():
            found[str(row["segment_id"])] = {
                "segment_id": str(row["segment_id"]),
                "segment_type": str(row["segment_type"]),
                "started_at": (iso_time(row["started_at"]) or ""),
                "finished_at": (iso_time(row["finished_at"]) or ""),
                "metadata": json_value(row["metadata_json"] or "{}"),
                "execution_id": str(row["execution_id"] or ""),
            }
    return [found[item] for item in ids if item in found]


class SegmentRecords:
    """Writes and reads of Segments, each in its own transaction."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def put_segment(
        self,
        *,
        segment_id: str = "",
        segment_type: str,
        content: object,
        started_at: str = "",
        finished_at: str = "",
        status: str = "completed",
        created_by_actor_ref: str = "",
        performed_by_actor_ref: str = "",
        content_type: str = "text/plain; charset=utf-8",
        metadata: Mapping[str, Any] | None = None,
        created_by_agent_id: str = "",
        execution_id: str = "",
    ) -> str:
        resolved_id = str(segment_id or new_identifier("segment"))
        return self.database.write(
            lambda connection: insert_segment(
                connection,
                segment_id=resolved_id,
                segment_type=segment_type,
                content=content,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                created_by_actor_ref=created_by_actor_ref,
                performed_by_actor_ref=performed_by_actor_ref,
                content_type=content_type,
                metadata=metadata or {},
                created_by_agent_id=created_by_agent_id,
                execution_id=execution_id,
            )
        )

    def has_segment(self, segment_id: str) -> bool:
        """Whether an identity names captured content, rather than an operation."""
        with self.database.read() as connection:
            return bool(connection.execute(
                "SELECT 1 FROM segments WHERE segment_id=%s", (segment_id,),
            ).fetchone())

    def segment(self, segment_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            return read_segment(connection, str(segment_id or ""))

    def add_relations(
        self,
        relations: Sequence[Mapping[str, Any]],
    ) -> list[str]:
        normalized: list[dict[str, Any]] = []
        for relation in relations:
            relation_type = str(relation.get("relation_type") or "")
            if relation_type not in RELATION_TYPES:
                raise ValueError(f"invalid Segment relation: {relation_type}")
            source = str(relation.get("source_segment_id") or "")
            target = str(relation.get("target_segment_id") or "")
            operation_id = str(relation.get("operation_id") or "")
            if not source or not target:
                raise ValueError("Segment relation endpoints are required")
            digest = hashlib.sha256(
                f"{relation_type}\0{source}\0{target}\0{operation_id}".encode("utf-8")
            ).hexdigest()[:32]
            normalized.append({
                "relation_id": f"relation_{digest}",
                "relation_type": relation_type,
                "source_segment_id": source,
                "target_segment_id": target,
                "operation_id": operation_id,
                "operation_type": str(relation.get("operation_type") or ""),
                "operation_version": str(relation.get("operation_version") or ""),
                "created_at": str(relation.get("created_at") or utc_now()),
                "actor_ref": str(relation.get("actor_ref") or ""),
                "metadata_json": json_document(dict(relation.get("metadata") or {})),
            })

        def write(connection: Any) -> list[str]:
            connection.executemany(
                '\n                INSERT INTO segment_relations(\n                    relation_id, relation_type, source_segment_id,\n                    target_segment_id, operation_id, operation_type,\n                    operation_version, created_at, actor_ref, metadata_json\n                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)\n                ON CONFLICT DO NOTHING\n                ',
                [tuple(item[key] for key in (
                    "relation_id", "relation_type", "source_segment_id",
                    "target_segment_id", "operation_id", "operation_type",
                    "operation_version", "created_at", "actor_ref", "metadata_json",
                )) for item in normalized],
            )
            return [str(item["relation_id"]) for item in normalized]

        return self.database.write(write) if normalized else []

    def segment_members(self, segment_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Segments in the given order, without their content bodies."""

        with self.database.read() as connection:
            return read_segment_members(connection, [str(item) for item in segment_ids])
