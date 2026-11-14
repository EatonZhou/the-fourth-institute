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
from management_portal.execution.provider_failures import ProviderOutcomeUnknown
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
    monkeypatch.setattr(accounts, "account_runtime", lambda *args: tmp_path)
    monkeypatch.setattr(accounts, "environment", lambda account: {"GEMINI_HOME": str(tmp_path)})
    monkeypatch.setattr(backend, "resident_process_environment", lambda options: ({}, {}))
    return tmp_path


def invocation(root, text="hello", *, sid="", tools=(), media=(), owner=True, execution="execution-test"):
    return SimpleNamespace(
        request=SimpleNamespace(runtime_options={"provider_profile": "one", "model": "gemini-test"},
            operation="turn", execution_id=execution, cwd=str(root), provider="antigravity",
            permission_profile="owner" if owner else "restricted", input_media=tuple(media)),
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


def test_images_reach_acp_on_new_and_resumed_sessions(agent):
    from PIL import Image
    import base64
    from management_portal.execution.contracts import InputMedia
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    from management_portal.society_service import resident_media_domain as media_domain, resident_execution_provider
    from management_portal import resident_chat_projection

    source = agent / "diagram.png"
    Image.new("RGB", (7, 5), "blue").save(source)
    original = source.read_bytes()
    media = media_domain.resident_input_media(
        [{"media_type": "image", "source": str(source)}], actor="test",
        resident_id="resident_111111111111", content="", workspace_roots=(agent,))
    staged = media_domain.stage_resident_input_images(media, agent / "staged", "msg-image")
    source.unlink()  # accepted media must survive deletion of the composer copy
    image = InputMedia.from_payload(staged[0])
    record = {"backend": "antigravity", "provider_profile": "one"}
    text = media_domain.provider_message_with_native_media(record, f"Read [image: {source}]", staged)
    assert str(source) not in text
    backend = AntigravityBackend()
    first = backend.execute(invocation(agent, text, media=[image], owner=False), context()[0])
    second = backend.execute(invocation(agent, "next", sid=first.backend_session_id, media=[image],
                                        owner=False, execution="execution-next"), context()[0])
    digest = hashlib.sha256(original).hexdigest()
    assert first.output["message"] == text + ":image:image/png:" + digest
    assert second.output["message"] == "next:image:image/png:" + digest
    prompts = [r["params"]["prompt"] for r in requests(agent) if r["method"] == "session/prompt"]
    assert len(prompts) == 2
    for prompt in prompts:
        assert len(prompt) == 2
        assert prompt[1]["mimeType"] == "image/png"
        assert base64.b64decode(prompt[1]["data"]) == original
    assert AGENT_PROVIDERS["antigravity"].media
    capabilities = resident_execution_provider.capability_projection(record)
    assert capabilities["image_input"] is True
    projected = resident_chat_projection.project_session({"resident_id": "resident_111111111111",
        "resident": record, "backend_capabilities": capabilities, "allows": {"DeliverMessage": True}}, "test")
    assert projected["chat_workspace"]["composer"]["can_attach"] is True


@pytest.mark.parametrize("suffix,body", [(".txt", b"attachment verification: 7149"),
                                          (".csv", b"name,value\nverification,7149\n")])
def test_file_attachments_reach_acp_and_are_read_through_offered_tools(agent, suffix, body):
    from management_portal.society_service import resident_media_domain as media_domain
    path = agent / ("attachment" + suffix)
    path.write_bytes(body)
    media = media_domain.resident_input_media(
        [{"media_type": "file", "source": str(path)}], actor="test",
        resident_id="resident_111111111111", content="Read this file", workspace_roots=(agent,))
    text = media_domain.resident_message_with_file_references("Read this file", media)
    seen = []
    def read_file(args):
        assert Path(args["path"]) == path
        seen.append(args["path"])
        return path.read_bytes().decode("utf-8")
    tool = HarnessTool("read_file", "Read the authorized attachment", {"type": "object"}, read_file)
    result = AntigravityBackend().execute(invocation(agent, text, tools=[tool], owner=False), context()[0])
    assert seen == [str(path)]
    assert result.output["message"] == text + ":" + body.decode("utf-8")
    prompt = next(r["params"]["prompt"] for r in requests(agent) if r["method"] == "session/prompt")
    assert prompt == [{"type": "text", "text": text}]
    # Plain user text is never reinterpreted as an authorized embedded resource.
    assert all(block["type"] != "resource_link" for block in prompt)


def test_runtime_without_images_rejects_before_authentication_or_prompt(agent, monkeypatch):
    from PIL import Image
    from management_portal.execution.contracts import InputMedia
    path = agent / "input.png"
    Image.new("RGB", (1, 1)).save(path)
    monkeypatch.setattr(accounts, "environment", lambda account: {"GEMINI_HOME": str(agent), "FAKE_NO_IMAGES": "1"})
    with pytest.raises(ValueError, match="does not support image"):
        AntigravityBackend().execute(invocation(agent, media=[InputMedia("image", "local_path", str(path))]), context()[0])
    assert [r["method"] for r in requests(agent)] == ["initialize"]


def test_catalog_reads_snapshots_without_starting_acp(agent, monkeypatch):
    cache = agent / "catalog"
    cache.mkdir()
    monkeypatch.setattr(accounts, "account_runtime", lambda *args: cache)
    (cache / "catalog.json").write_text(json.dumps({"observed_at": time.time(), "capabilities": {"media": False}}))
    (cache / "catalog.json").chmod(0o600)
    assert accounts.catalog("one") == {}
    assert not (agent / "requests.jsonl").exists()
    native = {"models": {"currentModelId": "text", "availableModels": [{"modelId": "text"}]}}
    accounts.save_catalog("antigravity_one", native, {"promptCapabilities": {"image": False}})
    assert accounts.catalog("one")["capabilities"]["media"] is False
    assert not (agent / "requests.jsonl").exists()


def test_input_media_validation_never_silently_drops_images(agent, monkeypatch):
    from PIL import Image
    from management_portal import antigravity_input_media as media
    from management_portal.execution.contracts import InputMedia
    path = agent / "input.png"
    Image.new("RGB", (1, 1)).save(path)
    local = InputMedia("image", "local_path", str(path), "image/png")
    with pytest.raises(ValueError, match="staged locally"):
        media.prompt_content("", [InputMedia("image", "url", "https://example.com/image.png")])
    with pytest.raises(ValueError, match="at most 20"):
        media.prompt_content("", [local] * 21)
    with pytest.raises(ValueError, match="matching MIME"):
        media.prompt_content("", [InputMedia("image", "local_path", str(path), "image/jpeg")])
    with monkeypatch.context() as m:
        m.setattr(media, "MAX_IMAGE_BYTES", 8)
        with pytest.raises(ValueError, match="20 MiB"):
            media.prompt_content("", [local])
    with monkeypatch.context() as m:
        m.setattr(media, "MAX_TURN_IMAGE_BYTES", path.stat().st_size)
        with pytest.raises(ValueError, match="40 MiB"):
            media.prompt_content("", [local, local])
    path.write_text("not an image")
    with pytest.raises(ValueError, match="valid image"):
        AntigravityBackend().execute(invocation(agent, media=[local]), context()[0])
    assert not (agent / "requests.jsonl").exists()
    path.unlink()
    with pytest.raises(ValueError, match="readable file"):
        media.prompt_content("", [local])


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"),
                                        ("WEBP", "image/webp")])
