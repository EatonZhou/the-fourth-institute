"""Antigravity's ACP boundary, account isolation and official runtime installation.

:module: spec.design-governance
:design: decision_44f8f67d7390
:importance: P2
"""
import hashlib
import io
import json
from pathlib import Path
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock
import zipfile

import pytest

from management_portal import antigravity_accounts as accounts, antigravity_runtime as runtime
from management_portal.antigravity_client import AcpClient, AcpError, AuthenticationRequired
from management_portal.execution.antigravity_backend import AntigravityBackend
from management_portal.execution.backend_contracts import BackendContext
from management_portal.execution.harness import HarnessTool
from tests.cases import capability_contracts

FAKE = Path(__file__).with_name("fake_antigravity.py")


@pytest.fixture
def agent(monkeypatch, tmp_path):
    from management_portal.execution import antigravity_backend as backend
    monkeypatch.setattr(backend, "command", lambda: [sys.executable, str(FAKE)])
    monkeypatch.setattr(accounts, "command", lambda: [sys.executable, str(FAKE)])
    monkeypatch.setattr(accounts, "account_for_route", lambda route: {"account_id": "antigravity_one", "route_id": "one"})
    monkeypatch.setattr(accounts, "account_home", lambda provider, account: tmp_path)
    monkeypatch.setattr(accounts, "environment", lambda account: {"GEMINI_HOME": str(tmp_path)})
    monkeypatch.setattr(backend, "resident_process_environment", lambda options: ({}, {}))
    return tmp_path


def invocation(root, text="hello", *, sid="", tools=(), owner=True, execution="execution-test"):
    return SimpleNamespace(
        request=SimpleNamespace(runtime_options={"provider_profile": "one", "model": "gemini-test"},
            operation="turn", execution_id=execution, cwd=str(root), provider="antigravity",
            permission_profile="owner" if owner else "restricted"),
        session=SimpleNamespace(backend_route_id="one", backend_session_id=sid),
        user_prompt=text, system_context="Portal rules", session_system_context="All Portal rules",
        attach_system_context=True, recovery_checkpoint={}, harness_tools=tuple(tools),
        available_harness_tool_names=frozenset(), tool_batch_processor=None, provider_event_processor=mock.Mock())


def context(cancel=None):
    events, state = [], {"execution_events": []}
    result = BackendContext(emit_event=lambda *args: state["execution_events"].append(args), cancellation_requested=cancel or (lambda: False),
        set_interrupt_target=lambda *args: None, set_provider_recovery_state=lambda value: state.update(value),
        provider_event_observer=lambda p, s, r, e: events.append(e),
        update_context_usage=lambda usage, window: state.update(context=usage, window=window))
    return result, events, state


def requests(root):
    return [json.loads(line) for line in (root / "requests.jsonl").read_text().splitlines()]


def test_turn_serves_portal_tools_and_reports_native_events_and_usage(agent):
    seen = []
    tool = HarnessTool("read_resource", "Read resource", {"type": "object"}, lambda args: seen.append(args) or "resource text")
    inv = invocation(agent, tools=[tool])
    ctx, events, state = context()
    result = AntigravityBackend().execute(inv, ctx)
    assert result.output["message"] == "hello:resource text"
    assert result.backend_session_id == "google-session-1"
    assert result.usage == {"input_tokens": 80, "output_tokens": 6, "total_tokens": 86}
    assert seen == [{"id": "resource-one"}]
    capability_contracts.check_tool_events(events)
    observed = [call.args[0] for call in inv.provider_event_processor.process_event.call_args_list]
    capability_contracts.check_tool_events(observed)
    assert any(e["method"] == "thread/tokenUsage/updated" for e in observed)
    assert sum(e["method"] == "thread/tokenUsage/updated" for e in observed) == 1
    assert any(e["method"] == "thread/tokenUsage/updated" for e in events)
    assert sum(kind == "provider_input_bound" for kind, _ in state["execution_events"]) == 1
    assert state["context"] == {"input_tokens": 80} and state["window"] == 1000
    assert state["phase"] == "completed"
    created = next(r["params"] for r in requests(agent) if r["method"] == "session/new")
    assert created["_meta"]["antigravity"]["systemPrompt"] == {"append": "All Portal rules"}
    assert "start_subagent" in created["_meta"]["antigravity"]["disabledTools"]


