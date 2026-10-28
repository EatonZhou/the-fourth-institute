from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.codex_app_server_protocol import CodexTurnInterrupted
from management_portal.execution.backends import CodexBackend, _CODEX_COMPACTION_PROMPT


def _installed_native_binaries() -> list[str]:
    """The native app-server binaries shipped inside the installed Codex npm package."""
    launcher = shutil.which("codex")
    if not launcher:
        return []
    package = Path(launcher).resolve().parent.parent
    return [str(path) for path in package.glob("node_modules/@openai/codex-*/vendor/*/bin/codex") if path.is_file()]


NATIVE_BINARIES = sorted(set(filter(None, os.environ.get(
    "PORTAL_CODEX_NATIVE_TEST_BINARIES", "",
).split(os.pathsep)))) or _installed_native_binaries() or [""]
INTERRUPT_GUIDANCE = "The user interrupted the previous turn on purpose."


class OfflineResponsesServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, interrupt_during_tool: bool, *, auto_compaction: bool = False,
                 complete_immediately: bool = False) -> None:
        super().__init__(("127.0.0.1", 0), OfflineResponsesHandler)
        self.requests: list[dict] = []
        self.generation_started = threading.Event()
        self.release_generation = threading.Event()
        self.interrupt_during_tool = interrupt_during_tool
        self.auto_compaction = auto_compaction
        self.complete_immediately = complete_immediately
        self.events: list[dict] = []
        self.tool_started = threading.Event()

    def observe_event(self, event: dict) -> None:
        self.events.append(event)
        if event.get("method") == "item/started":
            item = event.get("params", {}).get("item", {})
            if item.get("type") == "commandExecution":
                self.tool_started.set()


class OfflineResponsesHandler(BaseHTTPRequestHandler):
    server: OfflineResponsesServer

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(request)
        request_number = len(self.server.requests)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        response_id = f"response-{self.server.server_port}-{request_number}"
        self.send_event("response.created", {"response": {"id": response_id}})
        if request_number == 1 and not self.server.complete_immediately:
            self.server.generation_started.set()
            if not self.server.interrupt_during_tool and not self.server.auto_compaction:
                self.server.release_generation.wait(30)
                return
            available = list(request.get("tools", []))
            function_names = []
            while available:
                tool = available.pop()
                available.extend(tool.get("tools", []))
                if tool.get("type") == "function":
                    function_names.append(tool.get("name"))
            tool_name = next(name for name in ("exec_command", "shell_command", "shell")
                             if name in function_names)
            arguments = {"cmd": "sleep 10", "yield_time_ms": 10000}
            if tool_name == "shell_command":
                arguments = {"command": "sleep 10", "timeout_ms": 10000}
            elif tool_name == "shell":
                arguments = {"command": ["sleep", "10"], "timeout_ms": 10000}
            if self.server.auto_compaction:
                arguments = (
                    {"cmd": "printf NATIVE_TOOL_DONE", "yield_time_ms": 1000}
                    if tool_name == "exec_command" else
                    {"command": "printf NATIVE_TOOL_DONE", "timeout_ms": 1000}
                    if tool_name == "shell_command" else
                    {"command": ["printf", "NATIVE_TOOL_DONE"], "timeout_ms": 1000}
                )
            item = {
                "id": "tool-sleep",
                "call_id": "tool-sleep",
                "type": "function_call",
                "name": tool_name,
                "arguments": json.dumps(arguments),
            }
        else:
            text = (
                "NATIVE_COMPACT_SUMMARY"
                if self.server.auto_compaction and request_number == 2
                else "NATIVE_TEST_DONE"
            )
            item = {
                "id": f"message-{self.server.server_port}-{request_number}",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": text}],
            }
        self.send_event("response.output_item.done", {"item": item})
        input_tokens = 100_000 if self.server.auto_compaction and request_number == 1 else 10
        self.send_event("response.completed", {
            "response": {
                "id": response_id,
                "status": "completed",
                "usage": {"input_tokens": input_tokens, "output_tokens": 2,
                          "total_tokens": input_tokens + 2},
            },
        })

    def send_event(self, event_type: str, payload: dict) -> None:
        data = json.dumps({"type": event_type, **payload})
        self.wfile.write(f"event: {event_type}\ndata: {data}\n\n".encode())
        self.wfile.flush()


@contextmanager
def native_test_session(binary_path: str, tmp_path: Path, model_server: OfflineResponsesServer,
                        config: dict, *, resume_thread_id: str = ""):
    if not binary_path:
        model_server.server_close()
        pytest.skip("Set PORTAL_CODEX_NATIVE_TEST_BINARIES for offline native tests")
    binary = Path(binary_path)
    assert binary.is_file()
    server_thread = threading.Thread(target=model_server.serve_forever, daemon=True)
    server_thread.start()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    codex_home = home / ".codex"
    codex_home.mkdir(exist_ok=True)
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.4"\nmodel_provider = "offline"\n'
        '[model_providers.offline]\nname = "Offline test"\n'
        f'base_url = "http://127.0.0.1:{model_server.server_port}"\n'
        'wire_api = "responses"\n'
        '[features]\napps = false\nplugins = false\nmemories = false\n',
        encoding="utf-8",
    )
    socket_path = tmp_path / "native.sock"
    socket_path.unlink(missing_ok=True)
    command = [str(binary)]
    if binary.name == "codex":
        command.append("app-server")
    command.extend(["--listen", f"unix://{socket_path}"])
    config.update({
        "web_search": "disabled",
        "features": {"apps": False, "plugins": False, "memories": False},
        "project_doc_max_bytes": 0,
    })
    client = CodexAppServerClient(socket_path, request_timeout_seconds=10)
    process = None
    try:
        with (tmp_path / "native.log").open("a") as log:
            process = subprocess.Popen(
                command, cwd=tmp_path,
                env={"HOME": str(home), "CODEX_HOME": str(codex_home), "PATH": os.defpath},
                stdout=log, stderr=log,
            )
            deadline = time.monotonic() + 30
            while not socket_path.exists():
                assert process.poll() is None, "Native app-server exited before startup"
                assert time.monotonic() < deadline, "Native app-server startup timed out"
                time.sleep(0.02)
            params = {
                "cwd": str(tmp_path), "config": config,
                "approvalPolicy": "never", "sandbox": "danger-full-access",
            }
            if resume_thread_id:
                started = client.request("thread/resume", {**params, "threadId": resume_thread_id})
            else:
                started = client.start_thread(params)
            yield client, started["thread"]["id"], codex_home
    finally:
        model_server.release_generation.set()
        client.close()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        model_server.shutdown()
        model_server.server_close()