def test_supported_image_formats_preserve_bytes(agent, format, mime):
    import base64
    from PIL import Image
    from management_portal.antigravity_input_media import prompt_content
    from management_portal.execution.contracts import InputMedia
    path = agent / "image.bin"
    Image.new("RGB", (2, 3), "green").save(path, format=format)
    block = prompt_content("look", [InputMedia("image", "local_path", str(path), mime)])[1]
    assert block["mimeType"] == mime
    assert base64.b64decode(block["data"]) == path.read_bytes()


def test_static_gif_is_converted_and_animation_is_rejected(agent):
    import base64
    from PIL import Image
    from management_portal.antigravity_input_media import prompt_content
    from management_portal.execution.contracts import InputMedia
    path = agent / "image.gif"
    frame = Image.new("RGB", (2, 3), "green")
    frame.save(path)
    media = InputMedia("image", "local_path", str(path), "image/gif")
    block = prompt_content("look", [media])[1]
    assert block["mimeType"] == "image/png"
    with Image.open(io.BytesIO(base64.b64decode(block["data"]))) as converted:
        assert converted.convert("RGB").tobytes() == frame.tobytes()
    frame.save(path, save_all=True, append_images=[Image.new("RGB", (2, 3), "red")])
    with pytest.raises(ValueError, match="animated GIF"):
        prompt_content("look", [media])