def test_resume_uses_native_history_without_replaying_it(agent):
    backend = AntigravityBackend()
    first = backend.execute(invocation(agent), context()[0])
    second = backend.execute(invocation(agent, "next", sid=first.backend_session_id, execution="execution-next"), context()[0])
    assert second.backend_session_id == first.backend_session_id
    assert second.output["message"] == "next"
    rows = requests(agent)
    assert [r["params"]["prompt"][0]["text"] for r in rows if r["method"] == "session/prompt"] == ["hello", "next"]
    assert len([r for r in rows if r["method"] == "session/load"]) == 1


def test_tool_progress_is_separate_from_final_answer(agent):
    ctx, events, _ = context()
    result = AntigravityBackend().execute(invocation(agent, "multi-step"), ctx)
    assert result.output["message"] == "Final answer."
    messages = [e["params"]["item"] for e in events if e["method"] == "item/completed"
                and e["params"]["item"]["type"] == "agentMessage"]
    assert [(m["text"], m["phase"]) for m in messages] == [
        ("Checking a file.", "commentary"), ("Final answer.", "final_answer")]
    assert messages[0]["id"] != messages[1]["id"]


@pytest.mark.parametrize("owner,outcome", [(True, "selected"), (False, "cancelled")])
def test_permissions_are_enforced_in_protocol_not_prompt(agent, owner, outcome):
    result = AntigravityBackend().execute(invocation(agent, "permission", owner=owner), context()[0])
    assert result.output["message"] == outcome
    meta = next(r["params"]["_meta"]["antigravity"] for r in requests(agent) if r["method"] == "session/new")
    assert ("enabledTools" not in meta) if owner else meta["enabledTools"] == []


def test_cancel_delivers_native_cancel_and_suppresses_late_answer(agent):
    cancel = threading.Event()
    timer = threading.Timer(.5, cancel.set)
    timer.start()
    try:
        with pytest.raises(InterruptedError):
            AntigravityBackend().execute(invocation(agent, "wait"), context(cancel.is_set)[0])
    finally:
        timer.cancel()
    assert any(r["method"] == "session/cancel" for r in requests(agent))


def test_disconnect_checkpoint_refuses_to_replay_input(agent):
    inv = invocation(agent, "disconnect")
    ctx, _, state = context()
    with pytest.raises(AcpError):
        AntigravityBackend().execute(inv, ctx)
    assert state["phase"] == "prompt_sent"
    assert not any(kind == "provider_input_bound" for kind, _ in state["execution_events"])
    inv.recovery_checkpoint = {"provider_recovery_state": state}
    with pytest.raises(RuntimeError, match="refusing to replay"):
        AntigravityBackend().execute(inv, context()[0])
    assert sum(r["method"] == "session/prompt" for r in requests(agent)) == 1


def test_completed_recovery_preserves_tool_capabilities_without_new_prompt(agent):
    tool = HarnessTool("read_resource", "Read", {"type": "object"}, lambda _: "text")
    inv = invocation(agent, tools=[tool])
    ctx, _, state = context()
    first = AntigravityBackend().execute(inv, ctx)
    inv.recovery_checkpoint = {"provider_recovery_state": state}
    second = AntigravityBackend().execute(inv, context()[0])
    assert second == first
    assert second.available_harness_tool_names == frozenset({"read_resource"})
    assert sum(r["method"] == "session/prompt" for r in requests(agent)) == 1