@pytest.mark.parametrize("binary_path", NATIVE_BINARIES)
@pytest.mark.parametrize("managed_config", [False, True])
@pytest.mark.parametrize("interrupt_during_tool", [False, True])
def test_native_interrupt_attribution_during_compaction(
    binary_path: str, managed_config: bool, interrupt_during_tool: bool, tmp_path: Path,
) -> None:
    """原生轮次中断后压缩时，托管配置使用 Portal 压缩提示且不带原生中断说明"""
    model_server = OfflineResponsesServer(interrupt_during_tool)
    config = CodexBackend._thread_config({}) if managed_config else {}
    with native_test_session(binary_path, tmp_path, model_server, config) as (client, thread_id, codex_home):
        turn_ids: list[str] = []
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                client.run_turn, thread_id, "Inspect the current task state.",
                cwd=str(tmp_path), config=config, turn_started_callback=turn_ids.append,
                event_callback=model_server.observe_event,
            )
            try:
                assert model_server.generation_started.wait(10)
                if interrupt_during_tool:
                    assert model_server.tool_started.wait(10), "Native tool did not start"
                assert turn_ids
                client.interrupt_turn(thread_id, turn_ids[0])
                with pytest.raises(CodexTurnInterrupted):
                    pending.result(timeout=15)
            finally:
                model_server.release_generation.set()
                if not pending.done():
                    client.close()
        client.compact_thread(thread_id, timeout_seconds=10,
                              no_progress_timeout_seconds=5, status_poll_interval_seconds=0.01)
        model_inputs = json.dumps(model_server.requests[1:])
        assert model_server.requests[1:]
        assert (INTERRUPT_GUIDANCE in model_inputs) is not managed_config
        assert (_CODEX_COMPACTION_PROMPT in model_inputs) is managed_config
        assert ("next LLM" in model_inputs) is not managed_config
        if interrupt_during_tool:
            assert "aborted by user" in model_inputs
        rollouts = list(codex_home.glob(f"sessions/**/*{thread_id}.jsonl"))
        assert len(rollouts) == 1
        history = [json.loads(line) for line in rollouts[0].read_text().splitlines()]
        assert any(record.get("payload", {}).get("type") == "turn_aborted" for record in history)
        assert any(record.get("type") == "compacted" for record in history)
        assert (INTERRUPT_GUIDANCE in json.dumps(history)) is not managed_config


@pytest.mark.parametrize("binary_path", NATIVE_BINARIES)
def test_native_auto_compaction_keeps_one_turn_and_completed_tool(binary_path: str,
                                                                  tmp_path: Path) -> None:
    """原生自动压缩在同一轮内完成，保留已完成的工具结果且不产生中断"""
    from unittest import mock

    model_server = OfflineResponsesServer(False, auto_compaction=True)
    config = CodexBackend._thread_config({"config_overrides": {
        "model_auto_compact_token_limit": 50_000,
        "model_auto_compact_token_limit_scope": "total",
    }})
    with native_test_session(binary_path, tmp_path, model_server, config) as (client, thread_id, codex_home):
        turn_ids: list[str] = []
        with mock.patch.object(client.rpc, "request", wraps=client.rpc.request) as requests:
            result = client.run_turn(
                thread_id, "Complete the tool operation and report the result.",
                cwd=str(tmp_path), config=config, turn_started_callback=turn_ids.append,
                event_callback=model_server.observe_event,
            )
        methods = [call.args[0] for call in requests.call_args_list]
        assert "turn/interrupt" not in methods
        assert "thread/compact/start" not in methods
        assert methods.count("turn/start") == 1
        assert len(turn_ids) == 1
        assert result.turn_id == turn_ids[0]
        assert result.response == "NATIVE_TEST_DONE"
        assert len(model_server.requests) == 3
        assert _CODEX_COMPACTION_PROMPT in json.dumps(model_server.requests[1])
        assert "NATIVE_TOOL_DONE" in json.dumps(model_server.requests[1])
        assert "NATIVE_COMPACT_SUMMARY" in json.dumps(model_server.requests[2])
        model_inputs = json.dumps(model_server.requests)
        assert INTERRUPT_GUIDANCE not in model_inputs
        assert "aborted by user" not in model_inputs
        assert any(event.get("params", {}).get("item", {}).get("type") == "contextCompaction"
                   for event in model_server.events)
        assert any(event.get("params", {}).get("item", {}).get("exitCode") == 0
                   for event in model_server.events)
        rollouts = list(codex_home.glob(f"sessions/**/*{thread_id}.jsonl"))
        assert len(rollouts) == 1
        history = [json.loads(line) for line in rollouts[0].read_text().splitlines()]
        assert any(record.get("type") == "compacted" for record in history)
        assert not any(record.get("payload", {}).get("type") == "turn_aborted" for record in history)