def test_turn_serves_portal_tools_and_reports_native_events_and_usage(agent):
    seen = []
    tool = HarnessTool("read_resource", "Read resource", {"type": "object"}, lambda args: seen.append(args) or "resource text")
    inv = invocation(agent, tools=[tool])
    ctx, events, state = context()
    result = AntigravityBackend().execute(inv, ctx)
    assert result.output["message"] == "hello:resource text"
    assert result.backend_session_id == "google-session-1"
    assert result.usage == {
        "last": {"inputTokens": 80, "outputTokens": 6, "totalTokens": 86},
        "context": {"inputTokens": 80},
        "modelContextWindow": 1000,
    }
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


def test_release_keeps_provider_history_but_a_cleared_binding_starts_a_new_session(agent):
    """释放不发远端删除；Portal 清除绑定后下一输入使用 session/new 而非旧会话 load。"""
    backend = AntigravityBackend()
    first = backend.execute(invocation(agent), context()[0])
    before = requests(agent)
    release = invocation(agent, sid=first.backend_session_id, execution='execution-release')
    release.request.operation = 'release'
    backend.execute(release, context()[0])
    assert requests(agent) == before, 'release must not contact Google or delete its stored history'
    backend.execute(invocation(agent, 'new context', sid='', execution='execution-after-clear'), context()[0])
    rows = requests(agent)
    assert len([r for r in rows if r['method'] == 'session/new']) == 2
    assert not [r for r in rows if r['method'] == 'session/load']


def test_tool_progress_is_separate_from_final_answer(agent):
    ctx, events, _ = context()
    result = AntigravityBackend().execute(invocation(agent, "multi-step"), ctx)
    assert result.output["message"] == "Final answer."
    messages = [e["params"]["item"] for e in events if e["method"] == "item/completed"
                and e["params"]["item"]["type"] == "agentMessage"]
    assert [(m["text"], m["phase"]) for m in messages] == [
        ("Checking a file.", "commentary"), ("Final answer.", "final_answer")]
    assert messages[0]["id"] != messages[1]["id"]


@pytest.mark.parametrize("prompt", ["parallel-tool-error", "parallel-tool-correction", "parallel-tool-empty-result"])
def test_parallel_error_flush_preserves_actual_tool_result_and_immutable_segments(agent, prompt):
    from tests.cases.live_trace_support import _turn
    from management_portal.execution.agent_data_output_capture import live_tool_segment_id

    store, projector, request, _ = _turn("antigravity")
    ctx, events, state = context()

    def observe(provider, sid, route, event):
        events.append(event)
        projector.observe(provider=provider, backend_route_id=route, backend_session_id=sid,
                          execution_id=request.execution_id, event=event)

    ctx._provider_event_observer = observe
    result = AntigravityBackend().execute(invocation(agent, prompt, sid="thread-native",
                                                    execution=request.execution_id), ctx)
    assert result.output["message"] == prompt
    assert state["phase"] == "completed"
    for call in ("parallel-error", "parallel-command"):
        for method in ("item/started", "item/completed"):
            assert sum(e["method"] == method and e["params"].get("item", {}).get("id") == call for e in events) == 1
    command = store.segment(live_tool_segment_id(request.execution_id, "parallel-command"))
    failure = store.segment(live_tool_segment_id(request.execution_id, "parallel-error"))
    assert command["status"] == "completed"
    assert command["content"]["result"]["aggregatedOutput"] == ("" if prompt == "parallel-tool-empty-result" else "parallel-ok")
    assert failure["status"] == "failed"
    assert "missing address" in str(failure["content"]["result"])
    assert events[-1]["method"] == "turn/completed"