def test_stale_interrupt_cannot_cancel_a_later_turn_in_same_session(agent):
    backend = AntigravityBackend()
    inv = invocation(agent, "wait", sid="google-session-1", execution="current-turn")
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(backend.execute, inv, context()[0])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (agent / "requests.jsonl").exists() and any(r["method"] == "session/prompt" for r in requests(agent)):
                break
            time.sleep(.01)
        else:
            pytest.fail("Native prompt did not start")
        args = dict(backend_session_id="google-session-1", backend_route_id="one", runtime_options={})
        assert not backend.interrupt(turn_id="previous-turn", **args)
        assert not any(r["method"] == "session/cancel" for r in requests(agent))
        assert backend.interrupt(turn_id="current-turn", **args)
        with pytest.raises(InterruptedError):
            running.result(timeout=5)


def test_native_file_edit_is_recorded_as_a_turn_artifact(agent):
    ctx, events, _ = context()
    AntigravityBackend().execute(invocation(agent, "file-edit"), ctx)
    edits = [e["params"]["item"] for e in events if e["method"] == "item/completed"
             and e["params"]["item"].get("tool") == "edit"]
    assert edits[0]["arguments"] == {"file_path": "file.txt", "old_string": "old", "new_string": "new", "replace_all": False}
    artifacts = [e["params"]["item"] for e in events if e["method"] == "item/completed"
                 and e["params"]["item"].get("type") == "turnArtifacts"]
    assert "-old" in artifacts[0]["artifacts"]["diff"] and "+new" in artifacts[0]["artifacts"]["diff"]
    assert events[-1]["method"] == "turn/completed"


@pytest.mark.parametrize("error_name", ["ExecutionClaimOwnershipLost", "ExecutionServiceUnavailable", "ExecutionResponseUnknown"])
def test_execution_control_errors_cross_acp_reader_unchanged(agent, error_name):
    from management_portal.execution import transport
    failure = getattr(transport, error_name)("control plane unavailable")
    def failed_observer(params):
        if params["update"]["sessionUpdate"] != "available_commands_update":
            raise failure
    with AcpClient([sys.executable, str(FAKE)], env={"GEMINI_HOME": str(agent)}, on_update=failed_observer) as client:
        client.initialize()
        client.call("session/new", {"cwd": str(agent), "mcpServers": []})
        with pytest.raises(type(failure)) as caught:
            client.call("session/prompt", {"sessionId": "google-session-1", "prompt": [{"type": "text", "text": "hello"}]})
        assert caught.value is failure
        with pytest.raises(type(failure)):
            client.call("session/new", {})


def test_ordinary_request_never_waits_for_interactive_login(agent):
    with AcpClient([sys.executable, str(FAKE)], env={"GEMINI_HOME": str(agent), "FAKE_LOGIN": "1"}) as client:
        client.initialize()
        with pytest.raises(AuthenticationRequired):
            client.call("authenticate", {"methodId": "oauth-personal"})


@pytest.mark.parametrize("authenticated", [False, True])
def test_account_monitor_records_native_login_instead_of_runtime_availability(agent, monkeypatch, authenticated):
    from management_portal.execution import account_registrations
    from management_portal.execution.account_adapters import AntigravityAccounts
    from management_portal.execution.account_monitor import AccountMonitor
    from management_portal.execution.accounts import ExecutionAccounts
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS

    monkeypatch.setattr(account_registrations, "registrations", lambda provider: [{
        "account_id": "antigravity_one", "route_id": "one", "kind": "subscription_login",
        "config": {}, "max_concurrency": 4}])
    monkeypatch.setattr(accounts, "environment", lambda account: {
        "GEMINI_HOME": str(agent), **({} if authenticated else {"FAKE_LOGIN": "1"})})
    adapter = AntigravityAccounts()
    assert AGENT_PROVIDERS["antigravity"].account_adapter == "AntigravityAccounts"
    collection = ExecutionAccounts((adapter,))
    store = mock.Mock()
    monitor = AccountMonitor(collection, mock.Mock(), usage_store=mock.Mock(), login_state_store=store)
    monitor.refresh_account(collection.get("antigravity_one"))
    call = store.observe.call_args
    assert call.args == ("antigravity_one", "logged_in" if authenticated else "logged_out")
    assert call.kwargs["observed_at"].tzinfo is not None


