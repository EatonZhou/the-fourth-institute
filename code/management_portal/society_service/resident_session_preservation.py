"""Plan preserving session operations without running a model or rebinding."""
from __future__ import annotations

import hashlib
import json

from .domain_ports import execution_port


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def configuration_version(record):
    # Lifecycle timestamps/intake/work changes are not configuration changes.
    return digest({key: record.get(key) for key in (
        "backend", "backend_route_id", "execution_settings", "harness_config",
    )})


def request_signature(body):
    return digest({**{key: value for key, value in body.items() if key != "idempotency_key"},
                   "operation": body.get("operation") or "restart",
                   "context_strategy": body.get("context_strategy") or "preserve",
                   "active_execution_policy": body.get("active_execution_policy") or "wait",
                   "resume_failed_input": body.get("resume_failed_input", False)})


def check_replay(state, body):
    if state.get("request_signature") != request_signature(body):
        raise ValueError("Session operation id refers to a different request")


def canonical_seed(record, source_binding):
    """Freeze the effective window after work drains, without Chat truncation."""
    snapshot = execution_port.agent_context_snapshot(
        record["resident_id"], provider_session_ref=str(source_binding.get("provider_session_ref") or ""),
    )
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("segments"), list):
        raise ValueError("Cannot preserve context: canonical effective history is unavailable")
    segments = snapshot["segments"]
    if not segments and not snapshot.get("empty_context"):
        raise ValueError("Cannot preserve context: empty history has no explicit boundary")
    from management_portal.execution.session_history_import import validate_codex_preservation
    validate_codex_preservation(segments)
    return {"schema": "tfi.provider-context-seed.v1", "segments": [
        {"segment_id": item["segment_id"], **({"content_sha256": item["content_sha256"]}
                                              if item.get("content_sha256") else {})}
        for item in segments
    ], "empty_context": bool(snapshot.get("empty_context")), "source_agent_id": record["resident_id"]}


def preservation_capabilities():
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    return {**{name: {
        "same_account_native_fork": "fork" in definition.operations,
        "canonical_import": "validated_codex_history" if name == "codex" else False,
        "cross_provider_import": False,
        "empty_session_without_model": "create" in definition.operations,
    } for name, definition in AGENT_PROVIDERS.items()},
        "provider_api": {"same_account_native_fork": False, "canonical_import": False,
                         "cross_provider_import": False, "context_owner": "portal"}}
