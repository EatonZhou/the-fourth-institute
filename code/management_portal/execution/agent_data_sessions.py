"""Provider sessions and the bindings that name the session an Agent continues."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from management_common.database_time import instant, iso_time

from .agent_data_contexts import mark_context_received, read_session_context_ids
from .agent_data_store import (
    FIELD_NOT_PROVIDED,
    clean_string_tuple,
    in_batches,
    json_document,
    utc_now,
)


def _binding_record(row: Any) -> dict[str, Any]:
    return {
        "binding_id": str(row["binding_id"]),
        "provider_session_ref": str(row["provider_session_ref"]),
        "provider": str(row["provider"]),
        "backend_session_id": str(row["backend_session_id"]),
        "backend_route_id": str(row["backend_route_id"]),
        "agent_id": str(row["agent_id"] or ""),
        "context_fingerprint": str(row["context_fingerprint"]),
        "context_injected_at": (iso_time(row["context_injected_at"]) or ""),
        "context_injection_count": int(row["context_injection_count"] or 0),
        "harness_tool_names": clean_string_tuple(row["harness_tool_names_json"]),
        "created_at": (iso_time(row["created_at"]) or ""),
        "updated_at": (iso_time(row["updated_at"]) or ""),
    }


def _binding_row(
    connection: Any,
    binding_id: str,
) -> Any | None:
    return connection.execute(
        "\n            SELECT binding.*, session.provider, session.backend_route_id,\n                   session.backend_session_id,\n                   COALESCE((\n                       SELECT execution.agent_id\n                       FROM executions AS execution\n                       WHERE execution.provider_session_ref =\n                             session.provider_session_ref\n                       ORDER BY execution.started_at, execution.execution_id\n                       LIMIT 1\n                   ), '') AS agent_id\n            FROM provider_bindings AS binding\n            JOIN provider_sessions AS session\n              ON session.provider_session_ref = binding.provider_session_ref\n            WHERE binding.binding_id = %s\n            ",
        (binding_id,),
    ).fetchone()


class ProviderSessions:
    """Records of Provider sessions and of their bindings."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def ensure_provider_session(
        self,
        *,
        provider: str,
        backend_route_id: str,
        backend_session_id: str,
        agent_id: str,
        actor_ref: str,
        source_backend_session_id: str = "",
        source_backend_route_id: str = "",
        context_agent_id: str = "",
    ) -> dict[str, Any]:
        """Record a Provider session, the first time it is seen.

        A native fork (``source_backend_session_id``) starts with its source's
        current context; the trace of ``context_agent_id`` (the Agent that will
        work in it; by default ``agent_id``) marks that inherited context, so
        that the session's context read from the trace begins with it.  The
        context_received operation marks the first inherited trace member;
        the content must already belong to that Agent's trace.
        """

        clean_provider = str(provider or "").strip()
        clean_route = str(backend_route_id or "").strip()
        clean_session = str(backend_session_id or "").strip()
        if not clean_provider or not clean_session:
            raise ValueError("Provider Session identity is required")
        digest = hashlib.sha256(
            f"{clean_provider}\0{clean_route}\0{clean_session}".encode("utf-8")
        ).hexdigest()[:32]
        session_ref = f"provider_session_{digest}"

        def write(connection: Any) -> dict[str, Any]:
            existing = connection.execute(
                'SELECT 1 FROM provider_sessions WHERE provider_session_ref = %s',
                (session_ref,),
            ).fetchone()
            at = utc_now()
            if existing is None:
                clean_source = str(source_backend_session_id or "").strip()
                inherited: list[str] = []
                if clean_source:
                    source = connection.execute(
                        'SELECT provider_session_ref FROM provider_sessions '
                        'WHERE provider = %s AND backend_route_id = %s AND backend_session_id = %s',
                        (clean_provider, str(source_backend_route_id or clean_route), clean_source),
                    ).fetchone()
                    if source is None:
                        raise KeyError(
                            f"Provider Session source is not recorded: {clean_source}"
                        )
                    inherited = read_session_context_ids(
                        connection, str(source["provider_session_ref"]),
                        agent_id=str(context_agent_id or agent_id or ""),
                    )
                # The event projection and the runtime can both see a new
                # session first; the later one keeps the committed record.
                inserted = connection.execute(
                    'INSERT INTO provider_sessions(provider_session_ref, provider, backend_route_id, '
                    'backend_session_id, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s) '
                    'ON CONFLICT (provider_session_ref) DO NOTHING RETURNING provider_session_ref',
                    (session_ref, clean_provider, clean_route, clean_session, at, at),
                ).fetchone()
                marking_agent = str(context_agent_id or agent_id or "").strip()
                if inserted is not None and inherited and marking_agent:
                    mark_context_received(
                        connection, marking_agent, session_ref, inherited, actor_ref=actor_ref,
                    )
            else:
                connection.execute(
                    'UPDATE provider_sessions SET updated_at = %s WHERE provider_session_ref = %s',
                    (at, session_ref),
                )
            return {
                "provider_session_ref": session_ref,
                "provider": clean_provider,
                "backend_route_id": clean_route,
                "backend_session_id": clean_session,
                "agent_id": str(agent_id or ""),
            }

        return self.database.write(write)

    def provider_binding(self, binding_id: str) -> dict[str, Any]:
        clean_id = str(binding_id or "").strip()
        if not clean_id:
            return {}
        with self.database.read() as connection:
            row = _binding_row(connection, clean_id)
        return _binding_record(row) if row is not None else {}

    def provider_binding_routes(
        self,
        binding_ids: Iterable[object],
    ) -> dict[str, dict[str, Any]]:
        requested = tuple(dict.fromkeys(
            str(item or "").strip()
            for item in binding_ids
            if str(item or "").strip()
        ))
        if not requested:
            return {}
        records: dict[str, dict[str, Any]] = {}
        with self.database.read() as connection:
            for chunk in in_batches(requested):
                placeholders = ",".join("%s" for _ in chunk)
                rows = connection.execute(
                    "SELECT binding.binding_id, session.provider_session_ref, "
                    "session.provider, session.backend_route_id, session.backend_session_id "
                    "FROM provider_bindings AS binding "
                    "JOIN provider_sessions AS session "
                    "ON session.provider_session_ref = binding.provider_session_ref "
                    f"WHERE binding.binding_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                records.update((str(row["binding_id"]), dict(row)) for row in rows)
        return records

    def bind_provider_session(
        self,
        binding_id: str,
        provider_session_ref: str,
        *,
        context_fingerprint: object = FIELD_NOT_PROVIDED,
        context_injected_at: object = FIELD_NOT_PROVIDED,
        context_injection_count: object = FIELD_NOT_PROVIDED,
        harness_tool_names: object = FIELD_NOT_PROVIDED,
    ) -> dict[str, Any]:
        clean_binding = str(binding_id or "").strip()
        clean_session_ref = str(provider_session_ref or "").strip()
        if not clean_binding or not clean_session_ref:
            raise ValueError("Provider binding and Session identities are required")

        def write(connection: Any) -> dict[str, Any]:
            if connection.execute(
                'SELECT 1 FROM provider_sessions WHERE provider_session_ref = %s',
                (clean_session_ref,),
            ).fetchone() is None:
                raise KeyError(clean_session_ref)
            existing = _binding_row(connection, clean_binding)
            at = utc_now()
            fingerprint = (
                str(existing["context_fingerprint"] or "")
                if existing is not None and context_fingerprint is FIELD_NOT_PROVIDED
                else str("" if context_fingerprint is FIELD_NOT_PROVIDED else context_fingerprint or "")
            )
            injected_at = (
                (iso_time(existing["context_injected_at"]) or "")
                if existing is not None and context_injected_at is FIELD_NOT_PROVIDED
                else str("" if context_injected_at is FIELD_NOT_PROVIDED else context_injected_at or "")
            )
            injection_count = (
                int(existing["context_injection_count"] or 0)
                if existing is not None and context_injection_count is FIELD_NOT_PROVIDED
                else max(0, int(0 if context_injection_count is FIELD_NOT_PROVIDED else context_injection_count or 0))
            )
            tools_json = json_document(clean_string_tuple(
                existing["harness_tool_names_json"]
                if existing is not None and harness_tool_names is FIELD_NOT_PROVIDED
                else () if harness_tool_names is FIELD_NOT_PROVIDED else harness_tool_names
            ))
            if existing is None:
                connection.execute(
                    '\n                    INSERT INTO provider_bindings(\n                        binding_id, provider_session_ref, context_fingerprint,\n                        context_injected_at, context_injection_count,\n                        harness_tool_names_json, created_at, updated_at\n                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)\n                    ',
                    (
                        clean_binding,
                        clean_session_ref,
                        fingerprint,
                        instant(injected_at),
                        injection_count,
                        tools_json,
                        at,
                        at,
                    ),
                )
            else:
                if str(existing["provider_session_ref"]) != clean_session_ref:
                    raise ValueError("Provider binding identity is immutable")
                connection.execute(
                    '\n                    UPDATE provider_bindings\n                    SET context_fingerprint = %s, context_injected_at = %s,\n                        context_injection_count = %s, harness_tool_names_json = %s,\n                        updated_at = %s\n                    WHERE binding_id = %s\n                    ',
                    (
                        fingerprint,
                        instant(injected_at),
                        injection_count,
                        tools_json,
                        at,
                        clean_binding,
                    ),
                )
            row = _binding_row(connection, clean_binding)
            if row is None:
                raise RuntimeError("Provider binding write did not produce a record")
            return _binding_record(row)

        return self.database.write(write)

    def replace_provider_binding(
        self,
        binding_id: str,
        prepared_binding_id: str,
        *,
        expected_provider: str = "",
        expected_backend_session_id: str = "",
        expected_provider_session_ref: str = "",
        expected_absent: bool = False,
    ) -> dict[str, Any]:
        canonical = str(binding_id or "").strip()
        prepared = str(prepared_binding_id or "").strip()
        if not canonical or not prepared or canonical == prepared:
            raise ValueError("Provider binding replacement requires distinct identities")
        if expected_absent and (
            expected_provider or expected_backend_session_id or expected_provider_session_ref
        ):
            raise ValueError("An expected absent binding cannot also have an expected identity")

        def write(connection: Any) -> dict[str, Any]:
            previous_row = _binding_row(connection, canonical)
            prepared_row = _binding_row(connection, prepared)
            if expected_absent and previous_row is not None:
                raise RuntimeError("Provider binding changed before replacement")
            if previous_row is None and not expected_absent:
                raise KeyError(f"Provider binding not found: {canonical}")
            if prepared_row is None:
                raise KeyError(f"Prepared Provider binding not found: {prepared}")
            if (
                expected_provider_session_ref
                and str(previous_row["provider_session_ref"]) != expected_provider_session_ref
            ):
                raise RuntimeError("Provider binding changed before replacement")
            if expected_provider and str(previous_row["provider"]) != expected_provider:
                raise RuntimeError("Provider binding changed before replacement")
            if (
                expected_backend_session_id
                and str(previous_row["backend_session_id"]) != expected_backend_session_id
            ):
                raise RuntimeError("Provider binding changed before replacement")
            previous = _binding_record(previous_row) if previous_row is not None else {}
            connection.execute(
                'DELETE FROM provider_bindings WHERE binding_id = %s', (canonical,)
            )
            connection.execute(
                'UPDATE provider_bindings SET binding_id = %s, updated_at = %s WHERE binding_id = %s',
                (canonical, utc_now(), prepared),
            )
            current_row = _binding_row(connection, canonical)
            if current_row is None:
                raise RuntimeError("Provider binding replacement did not produce a record")
            return {
                "previous_binding": previous,
                "current_binding": _binding_record(current_row),
            }

        return self.database.write(write)

    def restore_provider_binding(
        self,
        binding_id: str,
        *,
        expected_provider_session_ref: str,
        binding: Mapping[str, Any],
        expected_absent: bool = False,
    ) -> dict[str, Any]:
        canonical = str(binding_id or "").strip()
        expected = str(expected_provider_session_ref or "").strip()
        previous_ref = str(binding.get("provider_session_ref") or "").strip()
        if not canonical or (not expected and not expected_absent) or not previous_ref:
            raise ValueError("Provider binding restoration requires explicit current and prior state")

        def write(connection: Any) -> dict[str, Any]:
            current = _binding_row(connection, canonical)
            if expected_absent:
                if current is not None:
                    raise RuntimeError("Provider binding changed after detachment")
                connection.execute(
                    "INSERT INTO provider_bindings (binding_id, provider_session_ref, created_at, updated_at, "
                    "context_fingerprint, context_injected_at, context_injection_count, harness_tool_names_json) "
                    "VALUES (%s,%s,%s,%s,'',NULL,0,'[]')",
                    (canonical, previous_ref, utc_now(), utc_now()),
                )
                current = _binding_row(connection, canonical)
            if current is None or str(current["provider_session_ref"]) != (previous_ref if expected_absent else expected):
                raise RuntimeError("Provider binding changed after replacement")
            if connection.execute(
                'SELECT 1 FROM provider_sessions WHERE provider_session_ref = %s',
                (previous_ref,),
            ).fetchone() is None:
                raise KeyError(previous_ref)
            at = utc_now()
            connection.execute(
                '\n                UPDATE provider_bindings\n                SET provider_session_ref = %s, context_fingerprint = %s,\n                    context_injected_at = %s, context_injection_count = %s,\n                    harness_tool_names_json = %s, updated_at = %s\n                WHERE binding_id = %s\n                ',
                (
                    previous_ref,
                    str(binding.get("context_fingerprint") or ""),
                    instant(str(binding.get("context_injected_at") or "")),
                    max(0, int(binding.get("context_injection_count") or 0)),
                    json_document(clean_string_tuple(binding.get("harness_tool_names"))),
                    at,
                    canonical,
                ),
            )
            row = _binding_row(connection, canonical)
            if row is None:
                raise RuntimeError("Provider binding restoration did not produce a record")
            return _binding_record(row)

        return self.database.write(write)

    def delete_provider_binding(
        self, binding_id: str, *, expected_provider_session_ref: str = "",
    ) -> None:
        clean_id = str(binding_id or "").strip()
        if not clean_id:
            return

        def write(connection: Any) -> None:
            current = _binding_row(connection, clean_id)
            if current is None:
                return
            if (
                expected_provider_session_ref
                and str(current["provider_session_ref"]) != expected_provider_session_ref
            ):
                raise RuntimeError("Provider binding changed before release")
            connection.execute(
                'DELETE FROM provider_bindings WHERE binding_id = %s', (clean_id,)
            )

        self.database.write(write)
