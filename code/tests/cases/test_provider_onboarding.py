"""Provider onboarding keeps installation, authorization and execution separate.

:module: execution.providers
"""
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from management_portal.execution import account_setup as setup, account_registrations as registrations
from management_portal.execution import provider_updates as updates
from management_portal.hatg.contracts import InstructionError


def invoke(**args):
    return SimpleNamespace(caller="alice", argument=args.get)


def row(provider="claude"):
    return {"account_id": "existing", "provider": provider, "kind": "subscription_login",
            "route_id": "existing", "config": {}, "version": 1, "enabled": True}


@pytest.mark.parametrize("provider", ["codex", "claude", "kimi", "dsh", "opencode"])
def test_prepare_reuses_existing_runtime(monkeypatch, provider):
    """已有运行时不下载安装、不要求隔离路径。"""
    runtime = Mock()
    monkeypatch.setattr(updates, "runtimes", lambda: {provider: runtime})
    monkeypatch.setattr(updates, "runtime_available", lambda name: True)
    assert updates.prepare_runtime(provider) == {"status": "ready", "source": "existing"}
    runtime.stage.assert_not_called()


def test_prepare_api_needs_no_program():
    assert updates.prepare_runtime("provider_api")["source"] == "not_required"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_login_reuses_path_or_prepared_runtime(monkeypatch, tmp_path, provider):
    from management_common import site_config
    from management_portal.execution import account_login
    monkeypatch.setattr(site_config, "read_site_config", lambda: {})
    runtime = updates.CodexRuntime if provider == "codex" else updates.ClaudeRuntime
    binary = tmp_path / ("codex" if provider == "codex" else
                        "lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude")
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setattr(runtime, "link", lambda self: binary if provider == "codex" else tmp_path)
    monkeypatch.setattr(account_login.shutil, "which", lambda _: None)
    assert account_login.login_command(provider, status=True)[0] == str(binary)
    existing = str(tmp_path / "developer-installed-cli")
    monkeypatch.setattr(account_login.shutil, "which", lambda _: existing)
    assert account_login.login_command(provider)[0] == existing


def test_codex_link_reuses_configured_or_path_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(updates.sys, "platform", "linux")
    monkeypatch.setattr(updates, "_PROVIDER_DEPLOYMENT", {})
    existing = tmp_path / "existing-codex"
    monkeypatch.setattr(updates.shutil, "which", lambda _: str(existing))
    assert updates.CodexRuntime().link() == existing
    configured = tmp_path / "configured-codex"
    monkeypatch.setattr(updates, "_PROVIDER_DEPLOYMENT", {"codex_binary": str(configured)})
    assert updates.CodexRuntime().link() == configured


def test_missing_runtime_preparation_can_retry(monkeypatch, tmp_path):
    from management_portal import business_tasks
    runtime = Mock()
    runtime.stage.side_effect = [RuntimeError("private download details"), tmp_path / "runtime"]
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(updates, "runtimes", lambda: {"claude": runtime})
    installed = []
    monkeypatch.setattr(updates, "runtime_available", lambda name: bool(installed))
    monkeypatch.setattr(updates, "_activate", lambda *args: installed.append(True) or True)
    monkeypatch.setattr(business_tasks, "start", lambda task_id, work: work())
    updates.prepare_runtime("claude")
    failed = updates.preparation_status("claude")
    assert failed["status"] == "failed" and "private" not in str(failed)
    updates.prepare_runtime("claude")
    assert updates.preparation_status("claude")["status"] == "ready"


@pytest.mark.parametrize("previous", ["pending", "failed", "preparing"])
def test_preparation_observes_recovered_runtime(monkeypatch, tmp_path, previous):
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(updates, "runtime_available", lambda _: True)
    (tmp_path / "provider-updates.json").write_text(json.dumps({"preparations": {
        "claude": {"status": previous, "message": "old failure or waiting message"}}}))
    result = updates.preparation_status("claude")
    assert result["status"] == "ready"
    assert not result.get("message")


def test_preparation_does_not_claim_a_removed_runtime_is_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(updates, "runtime_available", lambda _: False)
    (tmp_path / "provider-updates.json").write_text(json.dumps({"preparations": {
        "claude": {"status": "ready", "source": "installed"}}}))
    assert updates.preparation_status("claude")["status"] == "missing"


