from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Mapping

from management_portal.execution.provider_catalog import AGENT_PROVIDERS, provider_name
from management_portal.execution.accounts import resolve_account_configuration

from .domain_ports import execution_port


PERSISTED_SESSION_FIELDS = frozenset({
    "backend_binding",
    "backend_session_id",
    "codex_session_path",
    "codex_thread_id",
    "forkedFromId",
    "sessionId",
    "session_history",
})


def session_id(value: object) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 160:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", clean):
        return ""
    return clean


def backend_name(record: Mapping[str, Any]) -> str:
    return provider_name(record.get("backend"))


def execution_backend(record: Mapping[str, Any]) -> str:
    return backend_name(record)


def execution_provider(record: Mapping[str, Any]) -> str:
    if execution_backend(record) == "provider_api":
        return str(record.get("backend_route_id") or "").strip()
    provider = AGENT_PROVIDERS.get(execution_backend(record))
    return provider.provider if provider else execution_backend(record)


def backend_label(record: Mapping[str, Any]) -> str:
    if execution_backend(record) == "provider_api":
        provider_id = execution_provider(record)
        return provider_id or "Model API"
    if execution_backend(record) == "portal":
        return "Portal 规则"
    provider = AGENT_PROVIDERS.get(execution_backend(record))
    # A deleted Resident may have no backend left.
    return provider.label if provider else "未知"


def execution_binding_id(
    record: Mapping[str, Any],
) -> str:
    resident_id = str(record.get("resident_id") or "").strip()
    if not resident_id:
        raise ValueError("resident_id missing for execution binding")
    return f"resident:{resident_id}"


def validate_resident_session_identity(record: Mapping[str, Any]) -> None:
    stale = sorted(_persisted_session_paths(record))
    if stale:
        raise ValueError(
            "Resident record contains persisted Agent session fields: "
            + ", ".join(stale)
        )


def _persisted_session_paths(
    value: object,
    prefix: str = "",
) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            path = f"{prefix}.{key}" if prefix else key
            lowered = key.lower()
            if (
                key in PERSISTED_SESSION_FIELDS
                or lowered == "session_id"
                or lowered.endswith("_session_id")
                or lowered.endswith("_thread_id")
            ):
                paths.add(path)
                continue
            paths.update(_persisted_session_paths(item, path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            paths.update(
                _persisted_session_paths(item, f"{prefix}[{index}]")
            )
    return paths


def record_session_id(record: Mapping[str, Any]) -> str:
    try:
        binding_id = execution_binding_id(record)
    except ValueError:
        return ""
    return _stored_session_id(binding_id)


def session_record(
    record: Mapping[str, Any],
    *, expected_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return execution_port.sessions().provider_binding(
        execution_binding_id(record), expected_binding=expected_binding,
    )


def session_record_for_binding(binding_id: object) -> dict[str, Any]:
    clean_binding_id = str(binding_id or "").strip()
    if not clean_binding_id:
        return {}
    return execution_port.sessions().provider_binding(clean_binding_id)


def session_routes_for_bindings(
    binding_ids: Iterable[object],
) -> dict[str, dict[str, Any]]:
    return execution_port.sessions().provider_binding_routes(binding_ids)




def account_id(
    record: Mapping[str, Any],
    *,
    binding: Mapping[str, Any] | None = None,
) -> str:
    return str(execution_account(record, binding).get("account_id") or "other")




def delete_session_identity(
    record: Mapping[str, Any],
    *, expected_provider_session_ref: str = "",
) -> None:
    execution_port.sessions().release_session(
        execution_binding_id(record), expected_provider_session_ref=expected_provider_session_ref,
    )
    # ClearContext returns before this durable cleanup finishes. A header may
    # still hold the old session, so publish after the binding is actually gone.
    from .changes import resident_changed

    resident_changed(record.get("resident_id"), scope="identity")


def replace_with_prepared_session(
    record: Mapping[str, Any],
    prepared_binding_id: object,
    *,
    expected_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return execution_port.sessions().replace_session(
        execution_binding_id(record),
        str(prepared_binding_id or "").strip(),
        expected_binding=expected_binding,
    )


def restore_replaced_session(
    record: Mapping[str, Any],
    replacement: Mapping[str, Any],
) -> dict[str, Any]:
    return execution_port.sessions().restore_session(
        execution_binding_id(record), replacement,
    )


def discard_prepared_session(prepared_binding_id: object) -> None:
    execution_port.sessions().release_session(
        str(prepared_binding_id or "").strip()
    )


def _stored_session_id(binding_id: str) -> str:
    session = session_record_for_binding(binding_id)
    return session_id(session.get("backend_session_id"))


def execution_account(record: Mapping[str, Any], binding: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve public identity through ExecutionService, never infer it from a backend name."""
    session = session_record(record) if binding is None else binding
    from .resident_default_backend import configuration
    configured = configuration(record, session)
    if configured:
        record = {**record, "backend": configured["backend"], "backend_route_id": configured["backend_route_id"]}
    configuration = record.get("backend_binding") or {}
    return resolve_account_configuration(
        execution_provider(record),
        execution_account_route(record, session),
        runtime_options={"provider_profile": record.get("provider_profile") or configuration.get("provider_profile") or ""},
    )


def execution_account_projection(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project an available account without requiring execution readiness.

    Missing site configuration must not hide Resident history or settings.
    Execution callers continue to use execution_account and its strict resolver.
    """
    try:
        return execution_account(record)
    except (ValueError, KeyError):
        return {}


def execution_account_route(record: Mapping[str, Any], binding: Mapping[str, Any]) -> str:
    # The Model API route selects the provider itself. Its execution account
    # has no additional native session route.
    if execution_backend(record) == "provider_api":
        return ""
    return str(binding.get("backend_route_id") or record.get("backend_route_id") or "").strip()
