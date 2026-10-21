"""User credentials travel only to explicitly trusted Portal entrances.

:module: security.identity-access
:importance: P1
"""
import json
import stat
import sys
from unittest.mock import Mock

import pytest

from scripts import portal_user as cli


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    original = {"url": "https://portal.example", "token": "dbu_test_secret", "status": "active", "username": "sample_editor"}
    path.write_text(json.dumps(original))
    path.chmod(0o600)
    request = Mock(return_value={"username": "sample_editor"})
    monkeypatch.setattr(cli, "request", request)

    def run(url, *command):
        monkeypatch.setattr(sys, "argv", ["portal_user.py", "--url", url, "--credentials", str(path), *command])
        cli.main()

    return path, original, request, run


def test_cli_trusts_alias_without_sending_or_replacing_token(client, capsys):
    """信任别名地址时不发送也不替换令牌，之后经别名或原地址都可调用

    :design: decision_6eac174d85bf"""
    path, original, request, run = client
    alias = "https://10.77.0.1"
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        run(alias, "call", "GetUser")
    request.assert_not_called()
    assert path.read_bytes() == before
    run(alias, "trust-url")
    run(alias + ":443/", "trust-url")
    request.assert_not_called()
    saved = json.loads(path.read_text())
    assert saved == {**original, "trusted_urls": [original["url"], alias]}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert original["token"] not in capsys.readouterr().out
    for base in [alias, original["url"]]:
        run(base, "call", "GetUser")
        assert request.call_args.args[0] == base
        assert request.call_args.args[-1] == original["token"]


def test_cli_removing_all_entrances_does_not_restore_legacy_url(client):
    """移除全部信任地址后不退回旧 URL，重新信任后才能调用

    :design: decision_6eac174d85bf"""
    path, original, request, run = client
    run(original["url"], "untrust-url")
    assert json.loads(path.read_text())["trusted_urls"] == []
    with pytest.raises(SystemExit):
        run(original["url"], "call", "GetUser")
    request.assert_not_called()
    run(original["url"], "trust-url")
    run(original["url"], "call", "GetUser")
    request.assert_called_once()


@pytest.mark.parametrize("url", ["http://10.77.0.1", "https://other.example/path", "https://u:p@other.example", "https://other.example?q=1", "https://other.example#fragment", "https://", "https://other.example:bad", "https://other.example\n", "https://other.example:65536"])
def test_cli_rejects_unsafe_alias_before_writing_or_network(client, url):
    """不安全的别名地址在写文件和联网之前被拒绝

    :design: decision_6eac174d85bf"""
    path, _, request, run = client
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        run(url, "trust-url")
    assert path.read_bytes() == before
    request.assert_not_called()


def test_cli_legacy_credentials_and_explicit_loopback_tunnel(client):
    """旧凭证文件按原地址调用时不被改写，显式信任的回环隧道地址可用

    :design: decision_6eac174d85bf"""
    path, original, request, run = client
    before = path.read_bytes()
    run("https://PORTAL.example:443/", "call", "GetUser")
    assert path.read_bytes() == before
    assert request.call_args.args[0] == original["url"]
    for alias in ["http://127.0.0.1:18084", "http://[::1]:18084"]:
        run(alias, "trust-url")
        run(alias, "call", "GetUser")
        assert request.call_args.args[0] == alias


def test_cli_refuses_alias_changes_for_insecure_credential_file(client):
    """凭证文件权限过宽时拒绝修改信任地址

    :design: decision_6eac174d85bf"""
    path, _, request, run = client
    path.chmod(0o644)
    with pytest.raises(SystemExit):
        run("https://10.77.0.1", "trust-url")
    request.assert_not_called()


def test_cli_join_records_endpoint_and_does_not_reregister_for_alias(client, monkeypatch):
    """join 记录登记地址，信任别名后不能经别名重复加入

    :design: decision_6eac174d85bf"""
    path, _, request, run = client
    path.unlink()
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "invitation-secret")
    request.return_value = {"username": "sample_editor", "credential": {"credential_id": "token-1"}}
    run("https://portal.example", "join")
    saved = json.loads(path.read_text())
    assert saved["trusted_urls"] == ["https://portal.example"]
    assert saved["status"] == "active"
    request.reset_mock()
    run("https://10.77.0.1", "trust-url")
    assert json.loads(path.read_text())["token"] == saved["token"]
    with pytest.raises(SystemExit):
        run("https://10.77.0.1", "join")
    request.assert_not_called()


