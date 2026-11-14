"""Application tools use business permissions without entering the ISA.

:module: resident.execution
:importance: P1
"""
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
import json
import uuid

import pytest

from management_portal import application_tools as apps
from management_portal.execution import resident_application_tools as transport
from management_portal.execution import resident_capability_assembly as assembly
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.hatg.declarations import FIXED, R
from management_portal.hatg.kernel import InstructionKernel, OwnerBinding


REF = "application:test.write"
DECL = apps.ApplicationToolDeclaration(REF, "ApplicationWrite", "Write a test object",
    parameters=(R("object_id"),), decides=FIXED, target="object_id", mutates=True)
CALL = {"schema": "tfi.application-tool-call.v1", "tool": REF,
        "arguments": {"object_id": "test_one"}}
CONTEXT = InstructionContext("alice", "web")


@pytest.fixture
def operation(monkeypatch):
    from management_common.portal_database import portal_database
    from management_portal.hatg.store import ExecutionStore
    writes, authorizations = [], []
    def handler(inv):
        writes.append(inv.caller)
        return {"object_id": inv.argument("object_id")}
    def authorize(declaration, arguments, context):
        authorizations.append(context.caller)
        if context.caller == "denied":
            raise PermissionError("business permission denied")
    kernel = InstructionKernel(OwnerBinding("web", handlers={DECL.name: handler},
        version_of=lambda inv, obj: 7), catalog={DECL.name: DECL}, authorize=authorize,
        store=ExecutionStore(portal_database(schema="app_test_" + uuid.uuid4().hex[:8])),
        maintenance_lookup=lambda name: DECL)
    monkeypatch.setattr(apps, "registry", lambda: ({REF: DECL}, {DECL.name: handler}, {}))
    monkeypatch.setattr(apps, "kernel", lambda: kernel)
    return kernel, writes, authorizations


def test_independent_kernel_reuses_authorization_idempotency_and_cas(operation):
    kernel, writes, authorized = operation
    payload = CALL | {"idempotency_key": "once", "expected_version": 7}
    first = apps.execute(payload, CONTEXT)
    replay = apps.execute(payload, CONTEXT)
    assert first.get("error") is None
    assert first["status"] == replay["status"] == "committed", (first, replay)
    assert replay["replayed"] and replay["call_id"] == first["call_id"]
    assert writes == ["alice"] and authorized == ["alice", "alice"]
    conflict = apps.execute(CALL | {"expected_version": 8}, CONTEXT)
    assert conflict["error"]["code"] == "instruction.version_conflict"
    denied = apps.execute(payload, InstructionContext("denied", "web"))
    assert denied["error"]["code"] == "instruction.forbidden"
    assert len(writes) == 1
    assert kernel.maintain()["pruned"] == 0


def test_arguments_and_caller_cannot_be_injected(operation):
    assert apps.execute(CALL | {"caller": "alice"}, CONTEXT)["status"] == "failed"
    assert apps.execute(CALL | {"arguments": {"caller": "alice"}}, CONTEXT)["status"] == "failed"
    assert operation[1] == []


def test_server_reads_own_current_registration_on_every_call(operation, monkeypatch):
    from management_portal.hatg import routing
    selected = []
    seen = []
    def settings(call, context):
        seen.append((call.arguments, context.caller))
        return SimpleNamespace(raise_for_error=lambda: {"settings": {"capability_config": {"tools": selected}}})
    monkeypatch.setattr(routing, "execute", settings)
    context = InstructionContext("resident_123456789abc", "harness")
    assert apps.execute(CALL, context)["error"]["code"] == "instruction.forbidden"
    selected.append({"ref": REF, "enabled": True})
    receipt = apps.execute(CALL, context)
    assert receipt.get("error") is None
    assert receipt["status"] == "committed", receipt
    selected[0]["enabled"] = False
    assert apps.execute(CALL, context)["error"]["code"] == "instruction.forbidden"
    assert all(args == {"resident_id": caller} for args, caller in seen)


def test_actual_catalog_and_permission_union():
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.permission_system.scheme.compiler import declared_decisions
    declarations = apps.registry()[0]
    assert len(declarations) == 14
    assert sum(d.ref.startswith("application:paper.") for d in declarations.values()) == 2
    assert sum(d.ref.startswith("application:pulse.") for d in declarations.values()) == 2
    assert sum(d.ref.startswith("application:telepus.") for d in declarations.values()) == 10
    isa = instruction_catalog()
    for declaration in declarations.values():
        assert declaration.name not in isa
        assert declared_decisions()[declaration.name] == declaration.decides
        assert apps.permission_declaration(declaration.name).elements == declaration.elements


