"""Fixtures and helpers shared by the Resident execution protocol tests.

``resident_record`` registers one Codex Resident with its Lineage, owned Workspace and
Provider binding; ``input_delivery`` delivers one input through the Resident protocol.
"""
from __future__ import annotations
from management_portal.society_service import resident_backend
from management_portal.society_service.resident_domain_common import LINEAGE_FOLDER_SCHEMA_VERSION
from management_portal.execution import slash_commands

import base64
from pathlib import Path
from unittest import mock

import pytest

from management_portal.society_service import (
    lineage_state,
    lineage_lifecycle,
    lineage_graph_edits,
    resident_execution_provider,
    resident_intake_domain,
    resident_protocol,
    resident_record_domain,
    resident_registration_domain,
    resident_session_backend_domain,
    resource_client,
)
from management_portal.execution.agent_data_store import AgentDataStore, get_agent_data_store
from management_portal.execution.account_adapters import CodexRoutes
from management_portal.execution.service import ExecutionService
from management_portal.execution.session_manager import SessionManager
from management_portal.society_service import execution_client
from management_portal.society_service import lineage_aggregate
from tests.cases.society_service_test_support import install_resource_boundary


VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _message_text(message: dict[str, object]) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _activity(resident_id: str, kind: str = "thinking") -> dict[str, str]:
    return {
        "activity_id": f"activity:{kind}",
        "resident_id": resident_id,
        "message_id": "message_active",
        "kind": kind,
        "status": "running",
        "title": kind.title(),
        "content": "",
        "started_at": "2026-08-28T00:00:00Z",
        "updated_at": "2026-08-28T00:00:00Z",
    }


def _deliver_session(
    record: dict[str, object],
    job: dict[str, object],
    *,
    persist_messages: bool = True,
) -> resident_execution_provider.DeliveryResult:
    provider = resident_execution_provider.provider_for(record)
    return provider.deliver(
        resident_execution_provider.DeliveryRequest(
            lineage_id=str(record.get("lineage_id") or ""),
            resident_id=str(record.get("resident_id") or ""),
            actor_id=str(job.get("actor") or "CASE-A"),
            message=str(job.get("message") or ""),
            resident=record,
            delivery=job,
            persist_messages=persist_messages,
        )
    )


def _bind(
    store: AgentDataStore,
    binding_id: str,
    provider: str,
    backend_session_id: str,
    *,
    backend_route_id: str = "",
) -> None:
    session = store.ensure_provider_session(
        provider=provider,
        backend_route_id=backend_route_id,
        backend_session_id=backend_session_id,
        agent_id=binding_id,
        actor_ref="test",
    )
    store.bind_provider_session(binding_id, session["provider_session_ref"])