@pytest.mark.parametrize("provider", ["codex", "claude", "provider_api"])
def test_check_uses_normal_execution_owner(monkeypatch, tmp_path, provider):
    from management_portal.execution import resident_configuration, account_hosting
    hosting = []
    monkeypatch.setattr(account_hosting, "install_codex_unit_template", lambda: hosting.append("install"))
    monkeypatch.setattr(account_hosting, "start_codex_instance", lambda account_id: hosting.append(account_id))
    account = row(provider)
    monkeypatch.setattr(setup, "catalog", lambda _row: {"models": ["model"]})
    if provider == "provider_api":
        account.update(kind="api_profile", config={"api_id": "api_existing"})
    monkeypatch.setattr(setup, "account", lambda _: account)
    monkeypatch.setattr(setup, "signature", lambda _: {"registration_version": 1})
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(updates, "runtime_available", lambda _: True)
    monkeypatch.setattr(setup, "AGENT_PROVIDERS", {provider: SimpleNamespace(validate_settings=lambda *args: None,
        runtime_adapter=lambda: SimpleNamespace(defaults=lambda *args: {"permission_preset": "full-access"}))})
    monkeypatch.setattr(resident_configuration, "validate_api_settings", lambda *args: None)
    requests = []
    client = SimpleNamespace(submit=lambda req: (requests.append(req) or SimpleNamespace(execution_id=req.execution_id)))
    monkeypatch.setattr(setup, "ExecutionClient", lambda: client)
    result = setup.check(invoke(account_id="existing", model="model"))
    assert result["execution_id"]
    request = requests[0]
    assert request.actor == "alice"
    assert request.provider == ("api_existing" if provider == "provider_api" else provider)
    assert request.service_type == ("chat_completion" if provider == "provider_api" else "agent_session")
    assert request.metadata["provider_account_check"]["account_id"] == "existing"
    assert hosting == (["existing"] if provider == "codex" else [])
    if provider != "provider_api":
        assert request.permission_profile == "owner" and request.runtime_options["is_owner"]
        assert request.runtime_options["runtime_settings"]["permission_preset"] == "full-access"


def test_default_requires_successful_current_account_check(monkeypatch):
    account = row()
    snapshot = {"execution_id": "execution_test", "status": "completed", "request": {"metadata": {
        "provider_account_check": {"account_id": "existing", "model": "model", "registration_version": 1}}},
        "result": {"output": {"message": "PORTAL_PROVIDER_OK"}}}
    monkeypatch.setattr(setup, "account", lambda _: account)
    monkeypatch.setattr(setup, "signature", lambda r: {"registration_version": r["version"]})
    monkeypatch.setattr(setup, "ExecutionClient", lambda: SimpleNamespace(status=lambda *a, **k: snapshot))
    args = invoke(account_id="existing", model="model", verification_execution_id="execution_test")
    setup.require_verified(args)
    account["version"] = 2
    with pytest.raises(InstructionError):
        setup.require_verified(args)
    account["version"] = 1
    snapshot["result"]["output"]["message"] = "Unable to reply PORTAL_PROVIDER_OK"
    with pytest.raises(InstructionError):
        setup.require_verified(args)


@pytest.mark.parametrize("stage", ["start", "ready"])
def test_failed_codex_service_reports_recovery_without_submitting(monkeypatch, stage):
    from management_portal.execution import account_hosting
    from management_portal.codex_app_server_client import CodexAppServerUnavailable
    monkeypatch.setattr(setup, "account", lambda _: row("codex"))
    monkeypatch.setattr(updates, "runtime_available", lambda _: True)
    start = Mock(side_effect=OSError("private service detail") if stage == "start" else None)
    monkeypatch.setattr(account_hosting, "start_codex_instance", start)
    monkeypatch.setattr(setup, "_started_codex_catalog", Mock(side_effect=CodexAppServerUnavailable("private socket path")))
    client = Mock()
    monkeypatch.setattr(setup, "ExecutionClient", client)
    with pytest.raises(InstructionError) as error:
        setup.check(invoke(account_id="existing", model="model"))
    assert "登录授权仍然保留" in str(error.value)
    assert "private" not in str(error.value)
    assert error.value.retryable
    client.assert_not_called()


def test_unmapped_imported_role_uses_default(monkeypatch):
    monkeypatch.setattr(registrations, "roles", lambda: [
        {"role": "coding", "account_id": None}, {"role": "default", "account_id": "existing"}])
    resolved, fallback = registrations.resolve_role("coding")
    assert resolved["account_id"] == "existing"
    assert fallback == {"requested": "coding", "used": "default"}


def test_existing_bindings_keep_their_account(monkeypatch):
    from management_portal.society_service import resident_default_backend as defaults
    configured = {"backend": "claude", "backend_route_id": "existing", "runtime_settings": {"model": "model"}}
    monkeypatch.setattr(defaults, "role_configuration", lambda _: configured)
    assert defaults.configuration({"backend": "codex", "backend_route_id": "old"}, {}) is None
    assert defaults.configuration({"backend": "codex"}, {"backend_session_id": "running"}) is None
    assert defaults.configuration({"backend": "codex"}, {}) == configured


def test_new_resident_uses_default_without_explicit_role(monkeypatch):
    from management_portal.society_service.isa import residents
    registrations.create_registration({"account_id": "claude_first", "provider": "claude", "kind": "subscription_login"}, "alice")
    registrations.update_role("default", "claude_first", "model", "", None, "alice")
    created = []
    monkeypatch.setattr(residents, "_family_to_join", lambda *a: "")
    monkeypatch.setattr(residents, "placement", lambda *a: {})
    monkeypatch.setattr(residents, "domain", lambda: SimpleNamespace(create_lineage_resident=lambda lineage, payload, actor:
        created.append(payload) or {"resident": {"resident_id": "resident_000000000001"}}))
    residents.create_resident(invoke(title="New", lineage_id="lineage_000000000001"))
    assert created[0]["backend"] == "claude"
    assert created[0]["backend_route_id"] == "claude_first"
    assert created[0]["runtime_settings"]["model"] == "model"