@dataclass(frozen=True)
class Request:
    operation: str = "turn"
    provider: str = "codex"
    execution_id: str = "execution_test"
    metadata: dict = field(default_factory=lambda: {"resident_id": "resident_123456789abc"})
    runtime_options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Invocation:
    request: Request = field(default_factory=Request)
    harness_tools: tuple = ()
    recovery_checkpoint: dict = field(default_factory=dict)
    available_harness_tool_names: frozenset = frozenset()
    session: object = field(default_factory=lambda: SimpleNamespace(backend_session_id="", backend_route_id=""))
    harness_capabilities: frozenset = frozenset({"portal_tools"})


class Context:
    def __init__(self):
        self.snapshots, self.events = [], []
    def is_cancelled(self):
        return False
    def record_capability_assembly(self, snapshot):
        self.snapshots.append(snapshot)
    def emit(self, name, payload):
        self.events.append((name, payload))


def invocation(required=False, **changes):
    options = {"runtime_settings": {"capability_config": {
        "tools": [{"ref": REF, "enabled": True, "required": required}]}}}
    return Invocation(request=Request(runtime_options=options), **changes)


@pytest.fixture
def rpc(monkeypatch):
    calls = []
    def remote(method, params, *, caller):
        calls.append((method, params, caller))
        if method == "application.tools.list":
            return {"tools": [DECL.schema()]}
        return {"status": "committed", "result": {}}
    monkeypatch.setattr(transport, "rpc", remote)
    return calls


def test_default_does_not_discover_apps_and_legacy_builtin_keeps_default(monkeypatch):
    monkeypatch.setattr(transport, "rpc", lambda *a, **k: pytest.fail("unexpected discovery"))
    assert assembly.prepare(Invocation(), Context()).harness_tools == ()
    assert assembly._enabled({}, SimpleNamespace(name="builtin"))
    assert not assembly._enabled({}, SimpleNamespace(capability_ref=REF))


def test_materialization_and_call_revocation(rpc, monkeypatch):
    context = Context()
    result = assembly.prepare(invocation(), context)
    tool = result.harness_tools[0]
    assert (tool.name, tool.capability_ref) == (DECL.name, REF)
    monkeypatch.setattr(assembly, "current_selection", lambda *args: {"tools": [{"ref": REF}]})
    assert json.loads(tool.handler({"arguments": {"object_id": "one"}}))["status"] == "committed"
    assert rpc[-1][2] == "resident_123456789abc"
    monkeypatch.setattr(assembly, "current_selection", lambda *args: {})
    with pytest.raises(PermissionError):
        tool.handler({"object_id": "one"})
    assert context.snapshots[0]["application_dependencies"][REF]["name"] == DECL.name


def test_recovery_freezes_application_declarations(rpc, monkeypatch):
    context = Context()
    before = invocation()
    assembly.prepare(before, context)
    monkeypatch.setattr(transport, "rpc", lambda *a, **k: {"tools": [DECL.schema() | {"description": "changed"}]})
    with pytest.raises(ValueError, match="recovery"):
        assembly.prepare(replace(before, recovery_checkpoint={"capability_assembly": context.snapshots[0]}), Context())


def test_required_unsupported_or_missing_fails_before_model(rpc, monkeypatch):
    with pytest.raises(ValueError, match="unsupported"):
        assembly.prepare(invocation(True, harness_capabilities=frozenset()), Context())
    monkeypatch.setattr(transport, "rpc", lambda *a, **k: {"tools": []})
    with pytest.raises(ValueError, match="missing"):
        assembly.prepare(invocation(True), Context())
    context = Context()
    assert assembly.prepare(invocation(), context).harness_tools == ()
    assert any(o["observation"] == "missing" for o in context.snapshots[0]["observations"])


def test_fixed_session_requires_explicit_new_session_for_required(rpc):
    old = SimpleNamespace(backend_session_id="existing", backend_route_id="same")
    with pytest.raises(ValueError, match="new session"):
        assembly.prepare(invocation(True, session=old), Context())
    result = assembly.prepare(invocation(session=old), Context())
    assert result.session is old
    assert result.request.runtime_options["resident_assembly"]["tools"][0]["application"] == "pending_session"


def test_web_api_authentication_origin_and_schema(monkeypatch):
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Route
    from management_portal import asgi
    client = TestClient(Starlette(routes=[
        Route("/api/application-tools", asgi.application_tools_api, methods=["GET"]),
        Route("/api/application-tools/execute", asgi.application_tools_api, methods=["POST"])]))
    monkeypatch.setattr(asgi, "_instruction_principal", lambda request: ("", ""))
    assert client.get("/api/application-tools").status_code == 401
    monkeypatch.setattr(asgi, "_instruction_principal", lambda request: ("alice", "web"))
    monkeypatch.setattr(asgi, "_same_origin", lambda request: False)
    assert client.post("/api/application-tools/execute", json=CALL).status_code == 403
    monkeypatch.setattr(asgi, "_same_origin", lambda request: True)
    assert len(client.get("/api/application-tools").json()["tools"]) == 14
    result = client.post("/api/application-tools/execute", json={"schema": "bad"})
    assert result.status_code == 400
    assert result.json()["schema"] == "tfi.application-tool-result.v1"