def test_account_observation_failure_does_not_invent_login_state(monkeypatch):
    from management_portal.execution.account_adapters import AntigravityAccounts
    adapter = object.__new__(AntigravityAccounts)
    monkeypatch.setattr(accounts, "observe_account", mock.Mock(side_effect=ValueError("status unavailable")))
    with pytest.raises(ValueError, match="status unavailable"):
        adapter.observe(SimpleNamespace(account_id="antigravity_one"))


def test_account_quota_uses_native_command_without_model_tools_or_resident_history(agent):
    from datetime import datetime, timezone
    sample = accounts.observe_account("antigravity_one")
    assert sample["login_observation"] == "logged_in"
    assert sample["status"] == "ok"
    assert [w["remaining_percent"] for w in sample["rate_limits"]] == [73, 91]
    assert sample["rate_limits"][0]["resets_at"] == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc).timestamp()
    assert sample["rate_limits"][0]["label"] == "Gemini Models · Five Hour Limit"
    calls = requests(agent)
    assert [c["method"] for c in calls] == ["initialize", "authenticate", "session/new", "session/prompt"]
    assert calls[2]["params"]["mcpServers"] == []
    assert calls[2]["params"]["_meta"]["antigravity"]["enabledTools"] == []
    assert calls[3]["params"]["prompt"] == [{"type": "text", "text": "/usage"}]


@pytest.mark.parametrize("setting,code,prompted", [
    ("FAKE_USAGE_FAIL", "quota_unavailable", True), ("FAKE_NO_USAGE", "quota_unsupported", False)])
def test_quota_failure_retains_auth_without_exposing_errors_or_falling_back_to_llm(agent, monkeypatch, setting, code, prompted):
    monkeypatch.setattr(accounts, "environment", lambda _: {"GEMINI_HOME": str(agent), setting: "1"})
    sample = accounts.observe_account("antigravity_one")
    assert sample["login_observation"] == "logged_in"
    assert sample["error_code"] == code
    assert sample["rate_limits"] == []
    assert "secret" not in json.dumps(sample)
    assert any(c["method"] == "session/prompt" for c in requests(agent)) is prompted


def test_quota_parser_does_not_invent_percentages_for_unknown_units_disabled_or_malformed_data():
    report = ("### Quota Limits\n- **Gemini**\n"
              "  - **Daily:** 0% remaining (resets invalid)\n"
              "  - **Weekly:** 100% remaining\n"
              "  - **Unused:** Disabled\n"
              "  - **Absolute:** 1,200 units remaining\n"
              "  - **Broken:** 101% remaining\n"
              "### Token Usage\n  - **Tokens:** 90% remaining\n")
    windows = accounts.quota_windows(report, 123)
    assert [w["remaining_percent"] for w in windows] == [0, 100, None, None]
    assert all(w["resets_at"] is None and w["observed_at"] == 123 for w in windows)
    assert accounts.quota_windows("### Quota Limits\n- Unable to retrieve quota status (secret)", 1) == []


def test_login_diagnostics_only_expose_known_failure_categories():
    from management_portal.antigravity_client import login_failure_reason, LOGIN_FAILURES
    from management_portal.execution.account_login import LoginOutputParser
    reason = login_failure_reason("Onboarding failed: User is ineligible for free-tier. access_token=secret")
    assert reason == "account_ineligible"
    parser = LoginOutputParser("antigravity")
    parser.feed("ANTIGRAVITY_LOGIN_ERROR:" + reason + "\n")
    assert parser.error == LOGIN_FAILURES[reason]
    assert "secret" not in parser.error
    parser.feed("ANTIGRAVITY_LOGIN_ERROR:secret-token\n")
    assert parser.error == ""
    assert login_failure_reason("access_token=secret") == ""


