"""API-only installations can configure Harness and admit local commands.

:module: lineage.service
:design: decision_8b2fa224616b
"""
from __future__ import annotations

import pytest

from management_portal.execution import account_registrations, codex_runtime_settings
from management_portal.society_service import resident_session_backend_domain as commands
from management_portal.society_service import resident_runtime_domain as runtime
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401

pytestmark = pytest.mark.empty_execution_registrations


@pytest.fixture
def api_only(monkeypatch):
    from management_portal.society_service import resident_backend
    monkeypatch.setattr(resident_backend, "session_record", lambda _: {})
    account_registrations.create_registration({
        "account_id": "deepseek_only", "provider": "provider_api", "kind": "api_profile",
        "config": {"api_id": "deepseek_test"},
    }, "test")
    assert account_registrations.registrations("codex") == []


@pytest.mark.parametrize("content,handled", [
    ("hello", False), ("/portal-help", False), ("/portal-tokens", False),
    ("/tmp/project/readme.md", False), ("/unknown", False), ("/", False),
    ("/status", True), ("/goal", True), ("/plan on", True),
])
def test_seed_resident_local_commands_need_no_codex_account(api_only, content, handled):
    result = commands._handle_resident_native_command(
        {"backend": "codex", "backend_route_id": ""}, content,
        {"backend": "codex", "model": "gpt-6-luna"},
        session_id="", cwd="/tmp", actor="test", can_update_runtime=True,
        can_manage_permissions=False, scope_label="test", updated_at="", token_usage={}, goal={},
    )
    assert result["handled"] is handled


def test_harness_only_update_preserves_old_model_without_codex_catalog(api_only, monkeypatch):
    def no_catalog(**kwargs):
        pytest.fail("Harness-only updates must not fetch a model catalog")
    monkeypatch.setattr(codex_runtime_settings, "codex_runtime_catalog", no_catalog)
    updates = {"agent_harness": {"mode": "auto"}}
    actual = runtime._validate_backend_runtime_updates(
        {"backend": "codex", "backend_route_id": ""},
        {"backend": "codex", "model": "gpt-6-luna", "reasoning_effort": "high"}, updates,
    )
    assert actual == updates


def test_api_only_user_can_save_and_read_back_harness_on_unbound_ama(api_only, future_creation_store, monkeypatch):
    from management_portal.society_service import principal_hierarchy_domain, resident_catalog
    from management_portal.society_service import resident_execution_domain
    ama = principal_hierarchy_domain.ensure_personal_ama("api_user", "api_user")
    record = resident_catalog.get_catalog().get(ama["resident_id"])
    before = runtime._read_resident_runtime_settings(record)
    before["model"] = "gpt-6-luna"
    runtime._write_resident_runtime_settings(record, before)
    monkeypatch.setattr(codex_runtime_settings, "codex_runtime_catalog", lambda **_: pytest.fail("unexpected Codex catalog"))
    resident_execution_domain.change_resident_runtime_settings(ama["resident_id"], "api_user", {
        "agent_harness": {"mode": "auto", "features": {"provider_error_continue": {"enabled": False}}},
    })
    current = runtime._read_resident_runtime_settings(resident_catalog.get_catalog().get(ama["resident_id"]))
    assert current["model"] == "gpt-6-luna"
    assert current["agent_harness"]["features"]["provider_error_continue"]["enabled"] is False
    assert resident_catalog.get_catalog().get(ama["resident_id"])["backend_route_id"] == ""


def test_model_command_still_requires_the_selected_native_account(api_only):
    with pytest.raises(ValueError, match="No enabled default Codex account"):
        commands._handle_resident_native_command(
            {"backend": "codex", "backend_route_id": ""}, "/model", {},
            session_id="", cwd="/tmp", actor="test", can_update_runtime=True,
            can_manage_permissions=False, scope_label="test", updated_at="", token_usage={}, goal={},
        )