def test_cli_discovery_shortcuts_and_join_report(client, monkeypatch, capsys):
    """用户 CLI 的 list 和 schema 映射到对应指令，join 报告令牌到期日和个人 AMA

    :module: platform.core
    :importance: P2
    """
    path, _, request, run = client
    run("https://portal.example", "list", "--category", "conversation")
    run("https://portal.example", "schema", "DeliverMessage")
    run("https://portal.example", "call", "MarkMailboxItemRead", "--arguments", '{"key": "resident:r"}', "--expected-version", "v7")
    sent = [call.args[2] for call in request.call_args_list]
    assert sent[0]["instruction"] == "ListInstructions" and sent[0]["arguments"] == {"category": "conversation"}
    assert sent[1]["instruction"] == "GetInstructionSchema" and sent[1]["arguments"] == {"instruction": "DeliverMessage"}
    assert sent[2]["expected_version"] == "v7" and "expected_version" not in sent[0]
    path.unlink()
    request.reset_mock()
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "invitation-secret")
    request.return_value = {"username": "sample_editor", "credential": {"credential_id": "t", "expires_at": 1800000000},
                            "personal_ama": {"lineage_id": "lineage_000000000001", "resident_id": "resident_000000000001"}}
    run("https://portal.example", "join", "--expires-in-days", "30")
    assert request.call_args.args[2]["expires_in_days"] == 30
    out = capsys.readouterr().out
    assert "Token expires at 2027-01-15" in out and "resident_000000000001" in out and "/guide/agent.md" in out


def test_cli_watch_prints_items_and_resumes_after_the_last_cursor(client, monkeypatch, capsys):
    """用户 CLI 的 watch 打印事件条目，并把最后游标作为 after_cursor 续读

    :module: platform.core
    :importance: P2
    """
    path, _, request, run = client
    bodies = []
    streams = iter([
        [b"event: batch\n", b'data: {"items": [{"cursor": 4, "kind": "message"}]}\n', b"\n"],
        [b"event: end\n", b"data: {}\n", b"\n"],
    ])

    class Response(list):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    opener = Mock()

    def open_stream(req, timeout):
        bodies.append(json.loads(req.data))
        assert req.headers["Accept"] == "text/event-stream"
        return Response(next(streams))

    opener.open.side_effect = open_stream
    monkeypatch.setattr(cli, "build_opener", lambda *handlers: opener)
    real_events = cli.events

    def events_then_reconnect(response):
        # The first stream times out after one batch; the client reconnects after its cursor.
        yield from real_events(response)
        if len(bodies) == 1:
            raise TimeoutError

    monkeypatch.setattr(cli, "events", events_then_reconnect)
    run("https://portal.example", "watch", "WatchMailbox", "--arguments", '{"timeout_seconds": 30}')
    assert '"cursor": 4' in capsys.readouterr().out
    request.assert_not_called()
    assert bodies[0]["arguments"] == {"timeout_seconds": 30}
    assert bodies[1]["arguments"] == {"timeout_seconds": 30, "after_cursor": 4}


def test_cli_guide_needs_no_credentials_and_sends_none(monkeypatch, capsys):
    """用户 CLI 读取 agent 指南不需要凭证，也不发送 Authorization

    :module: platform.core
    :importance: P2
    """
    opened = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return "# Portal agent guide\n".encode()

    opener = Mock()
    opener.open.side_effect = lambda req, timeout: opened.append(req) or Response()
    monkeypatch.setattr(cli, "build_opener", lambda *handlers: opener)
    monkeypatch.setattr(sys, "argv", ["portal_user.py", "--url", "https://portal.example", "guide"])
    cli.main()
    assert opened[0].full_url == "https://portal.example/guide/agent.md"
    assert "Authorization" not in opened[0].headers
    assert "# Portal agent guide" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["portal_user.py", "--url", "https://portal.example", "call", "GetUser"])
    with pytest.raises(SystemExit):
        cli.main()


def test_cli_keeps_tls_verification_and_refuses_redirects(monkeypatch):
    """用户 CLI 保持 TLS 校验并拒绝跟随重定向

    :design: decision_6eac174d85bf"""
    opener = Mock()
    opener.open.return_value.__enter__ = Mock(return_value=Mock(read=lambda: b'{}'))
    opener.open.return_value.__exit__ = Mock(return_value=False)
    build = Mock(return_value=opener)
    monkeypatch.setattr(cli, "build_opener", build)
    cli.request("https://10.77.0.1", "/api/instructions/execute", {}, "dbu_secret")
    handlers = build.call_args.args
    assert len(handlers) == 1 and isinstance(handlers[0], cli.NoRedirect)
    req = opener.open.call_args.args[0]
    assert req.get_header("Authorization") == "Bearer dbu_secret"
    assert handlers[0].redirect_request(req, None, 302, "Found", {}, "https://other.example") is None
