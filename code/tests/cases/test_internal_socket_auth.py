import json
import socketserver
import threading
import time

import pytest

from management_common.rpc_auth import RpcAuthenticator, request_auth, sign_rpc_auth, rpc_signing_key_file
from management_portal.execution.transport import ExecutionServiceClient
from management_portal.hatg import socket_rpc
from management_portal.hatg.contracts import InstructionContext
from management_portal.society_service.transport import SocietyServiceClient


def test_authenticator_startup_and_requests_do_not_open_database_pools(tmp_path, monkeypatch):
    """RPC 签名校验的启动和请求都不连接数据库，签名密钥只由接收方生成"""
    import psycopg
    from management_common.postgres_database import PostgresDatabase

    def unexpected(*args, **kwargs):
        raise AssertionError("RPC authentication must not open a database")

    # Restore database functions before the isolated database fixture cleans up.
    with monkeypatch.context() as scoped:
        scoped.setattr(psycopg, "connect", unexpected)
        scoped.setattr(PostgresDatabase, "_connection_pool", unexpected)
        scoped.delenv("PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE", raising=False)
        path = tmp_path / "service.sock"
        key_path = rpc_signing_key_file(socket_path=path)
        assert not key_path.exists()
        with pytest.raises(RuntimeError, match="signing key is unavailable"):
            request_auth("health", {}, socket_path=path)
        assert not key_path.exists()  # Clients never invent a receiving service's key.
        receiver = RpcAuthenticator(path)
        assert key_path.stat().st_mode & 0o777 == 0o600
        assert len(key_path.read_bytes()) >= 32
        for _ in range(100):
            auth = request_auth("health", {}, socket_path=path)
            assert receiver.authenticate("health", {}, auth) == ""


def test_service_auth_rejects_unsigned_tampered_expired_and_replayed_calls(tmp_path):
    """服务 RPC 校验拒绝未签名、被篡改、过期和重放的调用"""
    receiver = RpcAuthenticator(tmp_path / "service.sock")
    params = {"context": {"caller": "alice"}, "call": {"instruction": "GetUser"}}
    auth = sign_rpc_auth(receiver.key, "instruction.execute", params, actor="alice")
    with pytest.raises(PermissionError):
        receiver.authenticate("instruction.execute", params, None)
    with pytest.raises(PermissionError):
        receiver.authenticate("instruction.execute", params, {**auth, "actor": "bob"})
    with pytest.raises(PermissionError):
        receiver.authenticate("instruction.execute", {**params, "context": {"caller": "bob"}}, auth)
    with pytest.raises(PermissionError):
        receiver.authenticate("domain.call", params, auth)
    expired = sign_rpc_auth(receiver.key, "instruction.execute", params, actor="alice", issued_at=int(time.time()) - 60)
    with pytest.raises(PermissionError):
        receiver.authenticate("instruction.execute", params, expired)
    assert receiver.authenticate("instruction.execute", params, auth) == "alice"
    with pytest.raises(PermissionError, match="replayed"):
        receiver.authenticate("instruction.execute", params, auth)
    fresh = sign_rpc_auth(receiver.key, "instruction.execute", params, actor="alice")
    assert receiver.authenticate("instruction.execute", params, fresh) == "alice"


@pytest.mark.parametrize("owner", ["lineage", "execution", "web", "resource"])
def test_instruction_clients_sign_for_each_owner_socket(tmp_path, monkeypatch, owner):
    """ISA 客户端用每个 owner socket 自己的密钥签名"""
    path = tmp_path / owner / "service.sock"
    receiver = RpcAuthenticator(path)
    monkeypatch.setattr(socket_rpc, "owner_socket", lambda selected: path)
    context = InstructionContext(caller="alice", entry="web")
    params = {"context": context.to_payload(), "recovery_release": {"key": "run", "instruction_ids": []}}
    auth = socket_rpc._signed(owner, params, context)["auth"]
    assert receiver.authenticate(socket_rpc.METHOD, params, auth) == "alice"


@pytest.mark.parametrize("client_type", [SocietyServiceClient, ExecutionServiceClient])
def test_domain_clients_send_signed_requests_to_a_verifying_socket(tmp_path, client_type):
    """Lineage 与 Execution 客户端向校验签名的 socket 发送已签名请求"""
    path = tmp_path / "service.sock"
    receiver = RpcAuthenticator(path)
    observed = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request = json.loads(self.rfile.readline())
            actor = receiver.authenticate(request["method"], request["params"], request.get("auth"))
            observed.append((request["method"], actor))
            self.wfile.write(json.dumps({"id": request["id"], "ok": True, "result": {"ok": True}}).encode() + b"\n")

    from management_common.service_endpoint import LocalServiceSocketMixin, service_endpoint
    from management_common.service_shutdown import DrainingStreamServer

    class Receiver(LocalServiceSocketMixin, DrainingStreamServer):
        def __init__(self):
            self.endpoint = service_endpoint(path)
            self.address_family = self.endpoint.family
            super().__init__(self.endpoint.address, Handler)

    with Receiver() as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert client_type(path).health() == {"ok": True}
            assert observed == [("health", "")]
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_execution_peer_request_uses_the_target_socket_key(tmp_path):
    """发往对端 Execution 服务的请求用目标 socket 的密钥签名"""
    primary = tmp_path / "dev" / "execution.sock"
    peer = tmp_path / "release" / "execution.sock"
    dev_auth = RpcAuthenticator(primary)
    release_auth = RpcAuthenticator(peer)
    signed = request_auth("health", {}, socket_path=peer)
    assert release_auth.authenticate("health", {}, signed) == ""
    with pytest.raises(PermissionError):
        dev_auth.authenticate("health", {}, signed)