@pytest.fixture
def resident_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    monkeypatch.setattr(execution_client, "configure_binding_quota_overrides", mock.Mock(return_value={"updated": 0}))
    from dataclasses import asdict
    from management_portal.execution.accounts import default_execution_accounts
    monkeypatch.setattr(execution_client, "health", lambda: {})
    def account_catalog(_service, **kwargs):
        try:
            partitions = execution_client.health().get("scheduler", {}).get("capacity_partitions", {})
        except RuntimeError:
            partitions = {}
        return {"accounts": [{**asdict(a), "backend_route_id": a.route_id,
                "quota_reserve": partitions.get(a.capacity_key, {}).get("quota_reserve", {})}
                for a in default_execution_accounts(account_registry=CodexRoutes()).accounts()]}
    def resolve_account(_service, provider, backend_route_id="", runtime_options=None, **kwargs):
        account = default_execution_accounts(account_registry=CodexRoutes()).resolve(provider, backend_route_id, runtime_options)
        return {**asdict(account), "backend_route_id": account.route_id} if account else {}
    monkeypatch.setattr(ExecutionService, "states", lambda self, executions: {key: {"status": "running"} for key in executions})
    monkeypatch.setattr(ExecutionService, "accounts", account_catalog)
    monkeypatch.setattr(ExecutionService, "resolve_account", resolve_account)
    monkeypatch.setattr(resident_registration_domain, "resolve_account_configuration", lambda *a, **kw: resolve_account(None, *a, **kw))
    monkeypatch.setattr(resident_backend, "resolve_account_configuration", lambda *a, **kw: resolve_account(None, *a, **kw))

    lineage_root = tmp_path / "lineages"
    workspace = lineage_root / "lineage_000000000001"
    default_cwd = workspace / "residents" / "resident_aaaaaaaaaaaa" / "workspace"
    monkeypatch.setattr(lineage_aggregate, "DATA_ROOT", tmp_path / "lineage-state")
    monkeypatch.setattr(lineage_state, "LINEAGE_FOLDER_ROOT", lineage_root)
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(lineage_root))
    session_store = AgentDataStore()
    monkeypatch.setattr(
        execution_client,
        "sessions",
        lambda: SessionManager(agent_data=session_store),
    )
    deliver_resident_input = resident_protocol.deliver_resident_input
    install_resource_boundary(monkeypatch)
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", deliver_resident_input)
    record: dict[str, object] = {
        "resident_id": "resident_aaaaaaaaaaaa",
        "folder_schema": LINEAGE_FOLDER_SCHEMA_VERSION,
        "lineage_id": "lineage_000000000001",
        "project_id": "project_test",
        "workspace_path": str(workspace),
        "owned_workspace_id": "workspace_111111111111",
        "owned_workspace": {
            "workspace_id": "workspace_111111111111",
            "path": str(default_cwd),
        },
        "workspace_bindings": [],
        "default_cwd": str(default_cwd),
        "backend": "codex",
        "session_state": "active",
    }
    resource_client.sync_lineage({
        "lineage_id": record["lineage_id"],
    })
    # Canonical membership now requires its aggregate to exist before a
    # Resident is inserted; initialize graph attributes after membership.
    with lineage_aggregate.lineage_scope(str(record["lineage_id"])):
        lineage_lifecycle._create_lineage_in_scope(
            {"title": "Resident protocol test", "project_id": record["project_id"],
             "nodes": [], "edges": []},
            "CASE-A",
            str(record["lineage_id"]),
        )
    default_cwd.mkdir(parents=True)
    resident_record_domain.save_resident_record(record)
    _bind(session_store,
        "resident:resident_aaaaaaaaaaaa",
        "codex",
        "019f0000-0000-7000-8000-000000000001",
        backend_route_id="zero",
    )
    lineage_graph_edits.update_lineage(
        str(record["lineage_id"]),
        {**lineage_aggregate.read_lineage(str(record["lineage_id"]), actor="CASE-A"),
         "leader_resident_id": record["resident_id"]},
        "CASE-A",
    )
    return record


def _resident_test_codex_registry(tmp_path: Path, monkeypatch) -> tuple[CodexRoutes, Path, Path]:
    from tests.cases.account_registration_test_support import register_codex
    registry = register_codex(monkeypatch, tmp_path)
    zero_home, one_home = registry.get("zero").codex_home, registry.get("one").codex_home
    zero_home.mkdir(parents=True, exist_ok=True)
    one_home.mkdir(parents=True, exist_ok=True)
    return registry, zero_home, one_home


_TURN_510K = {
    "input_tokens": 510_000, "cached_input_tokens": 470_000, "output_tokens": 3_000,
    "reasoning_output_tokens": 900, "total_tokens": 513_000,
}


def _ended_execution(agent_id: str, execution_id: str, status: str, counted: dict[str, int]) -> None:
    store = get_agent_data_store()
    if store.execution(execution_id) is None:
        store.begin_execution(
        execution_id=execution_id, agent_id=agent_id, actor_ref="CASE-A", provider="codex",
            started_at="2026-08-17T00:00:00Z",
        )
    store.finish_execution(execution_id, status=status, finished_at="2026-08-17T00:00:01Z")
    assert store.record_accounted_usage(execution_id, counted)


@pytest.fixture
def input_delivery(resident_record, monkeypatch):
    monkeypatch.setattr(resident_session_backend_domain, "handle_resident_native_command", lambda *a, **kw: {})
    monkeypatch.setattr(slash_commands, "handle_slash_command", lambda *a, **kw: {"handled": False})
    monkeypatch.setattr(resident_intake_domain, "resolve_context_refs", lambda *a, **kw: {})
    return resident_record