def test_signed_rpc_uses_authenticated_actor_and_rejects_tampering(monkeypatch):
    import io
    from pathlib import Path
    from management_common.rpc_auth import RpcAuthenticator, sign_rpc_auth
    from management_portal.web_instruction_server import _Handler
    key = b"test-signing-key-32-bytes-long!!!!"
    authenticator = RpcAuthenticator(Path("unused"), key=key)
    seen = []
    monkeypatch.setattr(apps, "execute", lambda call, context: seen.append(context.caller) or {"status": "committed"})
    def request(params, signed_params=None):
        method = "application.tools.execute"
        auth = sign_rpc_auth(key, method, signed_params or params, actor="resident_123456789abc")
        handler = object.__new__(_Handler)
        handler.server = SimpleNamespace(rpc_auth=authenticator)
        handler.rfile = io.BytesIO(json.dumps({"id": "request1", "method": method,
            "params": params, "auth": auth}).encode() + b"\n")
        handler.wfile = io.BytesIO()
        handler.handle()
        return json.loads(handler.wfile.getvalue())
    params = {"call": CALL, "caller": "alice"}
    assert request(params)["ok"]
    assert seen == ["resident_123456789abc"]
    assert not request(params | {"execution_id": "tampered"}, signed_params=params)["ok"]
    assert seen == ["resident_123456789abc"]


def test_optional_catalog_failure_is_observed_and_required_failure_stops(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionError("unavailable")
    monkeypatch.setattr(transport, "rpc", unavailable)
    context = Context()
    assert assembly.prepare(invocation(), context).harness_tools == ()
    assert context.snapshots[0]["observations"][0]["reason"] == "application_catalog_unavailable"
    with pytest.raises(ConnectionError):
        assembly.prepare(invocation(True), Context())


def test_independent_catalog_does_not_open_isa(operation):
    kernel = operation[0]
    result = kernel.execute(InstructionCall("ListInstructions"), CONTEXT)
    assert result.error["code"] == "instruction.unknown"


def test_fingerprint_uses_injected_parameter_contract():
    from management_portal.hatg.store import fingerprint
    from management_portal.hatg.declarations import P
    declaration = replace(DECL, parameters=(R("object_id"), P("optional"), P("value", "any")))
    plain = InstructionCall(DECL.name, {"object_id": "test"})
    omitted = InstructionCall(DECL.name, {"object_id": "test", "optional": None})
    null_value = InstructionCall(DECL.name, {"object_id": "test", "value": None})
    assert fingerprint(plain, declaration) == fingerprint(omitted, declaration)
    assert fingerprint(plain, declaration) != fingerprint(null_value, declaration)


def test_model_tool_exposes_call_controls_without_mixing_business_arguments(rpc, monkeypatch):
    monkeypatch.setattr(transport, 'current_tool_call_id', lambda: 'same-model-call')
    tools, _, _ = transport.materialize([{'ref': REF}], caller=CONTEXT.caller, execution_id='exec_test')
    tool = tools[0]
    assert tool.input_schema['properties']['arguments'] == DECL.schema()['input_schema']
    tool.handler({'arguments': {'object_id': 'one'}, 'expected_version': 7})
    call = rpc[-1][1]['call']
    assert call == {'schema': 'tfi.application-tool-call.v1', 'tool': REF,
        'arguments': {'object_id': 'one'}, 'expected_version': 7,
        'idempotency_key': 'tool:same-model-call'}
    tool.handler({'arguments': {'object_id': 'one'}, 'idempotency_key': 'business-key'})
    assert rpc[-1][1]['call']['idempotency_key'] == 'business-key'
    with pytest.raises(ValueError):
        tool.handler({'arguments': {}, 'caller': 'someone_else'})


def test_web_maintenance_recovers_application_receipt_without_isa_registration(operation):
    from management_portal.hatg.kernel import now_iso
    original, writes, _ = operation
    receipt = apps.execute(CALL, CONTEXT)
    assert receipt['status'] == 'committed'
    original.store.finish(receipt['call_id'], status='failed', now=now_iso(),
        result={'object_id': 'test_one'}, error={'code': 'instruction.projection_pending',
        'message': 'retry projection', 'commit_state': 'committed',
        'details': {'projection_failures': [{'kind': 'test_projection'}]}})
    recovered = []
    maintenance = InstructionKernel(OwnerBinding('web', handlers={},
        recover_projections=lambda failures, inv: recovered.append((inv.name, failures))),
        store=original.store, catalog={})
    assert maintenance.maintain()['projections'] == 1
    assert recovered[0][0] == DECL.name
    assert original.store.get(receipt['call_id'])['status'] == 'committed'
    assert writes == ['alice']
