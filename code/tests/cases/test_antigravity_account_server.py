"""Exercise the real Worker/host socket and native stdio across 12 sessions."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
import sys
import threading

import pytest

from management_common.local_ipc import connect_local, start_local_server
from management_portal.antigravity_account_server import AccountServer, FRAME_LIMIT
from management_portal.antigravity_client import AccountAcpClient, AcpError, AcpTransportError


NATIVE = """
import json, os, sys, threading
from urllib.request import Request, urlopen

lock = threading.Lock()
endpoints = {}
models = {'currentModelId': 'gemini-test', 'availableModels': [{'modelId': 'gemini-test'}]}

def send(**fields):
    with lock:
        print(json.dumps({'jsonrpc': '2.0', **fields}), flush=True)

def call_portal(sid, identifier):
    endpoint = endpoints[sid]
    headers = {item['name']: item['value'] for item in endpoint['headers']}
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'hatg_instruction', 'arguments': {'instruction': 'GetResident', 'arguments': {}}}}
    req = Request(endpoint['url'], data=json.dumps(body).encode(), headers=headers)
    with urlopen(req, timeout=6) as response:
        text = json.load(response)['result']['content'][0]['text']
    send(method='session/update', params={'sessionId': sid, 'update': {
        'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text}}})
    send(id=identifier, result={'stopReason': 'end_turn'})


sessions = 0
prompts = {}
for line in sys.stdin:
    message = json.loads(line)
    method, identifier = message.get('method'), message.get('id')
    params = message.get('params', {})
    sid = params.get('sessionId')
    if method == 'initialize':
        send(id=identifier, result={'protocolVersion': 1, 'agentInfo': {'version':'1.5.18'}, 'agentCapabilities': {'sessionCapabilities': {'close': {}}, '_meta': {'antigravity': {'systemPrompt': {'append': True}}},
            'loadSession': True, 'mcpCapabilities': {'http': True}}})
    elif method == 'session/load':
        if sid == 'missing':
            send(id=identifier, error={'code': -32002, 'message': 'missing'})
        else:
            if params.get('mcpServers'):
                endpoints[sid] = params['mcpServers'][0]
            send(id=identifier, result={'models': models, 'configOptions': [{'category':'model','id':'model','currentValue':'gemini-test'},{'category':'mode','id':'mode','currentValue':'default'}]})
    elif method == 'session/new':
        sessions += 1
        send(method='session/update', params={'sessionId': str(sessions), 'update': {
             'sessionUpdate': 'usage_update', 'size': 12000 + sessions}})
        if params.get('mcpServers'):
            endpoints[str(sessions)] = params['mcpServers'][0]
        if params.get('earlyCommands'):
            send(method='session/update', params={'sessionId': str(sessions), 'update': {
                'sessionUpdate': 'available_commands_update', 'availableCommands': [{'name': 'usage'}]}})
        send(id=identifier, result={'sessionId': str(sessions), 'pid': os.getpid(), 'models': models, 'configOptions': [{'category':'model','id':'model','currentValue':'gemini-test'},{'category':'mode','id':'mode','currentValue':'default'}]})
    elif method == 'session/prompt':
        if sid in endpoints:
            threading.Thread(target=call_portal, args=(sid, identifier), daemon=True).start()
            continue
        if params.get('crash'):
            sys.exit(7)
        if params.get('ignore_cancel'):
            prompts['stuck'] = sid
        prompts[sid] = identifier
        send(method='session/update', params={'sessionId': sid})
        if len(prompts) == 12:
            for s in prompts:
                send(method='session/request_permission', id='perm-' + s, params={'sessionId': s})
    elif method == 'session/cancel':
        if prompts.get('stuck') == sid:
            continue
        send(id=prompts.pop(sid), result={'stopReason': 'cancelled'})
    elif method is None:
        s = identifier.split('-')[1]
        assert message['result']['session'] == s
        if s != '1':
            send(id=prompts.pop(s), result={'stopReason': 'end_turn', 'sessionId': s})
    else:
        send(id=identifier, result={'protocolVersion': 1})
"""


async def with_host(tmp_path, exercise):
    # Local IPC itself chooses Unix sockets or an authenticated loopback endpoint.
    path = tmp_path / "host.sock"
    host = AccountServer([sys.executable, "-u", "-c", NATIVE], os.environ.copy(), tmp_path)
    async with await start_local_server(host.handle, path, limit=FRAME_LIMIT):
        try:
            await asyncio.wait_for(asyncio.to_thread(exercise, path), timeout=25)
        finally:
            await host.close()


def connect(path, **callbacks):
    connection = connect_local(path, timeout=4)
    connection.settimeout(None)
    return AccountAcpClient(connection, **callbacks)


def open_session(client):
    client.initialize()
    return client.call("session/new", {})


def test_one_process_twelve_workers(tmp_path):
    def exercise(path):
        all_attached = threading.Barrier(12)
        first_permission = threading.Event()

        def execute():
            events, windows, permissions = [], [], []
            def permit(method, params):
                permissions.append(params["sessionId"])
                if params["sessionId"] == "1":
                    first_permission.set()
                return {"session": params["sessionId"]}

            def update(params):
                if params.get("update"):
                    windows.append(params["update"]["size"])
                else:
                    events.append(params["sessionId"])

            with connect(path, on_update=update, on_request=permit) as client:
                session = open_session(client)
                sid = session["sessionId"]
                all_attached.wait(timeout=6)
                with pytest.raises(AcpError):
                    client.call("session/set_config_option", {"sessionId": "foreign"})
                if sid == "1":
                    def cancel():
                        if first_permission.wait(8):
                            client.notify("session/cancel", {"sessionId": sid})
                    thread = threading.Thread(target=cancel)
                    thread.start()
                outcome = client.call("session/prompt", {"sessionId": sid}, timeout=10)
                if sid == "1":
                    thread.join()
                assert outcome["stopReason"] == ("cancelled" if sid == "1" else "end_turn")
                assert events == permissions == [sid]
                assert windows == [12000 + int(sid)]
                return session
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: execute(), range(12)))
        assert len({row["pid"] for row in results}) == 1
        assert len({row["sessionId"] for row in results}) == 12
        with connect(path) as client:
            assert open_session(client)["pid"] == results[0]["pid"]
    asyncio.run(with_host(tmp_path, exercise))


def test_native_failure_detaches_workers_and_new_work_gets_fresh_process(tmp_path):
    def exercise(path):
        with connect(path) as a, connect(path) as b:
            one = open_session(a)
            two = open_session(b)
            assert one["pid"] == two["pid"]
            with pytest.raises(AcpTransportError):
                a.call("session/prompt", {"sessionId": one["sessionId"], "crash": True}, timeout=4)
            with pytest.raises(AcpTransportError):
                b.call("session/set_config_option", {"sessionId": two["sessionId"]}, timeout=4)
        with connect(path) as c:
            assert open_session(c)["pid"] != one["pid"]
    asyncio.run(with_host(tmp_path, exercise))


def test_session_load_reserved_and_subsequent_peers_reuse_process(tmp_path):
    def exercise(path):
        with connect(path) as a, connect(path) as b:
            one = open_session(a)
            b.initialize()
            with pytest.raises(AcpError):
                b.call("session/load", {"sessionId": one["sessionId"]})
            with pytest.raises(AcpError):
                b.call("session/load", {"sessionId": "missing"})
            with pytest.raises(AcpError):
                b.call("session/set_config_option", {"sessionId": "missing"})
            b.call("session/load", {"sessionId": "other"})
            a.call("session/set_config_option", {"sessionId": one["sessionId"]})
        with connect(path) as c:
            assert open_session(c)["pid"] == one["pid"]
    asyncio.run(with_host(tmp_path, exercise))

def test_disconnected_stuck_prompt_drains_without_interrupting_sibling(tmp_path, monkeypatch):
    from management_portal import antigravity_account_server
    monkeypatch.setattr(antigravity_account_server, "CANCEL_GRACE_SECONDS", 0.1)

    retiring = threading.Event()
    original_check = antigravity_account_server.NativeAccount.stop_if_abandoned
    def observe_retirement(native):
        if native.retiring:
            retiring.set()
        original_check(native)
    monkeypatch.setattr(antigravity_account_server.NativeAccount, "stop_if_abandoned", observe_retirement)

    def exercise(path):
        sent = threading.Event()
        with connect(path, on_update=lambda params: sent.set() if "update" not in params else None) as a, connect(path) as b, connect(path) as probe:
            one = open_session(a)
            two = open_session(b)
            probe.initialize()
            with ThreadPoolExecutor(max_workers=1) as pool:
                prompt = pool.submit(a.call, "session/prompt", {"sessionId": one["sessionId"], "ignore_cancel": True})
                assert sent.wait(4)
                a.close()
                with pytest.raises(AcpTransportError):
                    prompt.result(timeout=4)
            assert retiring.wait(4)
            # The disconnected caller still owns its native session.
            with pytest.raises(AcpError):
                probe.call("session/load", {"sessionId": one["sessionId"]})
            b.call("session/set_config_option", {"sessionId": two["sessionId"]})
        with connect(path) as fresh:
            assert open_session(fresh)["pid"] != one["pid"]
    asyncio.run(with_host(tmp_path, exercise))

def test_supervised_account_uses_private_socket_without_tcp_listener(tmp_path, monkeypatch):
    from management_common import host_instances
    from management_common.service_components import is_builtin
    from management_portal import service_runtime
    from management_portal.antigravity_account_host import socket_path
    from scripts.instance_service_definitions import dependencies
    from tests.cases.provider_service_test_support import provider_instance

    root, _ = provider_instance(monkeypatch, tmp_path)
    component = "antigravity-account@sample"
    instance = host_instances.add_service_component(root, component)
    command, _ = service_runtime.component_command(instance, component)
    assert command[1:] == ["-m", "management_portal.antigravity_account_server", "--account-id", "sample"]
    assert is_builtin(component) and not is_builtin("antigravity-account@../other")
    assert set(dependencies(instance, component)) <= {"postgres"}
    assert socket_path("sample") != socket_path("other")


def test_account_admits_more_than_twelve_sessions(tmp_path):
    def exercise(path):
        with ExitStack() as stack:
            clients = [stack.enter_context(connect(path)) for _ in range(14)]
            sessions = [open_session(client) for client in clients]
            assert len({row["pid"] for row in sessions}) == 1
            assert len({row["sessionId"] for row in sessions}) == 14
    asyncio.run(with_host(tmp_path, exercise))


def test_resident_tools_keep_execution_identity_across_concurrent_turns(tmp_path, monkeypatch):
    from management_portal import antigravity_accounts as accounts
    from management_portal.execution import antigravity_cli_backend as backend, harness_isa_tool as isa
    from tests.cases.test_antigravity import context, invocation

    monkeypatch.setattr(accounts, "account_for_route", lambda _: {"account_id": "one", "route_id": "one"})
    monkeypatch.setattr(accounts, "environment", lambda _: {"GEMINI_HOME": str(tmp_path)})
    monkeypatch.setattr(accounts, "account_runtime", lambda *_: tmp_path)
    both = threading.Barrier(2)

    def observe(caller, arguments, *, execution_id, call_id):
        assert arguments["instruction"] == "GetResident"
        both.wait(timeout=4)
        return {"caller": caller, "execution": execution_id}

    monkeypatch.setattr(isa, "run_instruction", observe)

    def exercise(path):
        monkeypatch.setattr(backend, "connect_account", lambda account_id, portal_only=False, **kw: connect(path, **kw))
        provider = backend.AntigravityCliBackend()
        def turn(suffix, number, session=""):
            caller = "resident_" + suffix * 12
            execution = f"execution-{suffix}-{number}"
            tool = isa.ResidentIsaTool().tool(caller, execution)
            request = invocation(tmp_path, "identity probe", sid=session, execution=execution, tools=[tool])
            request.request.metadata = {"resident_id": caller}
            result = provider.execute(request, context()[0])
            assert json.loads(result.output["message"]) == {"caller": caller, "execution": execution}
            return result.backend_session_id

        with ThreadPoolExecutor(2) as pool:
            first = list(pool.map(turn, "ab", (1, 1)))
            assert len(set(first)) == 2
            assert list(pool.map(turn, "ab", (2, 2), first)) == first
    asyncio.run(with_host(tmp_path, exercise))


def test_shared_google_environment_has_no_resident_caller(tmp_path, monkeypatch):
    from management_portal import antigravity_account_server as host, antigravity_accounts as accounts
    from management_portal import antigravity_runtime, resident_shell_environment
    from scripts import resident_hatg_instruction

    monkeypatch.setenv("PORTAL_RESIDENT_ID", "resident_aaaaaaaaaaaa")
    monkeypatch.setenv("PORTAL_LINEAGE_ID", "lineage_bbbbbbbbbbbb")
    monkeypatch.setenv("PORTAL_DATABASE_URL", "unused")
    monkeypatch.setattr(accounts, "account_for_route", lambda _: {
        "account_id": "one", "kind": "subscription_login", "config": {"account_dir": str(tmp_path)}})
    monkeypatch.setattr(accounts, "environment", lambda _: {"GEMINI_HOME": str(tmp_path)})
    monkeypatch.setattr(antigravity_runtime, "command", lambda env: [sys.executable])
    _, env, cwd = host.account_configuration("one")
    assert cwd == tmp_path and env["PORTAL_SHARED_ACP"] == "1"
    assert not {"PORTAL_RESIDENT_ID", "PORTAL_LINEAGE_ID", "PORTAL_DATABASE_URL"}.intersection(env)
    monkeypatch.setattr(accounts, "account_runtime", lambda *args: tmp_path / "runtime")
    args, _, agent_root = host.account_configuration("one", portal_only=True)
    assert args[-2:] == ["--agent", "portal-tools-only"]
    agent = agent_root / ".agents/agents/portal-tools-only.md"
    assert agent.is_file()
    modified = agent.stat().st_mtime_ns
    assert host.account_configuration("one", portal_only=True)[2] == agent_root
    assert agent.stat().st_mtime_ns == modified
    monkeypatch.setenv("PORTAL_SHARED_ACP", "1")
    with pytest.raises(RuntimeError, match="hatg_instruction"):
        resident_hatg_instruction._resident_id("resident_aaaaaaaaaaaa")


def test_cold_client_waits_for_concurrent_registration(monkeypatch, tmp_path):
    from management_portal import antigravity_account_host as host
    from scripts import portal_service_units

    attempts = []
    ready = object()
    def connect(path):
        if attempts:
            return ready
        raise FileNotFoundError
    def start(account_id, *, action):
        attempts.append((account_id, action))
        raise BlockingIOError
    monkeypatch.setattr(host, "socket_path", lambda *args: tmp_path / "account.sock")
    monkeypatch.setattr(host, "connect_local", connect)
    monkeypatch.setattr(host, "AccountAcpClient", lambda connection: connection)
    monkeypatch.setattr(portal_service_units, "antigravity_account_service", start)
    assert host.connect_account("one") is ready
    assert attempts == [("one", "start")]
