"""Command-line client of the HATG ISA for Residents (scripts/resident_hatg_instruction.py)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from scripts import resident_hatg_instruction as cli


RESIDENT = "resident_aaaaaaaaaaaa"


@pytest.fixture
def clock(monkeypatch):
    state = {"now": 0.0, "sleeps": []}

    def sleep(seconds):
        state["sleeps"].append(seconds)
        state["now"] += seconds

    monkeypatch.setattr(cli.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(cli.time, "sleep", sleep)
    monkeypatch.setattr(cli, "_token", lambda resident_id: "token-for-tests")
    monkeypatch.delenv("PORTAL_RESIDENT_ID", raising=False)
    return state


def failure(status: int, code: str = "") -> urllib.error.HTTPError:
    body = {"status": "failed", "error": {"code": code, "message": "test"}} if code else {"error": "temporary"}
    return urllib.error.HTTPError("http://portal.test/api/instructions/execute", status, "x", {},
                                  io.BytesIO(json.dumps(body).encode()))


def committed(result: dict | None = None) -> io.BytesIO:
    return io.BytesIO(json.dumps({"status": "committed", "replayed": False, "result": result or {}}).encode())


def test_call_sends_one_v2_envelope_with_one_key_and_retries_transient_failures(monkeypatch, clock, capsys):
    """HATG 命令行 call 以同一幂等键发送 v2 指令信封，临时失败按指数退避重试"""
    requests = []
    failures = [failure(503), urllib.error.URLError(ConnectionRefusedError()),
                failure(409, "instruction.in_progress"), failure(503, "instruction.projection_pending")]

    def send(request, timeout):
        requests.append(json.loads(request.data))
        assert request.headers["Authorization"] == "Bearer token-for-tests"
        assert request.full_url.endswith("/api/instructions/execute")
        if failures:
            raise failures.pop(0)
        return committed({"message_id": "msg_1"})

    monkeypatch.setattr(cli, "urlopen", send)
    code = cli.main(["--resident-id", RESIDENT, "call", "DeliverMessage", "--arguments-json",
                     json.dumps({"resident_id": RESIDENT, "message": {"content_parts": [{"type": "text", "text": "hi"}]}}),
                     "--expected-version", '{"lineage_aaaaaaaaaaaa": 3}'])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["result"] == {"message_id": "msg_1"}
    assert len(requests) == 5 and all(request == requests[0] for request in requests)
    body = requests[0]
    assert body["schema"] == "tfi.hatg-instruction.v2"
    assert body["instruction"] == "DeliverMessage"
    assert body["idempotency_key"].startswith("cli:")
    assert body["expected_version"] == {"lineage_aaaaaaaaaaaa": 3}
    assert clock["sleeps"] == [1.0, 2.0, 4.0, 8.0]


@pytest.mark.parametrize("status,code", [
    (400, "instruction.invalid_arguments"), (403, "instruction.forbidden"), (404, "instruction.not_found"),
    (409, "instruction.version_conflict"), (409, "instruction.idempotency_conflict"), (401, ""),
])
def test_terminal_errors_are_printed_once_without_retry(monkeypatch, clock, capsys, status, code):
    """HATG 命令行遇到参数、权限、版本冲突等终止性错误时只输出一次，不重试"""
    calls = []

    def send(request, timeout):
        calls.append(request)
        raise failure(status, code)

    monkeypatch.setattr(cli, "urlopen", send)
    assert cli.main(["--resident-id", RESIDENT, "call", "GetLineage", "--arguments-json", '{"lineage_id":"lineage_x"}']) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["http_status"] == status
    if code:
        assert output["error"]["code"] == code
    assert len(calls) == 1 and clock["sleeps"] == []


def test_retries_stop_at_the_retry_window(monkeypatch, clock, capsys):
    """HATG 命令行持续收到 503 时在重试时间窗内停止重试"""
    monkeypatch.setattr(cli, "urlopen", lambda request, timeout: (_ for _ in ()).throw(failure(503)))
    assert cli.main(["--resident-id", RESIDENT, "call", "ListTriggerPaths", "--arguments-json", '{"lineage_id":"l"}']) == 1
    assert sum(clock["sleeps"]) < cli.RETRY_SECONDS


def test_an_explicit_key_is_kept(monkeypatch, clock):
    """HATG 命令行使用调用方显式给出的幂等键"""
    sent = []
    monkeypatch.setattr(cli, "urlopen", lambda request, timeout: sent.append(json.loads(request.data)) or committed())
    cli.main(["--resident-id", RESIDENT, "call", "PauseComponent", "--arguments-json",
              '{"lineage_id":"l","component_id":"c","paused":true}', "--idempotency-key", "mine"])
    assert sent[0]["idempotency_key"] == "mine"


def test_list_and_schema_use_the_self_description_instructions(monkeypatch, clock):
    """命令行 list 调用 ListInstructions，schema 调用 GetInstructionSchema"""
    sent = []
    monkeypatch.setattr(cli, "urlopen", lambda request, timeout: sent.append(json.loads(request.data)) or committed())
    cli.main(["--resident-id", RESIDENT, "list", "--category", "lineage_run"])
    cli.main(["--resident-id", RESIDENT, "schema", "WatchTriggerPath"])
    assert [(body["instruction"], body["arguments"]) for body in sent] == [
        ("ListInstructions", {"category": "lineage_run"}), ("GetInstructionSchema", {"instruction": "WatchTriggerPath"}),
    ]


def test_the_injected_identity_wins_and_a_conflicting_flag_is_refused(monkeypatch, clock):
    """HATG 命令行以注入的 PORTAL_RESIDENT_ID 为准，冲突的 --resident-id 被拒绝"""
    monkeypatch.setenv("PORTAL_RESIDENT_ID", RESIDENT)
    assert cli._resident_id("") == RESIDENT
    assert cli._resident_id(RESIDENT) == RESIDENT
    with pytest.raises(RuntimeError, match="不一致"):
        cli._resident_id("resident_bbbbbbbbbbbb")
    monkeypatch.delenv("PORTAL_RESIDENT_ID")
    with pytest.raises(RuntimeError):
        cli._resident_id("portal")


def test_watch_prints_items_and_resumes_after_the_last_cursor(monkeypatch, clock, capsys):
    """HATG 命令行 watch 逐条输出事件，连接断开后从最后一个 cursor 之后续读"""
    streams = [
        [b"event: batch\n", b'data: {"items": [{"cursor": 1, "type": "a"}]}\n', b"\n"],
        [b"event: batch\n", b'data: {"items": [{"cursor": 2, "type": "b"}]}\n', b"\n", b"event: end\n", b"data: {}\n", b"\n"],
    ]
    bodies = []

    class Response(list):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def send(request, timeout):
        bodies.append(json.loads(request.data))
        assert request.headers["Accept"] == "text/event-stream"
        lines = streams.pop(0)
        if not streams:
            return Response(lines)
        return Response(lines)  # the connection then drops

    monkeypatch.setattr(cli, "urlopen", send)
    assert cli.main(["--resident-id", RESIDENT, "watch", "WatchTriggerPath", "--arguments-json",
                     '{"lineage_id":"l","trigger_path_id":"p"}']) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["cursor"] for item in lines] == [1, 2]
    assert "after_cursor" not in bodies[0]["arguments"]
    assert bodies[1]["arguments"]["after_cursor"] == 1


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