@pytest.fixture(params=[(owner, long) for owner in ('lineage', 'execution', 'web') for long in (False, True)])
def receiving_socket(request, tmp_path, monkeypatch):
    from management_portal.hatg import routing
    from management_portal.hatg.contracts import InstructionResult
    from management_portal.society_service.server import _LineageRequestHandler
    from management_portal.execution.service import _ExecutionRequestHandler
    from management_portal.web_instruction_server import _Handler
    handlers = {'lineage': _LineageRequestHandler, 'execution': _ExecutionRequestHandler, 'web': _Handler}
    from management_common.service_endpoint import LocalServiceSocketMixin, service_endpoint
    from management_common.service_shutdown import DrainingStreamServer
    from management_common.service_socket import connect_service
    owner, long_path = request.param
    path = tmp_path / ('试用资料' * 12) / 'owner.sock' if long_path else tmp_path / 'owner.sock'
    class Receiver(LocalServiceSocketMixin, DrainingStreamServer):
        def __init__(self):
            self.endpoint = service_endpoint(path)
            self.address_family = self.endpoint.family
            super().__init__(self.endpoint.address, handlers[owner])
    seen = []
    def execute_local(call, context, *, owner, instruction_id=""):
        seen.append(context.caller)
        return InstructionResult(instruction_id=instruction_id or 'instruction_111111111111', instruction=call.instruction, status='committed', result={})
    monkeypatch.setattr(routing, 'execute_local', execute_local)
    with Receiver() as server:
        server.rpc_auth = RpcAuthenticator(path)
        def dispatch(method, params):
            seen.append(method)
            return {'ok': True}
        server.dispatch = dispatch
        server.serve_instruction = lambda params, request_id, write_line, verified_caller: socket_rpc.serve(params, request_id, write_line, owner=owner, verified_caller=verified_caller)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        def send(params, auth=None, method='instruction.execute'):
            connection, _ = connect_service(path, timeout=3, restart_wait=0)
            with connection:
                connection.sendall(json.dumps({'id':'test', 'method':method, 'params':params, 'auth':auth}).encode()+b'\n')
                with connection.makefile('rb') as stream:
                    return json.loads(stream.readline())
        try:
            yield owner, server.rpc_auth, seen, send
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_receivers_reject_unsigned_tampered_and_replayed_instructions(receiving_socket):
    """各 owner 接收端拒绝未签名、被篡改和重放的指令"""
    owner, verifier, seen, send = receiving_socket
    from management_portal.hatg.kernel import new_instruction_id
    original_id = new_instruction_id(owner)
    params = {'context': InstructionContext(caller='alice', entry='web').to_payload(), 'call': {'instruction':'GetUser', 'arguments':{'username':'alice'}}, 'instruction_id': original_id}
    assert not send(params)['ok']
    auth = sign_rpc_auth(verifier.key, socket_rpc.METHOD, params, actor='alice')
    tampered = {**params, 'context': InstructionContext(caller='bob', entry='web').to_payload()}
    assert not send(tampered, auth)['ok']
    assert not send({**params, 'instruction_id': new_instruction_id(owner)}, auth)['ok']
    assert seen == []
    response = send(params, auth)
    assert response['ok'] and response['result']['instruction_id'] == original_id
    assert seen == ['alice']
    assert not send(params, auth)['ok']
    assert seen == ['alice']


def test_receivers_bind_instruction_caller_to_signature(receiving_socket):
    """接收端要求指令的调用者与签名者一致"""
    owner, verifier, seen, send = receiving_socket
    params = {'context': InstructionContext(caller='bob', entry='web').to_payload(), 'call': {'instruction':'GetUser', 'arguments':{'username':'bob'}}}
    auth = sign_rpc_auth(verifier.key, socket_rpc.METHOD, params, actor='alice')
    assert send(params,auth)['error']['code'] == 'instruction.forbidden'
    assert seen == []


def test_domain_and_worker_control_requests_require_valid_signatures(receiving_socket):
    """domain 和工作进程控制请求也需要有效签名

    未签名的 domain 与工作进程控制请求被拒绝；Execution 请求中带 actor 时签名者须与之一致。
    """
    owner, verifier, seen, send = receiving_socket
    assert not send({},method='health')['ok']
    assert seen == []
    if owner == 'web':
        return
    if owner == 'execution':
        params = {'actor': 'bob'}
        auth=sign_rpc_auth(verifier.key,'health',params,actor='alice')
        assert not send(params,auth,method='health')['ok']
        assert seen == []
    auth=sign_rpc_auth(verifier.key,'health',{},actor='')
    assert send({},auth,method='health')['ok']
    assert seen == ['health']


@pytest.mark.parametrize('owner',['lineage','execution','web','resource'])
def test_instruction_dispatch_never_trusts_an_unverified_caller(owner):
    """指令分发不信任未经签名验证的调用者"""
    params={'context':InstructionContext(caller='alice',entry='web').to_payload(),'call':{'instruction':'GetUser','arguments':{'username':'alice'}}}
    replies=[]
    socket_rpc.serve(params,'test',replies.append,owner=owner)
    assert replies[-1]['error']['code'] == 'instruction.forbidden'