def test_callback_relay_accepts_only_its_google_loopback_and_state():
    auth = "https://accounts.google.com/o/oauth2/auth?state=right&redirect_uri=http%3A%2F%2Flocalhost%3A23456%2F"
    with mock.patch.object(accounts, "build_opener") as opener:
        accounts.complete_loopback_login(auth, "http://localhost:23456/?state=right&code=secret")
        assert opener.return_value.open.call_count == 1
        for invalid in ["http://localhost:23457/?state=right&code=x", "http://localhost:23456/?state=wrong&code=x",
                        "https://example.com/?state=right&code=x", "http://localhost:23456/other?state=right"]:
            with pytest.raises(ValueError):
                accounts.complete_loopback_login(auth, invalid)
        assert opener.return_value.open.call_count == 1


def test_account_environment_and_login_parser_are_isolated(monkeypatch, tmp_path):
    from management_portal.execution import account_login as login
    monkeypatch.setattr(login, "account_home", lambda p, a: tmp_path / a)
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-leak")
    env = login.local_login_environment("antigravity", "antigravity_one")
    assert env["GEMINI_HOME"] == str(tmp_path / "antigravity_one")
    assert "GEMINI_API_KEY" not in env and "CLAUDE_CONFIG_DIR" not in env
    parser = login.LoginOutputParser("antigravity")
    _, url = parser.feed("https://accounts.google.com/o/oauth2/auth?state=123\n")
    assert url.endswith("state=123")
    assert login._session_result({"provider": "antigravity"}, {"state": "pending"})["requires_code_input"]


def test_installer_verifies_archive_before_activation(monkeypatch, tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        bundle.writestr("agy_acp_server.par", b"test")
    raw = data.getvalue()
    item = {"archive": "https://dl.google.com/test.zip", "cmd": "./agy_acp_server.par", "sha256": hashlib.sha256(raw).hexdigest()}
    monkeypatch.setattr(runtime, "manifest", lambda: {"version": "1.4.5", "distribution": {"binary": {"linux-x86_64": item}}})
    monkeypatch.setattr(runtime, "platform_key", lambda: "linux-x86_64")
    monkeypatch.setattr(runtime.updates, "RUNTIMES", tmp_path)
    monkeypatch.setattr(runtime, "urlopen", lambda *a, **k: io.BytesIO(raw))
    with mock.patch("management_portal.antigravity_client.AcpClient") as client:
        client.return_value.__enter__.return_value.initialize.return_value = {"agentInfo": {"version": "1.4.5"}}
        root = runtime.AntigravityRuntime().stage(("1.4.5",))
        assert (root / ".portal-validated").is_file()
    (root / ".portal-validated").unlink()
    item["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        runtime.AntigravityRuntime().stage(("1.4.5",))
    assert not (root / ".portal-validated").exists()


def test_installer_rejects_archive_path_escape(monkeypatch, tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        bundle.writestr("../outside", b"untrusted")
    raw = data.getvalue()
    item = {"archive": "https://dl.google.com/test.zip", "cmd": "./agy_acp_server.par", "sha256": hashlib.sha256(raw).hexdigest()}
    monkeypatch.setattr(runtime, "manifest", lambda: {"version": "1.4.5", "distribution": {"binary": {"linux-x86_64": item}}})
    monkeypatch.setattr(runtime, "platform_key", lambda: "linux-x86_64")
    monkeypatch.setattr(runtime.updates, "RUNTIMES", tmp_path)
    monkeypatch.setattr(runtime, "urlopen", lambda *a, **k: io.BytesIO(raw))
    with pytest.raises(ValueError, match="Unsafe path"):
        runtime.AntigravityRuntime().stage(("1.4.5",))
    assert not (tmp_path / "outside").exists()