@pytest.mark.parametrize("owner,outcome", [(True, "selected"), (False, "cancelled")])
def test_permissions_are_enforced_in_protocol_not_prompt(agent, owner, outcome):
    result = AntigravityBackend().execute(invocation(agent, "permission", owner=owner), context()[0])
    assert result.output["message"] == outcome
    meta = next(r["params"]["_meta"]["antigravity"] for r in requests(agent) if r["method"] == "session/new")
    assert ("enabledTools" not in meta) if owner else meta["enabledTools"] == []


@pytest.mark.parametrize("sid", ["", "existing-session"])
@pytest.mark.parametrize("prompt,allowed", [
    ("permission-mcp", True),
    ("permission-mcp-other", False),
    ("permission-mcp-unoffered", False),
    ("permission-mcp-title-only", False),
    ("permission-mcp-wrong-session", False),
])
def test_portal_only_permissions_allow_offered_mcp_dispatch(agent, sid, prompt, allowed):
    seen = []
    tool = HarnessTool("read_resource", "Read resource", {"type": "object"},
                       lambda args: seen.append(args) or "resource text")
    result = AntigravityBackend().execute(invocation(agent, prompt, owner=False, sid=sid, tools=[tool]), context()[0])
    assert result.output["message"] == ("selected:resource text" if allowed else "cancelled")
    assert seen == ([{"id": "resource-one"}] if allowed else [])


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
    with pytest.raises(ProviderOutcomeUnknown) as failure:
        AntigravityBackend().execute(inv, ctx)
    assert failure.value.error_code == "provider_outcome_unknown"
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


@pytest.mark.parametrize('empty_load', [False, True])
def test_quota_reuses_its_native_session_and_updates_catalog(agent, monkeypatch, empty_load):
    if empty_load:
        monkeypatch.setattr(accounts, 'environment', lambda _: {'GEMINI_HOME': str(agent), 'FAKE_EMPTY_LOAD': '1'})
    assert accounts.observe_account("antigravity_one")["status"] == "ok"
    assert accounts.observe_account("antigravity_one")["status"] == "ok"
    calls = requests(agent)
    assert len([c for c in calls if c['method'] == 'session/new']) == 1
    assert len([c for c in calls if c['method'] == 'session/load']) == 1
    assert accounts.catalog('one')['models'] == ['gemini-test']


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


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_installer_verifies_archive_before_activation(monkeypatch, tmp_path, platform):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        bundle.writestr("agy_acp_server.par", b"test")
    raw = data.getvalue()
    item = {"archive": "https://dl.google.com/test.zip", "cmd": "./agy_acp_server.par", "sha256": hashlib.sha256(raw).hexdigest()}
    monkeypatch.setattr(runtime, "manifest", lambda: {"version": "1.4.5", "distribution": {"binary": {"linux-x86_64": item}}})
    monkeypatch.setattr(runtime, "platform_key", lambda: "linux-x86_64")
    monkeypatch.setattr(runtime.updates, "RUNTIMES", tmp_path)
    monkeypatch.setattr(runtime, "urlopen", lambda *a, **k: io.BytesIO(raw))
    monkeypatch.setattr(runtime.sys, "platform", platform)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("USERPROFILE", "operator-home-must-not-be-used")
    monkeypatch.setenv("APPDATA", "operator-appdata-must-not-be-used")
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-leak")
    with mock.patch("management_portal.antigravity_client.AcpClient") as client:
        client.return_value.__enter__.return_value.initialize.return_value = {"agentInfo": {"version": "1.4.5"}}
        root = runtime.AntigravityRuntime().stage(("1.4.5",))
        assert (root / ".portal-validated").is_file()
        env = client.call_args.kwargs["env"]
        assert env["HOME"] == env["GEMINI_HOME"]
        assert "GEMINI_API_KEY" not in env
        if platform == "win32":
            assert env["SystemRoot"] == r"C:\Windows"
            assert env["USERPROFILE"] == env["APPDATA"] == env["LOCALAPPDATA"] == env["GEMINI_HOME"]
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


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _step_blob(*, uncached, cached, output, thinking, response):
    usage = (
        b"\x10" + _varint(uncached)
        + b"\x18" + _varint(output)
        + b"\x28" + _varint(cached)
        + b"\x48" + _varint(thinking)
        + b"\x50" + _varint(response)
    )
    chat_model = b"\x22" + _varint(len(usage)) + usage
    return b"\x0a" + _varint(len(chat_model)) + chat_model


def test_multi_step_turn_separates_turn_sum_from_context_window_usage(agent):
    import sqlite3
    from management_portal.society_service.resident_domain_common import execution_token_usage_contract

    conv_dir = agent / "antigravity-acp" / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    db_path = conv_dir / "google-session-1.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB, size INTEGER)")
        # Prior turn step (idx=0) must not be summed into the new turn.
        conn.execute("INSERT INTO gen_metadata VALUES (0, ?, 0)",
                     (_step_blob(uncached=5000, cached=10000, output=200, thinking=50, response=150),))
        conn.commit()

    ctx, events, state = context()
    # Simulate the Go harness writing two new steps (idx=1, idx=2) during the turn before usage_update.
    orig_observe = ctx.observe_context_usage
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO gen_metadata VALUES (1, ?, 0)",
                     (_step_blob(uncached=2000, cached=15000, output=300, thinking=100, response=200),))
        conn.execute("INSERT INTO gen_metadata VALUES (2, ?, 0)",
                     (_step_blob(uncached=2277, cached=150489, output=2958, thinking=2385, response=573),))
        conn.commit()

    # Pass start_idx=0 by intercepting _read_steps on the initial pre-prompt call only.
    from management_portal.execution import antigravity_backend as backend_mod
    real_read = backend_mod._read_steps
    calls = []
    defread = lambda env, sid, after_idx=-1: (calls.append(after_idx), (0, []) if len(calls) == 1 else real_read(env, sid, after_idx))[1]
    with mock.patch.object(backend_mod, "_read_steps", side_effect=defread):
        result = AntigravityBackend().execute(invocation(agent, sid="google-session-1"), ctx)

    assert result.usage == {
        "last": {
            "inputTokens": 169766,
            "cachedInputTokens": 165489,
            "outputTokens": 3258,
            "reasoningOutputTokens": 2485,
            "totalTokens": 173024,
        },
        "context": {
            "inputTokens": 152766,
            "cachedInputTokens": 150489,
            "outputTokens": 2958,
            "reasoningOutputTokens": 2385,
            "totalTokens": 155724,
        },
        "modelContextWindow": 1000,
    }
    assert state["context"] == {
        "input_tokens": 152766,
        "cached_input_tokens": 150489,
        "output_tokens": 2958,
        "reasoning_output_tokens": 2385,
        "total_tokens": 155724,
    }
    assert state["window"] == 1000
    contract = execution_token_usage_contract(result.usage)
    assert contract["turn_usage"]["total_tokens"] == 173024
    assert contract["context_usage"]["input_tokens"] == 152766
    assert contract["context_usage"]["total_tokens"] == 155724
    assert contract["model_context_window"] == 1000
