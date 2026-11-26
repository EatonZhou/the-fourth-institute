"""Studio stock scripts on the real interpreter and isolated prepared admission."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from management_portal.workbench_projections import studio, studio_credentials as subject
from management_portal.society_service import lineage_effect_script as scripts
from management_portal.society_service import lineage_primitives as values
from management_portal.society_service.lineage_program_effect_rules import (
    effect_script_inputs, prepare_call_arguments, validate_effect_argument,
)
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


RESIDENT = "resident_111111111111"
WORKBENCH = "workbench_111111111111"


def job(recipient=RESIDENT):
    return {"id": "a" * 32, "created_by": "studio-owner", "status": "submitting",
            "workbench_id": WORKBENCH, "resident_id": recipient,
            "lineage_id": "lineage_111111111111", "effect_id": "effect_111111111111",
            "event_source_id": "event_source_111111111111", "base_revision": 7,
            "request_prompt": "只修改本稿件；不发布。"}


def store(saved):
    return SimpleNamespace(job=lambda identity, actor: copy.deepcopy(saved),
                           get=lambda identity, actor: {"workbench_id": identity},
                           require_write=lambda app, actor: None)


def invocation(saved):
    payload = subject.submission_payload(saved, actor=saved["created_by"], store=store(saved))
    event = values.LineageEvent(id="event_studio", type="manual.triggered", payload={"data": payload})
    return values.EffectInvocation(id="invocation_studio", activation=event,
                                   arguments={"prompt": saved["request_prompt"]})


@pytest.mark.parametrize("flow", studio.FLOWS)
def test_generated_script_is_candidate_schema_valid_and_migration_is_whole_ast(flow):
    from management_portal.hatg.catalog import declaration
    prompt = studio.FLOWS[flow][1]
    replacement = subject.studio_script(prompt)
    analysis = scripts.analyze(replacement, declaration_for=declaration,
                               declared_endpoints=["target"], connected_endpoints=["target"],
                               endpoint_targets={"target": RESIDENT}, validate_argument=validate_effect_argument)
    assert analysis.diagnostics == []
    assert analysis.output_fields == {"resident_id", "message_id"}
    assert analysis.instructions == ["PrepareResidentInput", "CreateTemporaryCredential", "DeliverMessage"]
    for options in ({}, {"activation_policy": "enqueue_and_trigger"}):
        old = scripts.deliver_message_script(prompt, lend_temporary_credentials=True, **options)
        assert subject.migrate_studio_script(old) == replacement
        assert subject.migrate_studio_script("# installed\n" + old + "\n") == replacement
        assert subject.is_studio_script(old)
    assert subject.migrate_studio_script(replacement) == replacement
    assert subject.is_studio_script(replacement)


@pytest.mark.parametrize("edit", [
    lambda source: source + '\noutput message_id="custom"',
    lambda source: 'custom="extra"\n' + source,
    lambda source: source + ' context_policy="fresh"',
    lambda source: source.replace('${endpoint.target}', '${endpoint.other}'),
    lambda source: source.replace('${endpoint.target}', 'resident_111111111111'),
    lambda source: source.replace('${event.payload.data.temporary_credentials}', r'\${event.payload.data.temporary_credentials}'),
    lambda source: source.replace('${event.payload.data.temporary_credentials}', '${event.payload.data.other}'),
    lambda source: source.replace('enqueue_and_trigger', 'attach_to_next'),
    lambda source: source.replace('设计 Portal', '自定义 Portal'),
    lambda source: source + '\nDeliverMessage resident_id="${endpoint.target}" message="again"',
    lambda source: 'if [ -n "x" ]; then ' + source + '; fi',
    lambda source: source + ' unknown="constant"',
    lambda source: source + ' invalid:=not-json',
])
def test_near_stock_custom_scripts_are_preserved(edit):
    source = scripts.deliver_message_script(studio.FLOWS["generate"][1],
                                           lend_temporary_credentials=True, activation_policy="enqueue_and_trigger")
    source = edit(source)
    assert subject.migrate_studio_script(source) == source
    assert not subject.is_studio_script(source)


def test_payload_only_saved_job_scope_and_actual_caller():
    saved = job()
    payload = subject.submission_payload(saved, actor=saved["created_by"], store=store(saved))
    assert set(payload) == {"studio_request"}
    request = payload["studio_request"]
    assert request["workbench_id"] == WORKBENCH
    assert request["resident_id"] == RESIDENT
    assert "issuer" not in request and "actor" not in request and "temporary_credentials" not in request
    assert subject.submission_payload(saved, actor=saved["created_by"], store=store(saved)) == payload
    with pytest.raises(PermissionError):
        subject.submission_payload(saved, actor="somebody-else", store=store(saved))
    for field in subject.JOB_FIELDS:
        with pytest.raises(ValueError):
            subject.submission_payload({**saved, field: "forged"}, actor=saved["created_by"], store=store(saved))
    for changes in ({"status": "completed"}, {"message_id": "msg_already_delivered"},
                    {"workbench_id": "lineage_111111111111"}, {"resident_id": "user"}):
        changed = {**saved, **changes}
        with pytest.raises(ValueError):
            subject.submission_payload(changed, actor=changed["created_by"], store=store(changed))
    denied = store(saved)
    def no_write(*args):
        raise PermissionError("revoked")
    denied.require_write = no_write
    with pytest.raises(PermissionError, match="revoked"):
        subject.submission_payload(saved, actor=saved["created_by"], store=denied)


@pytest.mark.parametrize("changes", [{"schema": "other"}, {"job_id": ""}, {"message_id": ""},
                                    {"resident_id": "resident_222222222222"},
                                    {"workbench_id": "lineage_111111111111"}])
def test_bad_binding_fails_before_any_instruction(changes):
    saved = job()
    inputs = effect_script_inputs({"endpoints": {"target": RESIDENT}}, invocation(saved))
    inputs["event"]["payload"]["data"]["studio_request"].update(changes)
    outcome = scripts.Interpreter(scripts.parse(subject.studio_script(studio.FLOWS["generate"][1])),
                                  inputs=inputs, execute_call=lambda call: pytest.fail("must not call ISA")).run()
    assert outcome.status == "failed"


@pytest.fixture
def prepared_runtime(delivery_resident, monkeypatch):
    from management_portal import society_service
    from management_portal.permission_system.facts import Fact
    from management_portal.society_service import contracts, server, resident_conversation_stream
    from management_portal.society_service.transport import SocietyServiceClient
    from tests.cases.permission_test_support import enable
    recipient = delivery_resident["resident_id"]
    permissions = enable(monkeypatch)
    owner = "studio-owner"
    permissions.users(owner, category="intern")
    scope = {"project": "StudioPrivate", "lineage": "lineage_222222222222"}
    permissions.place(Fact("project", "StudioPrivate"),
                       Fact("lineage", "lineage_222222222222", {"project": "StudioPrivate"}),
                       Fact("resident", recipient), Fact("workbench", WORKBENCH, scope),
                       Fact("workbench", "workbench_222222222222", scope))
    permissions.grant(owner, recipient, "@message")
    permissions.grant(owner, WORKBENCH, "@contributor")
    assert not permissions.holds(recipient, "@contributor", WORKBENCH)
    assert not permissions.holds(owner, "@contributor", "workbench_222222222222")
    def dispatch(_client, method, params=None, **kwargs):
        assert method == "domain.call"
        operation = contracts.operation_contract(params["domain"], params["name"])
        handler, bound = server._bind_domain_call(params["domain"], params["name"], params["args"], params["kwargs"])
        return server._invoke_domain(operation, handler, bound)
    client = SocietyServiceClient()
    monkeypatch.setattr(SocietyServiceClient, "_request", dispatch)
    monkeypatch.setattr(society_service, "get_society_service", lambda: client)
    return SimpleNamespace(recipient=recipient, owner=owner, permissions=permissions,
                           lineage_id=delivery_resident["lineage_id"],
                           repository=resident_conversation_stream.get_repository())


def execute(runtime, flow, *, captured, invocation_value=None, interrupted=""):
    from tests.cases.isa_test_support import run
    invoke = invocation_value or invocation(job(runtime.recipient))
    script = scripts.parse(subject.studio_script(studio.FLOWS[flow][1]))
    def call(request):
        args = prepare_call_arguments(request.instruction, request.arguments, invoke)
        result = run(request.instruction, args, actor=runtime.owner, key="studio-" + request.position)
        captured.append((request.instruction, args, result))
        if request.instruction == "PrepareResidentInput" and not result.replayed:
            assert runtime.repository.message(runtime.recipient, args["message"]["message_id"]) is None
        if request.instruction == "CreateTemporaryCredential" and result.status == "committed":
            if runtime.repository.message(runtime.recipient, args["message_id"]) is None:
                assert not runtime.permissions.holds(runtime.recipient, "@contributor", WORKBENCH)
            assert "expires_in_seconds" not in args
        if request.instruction == interrupted:
            raise OSError("lost instruction response")
        return scripts.CallResult(result.status == "committed", result.result or {}, result.error or {})
    return scripts.Interpreter(script, inputs=effect_script_inputs({"endpoints": {"target": runtime.recipient}}, invoke),
                                execute_call=call).run()


@pytest.mark.parametrize("flow", studio.FLOWS)
def test_stock_flow_real_prepare_grant_deliver_and_task_end(prepared_runtime, flow):
    runtime = prepared_runtime
    calls = []
    outcome = execute(runtime, flow, captured=calls)
    assert outcome.status == "succeeded", [(n, r.error) for n, a, r in calls]
    assert [n for n, _, _ in calls] == ["PrepareResidentInput", "CreateTemporaryCredential", "DeliverMessage"]
    assert calls[0][1] == calls[2][1]
    grant = calls[1][2].result
    assert grant["issued_by"] == runtime.owner and grant["lifetime"] == "task"
    assert grant["expires_at"] is None and grant["state"] == "pending"
    assert calls[1][1]["resource_id"] == WORKBENCH and calls[1][1]["a"] == "@contributor"
    assert runtime.permissions.holds(runtime.recipient, "@contributor", WORKBENCH)
    assert not runtime.permissions.holds(runtime.recipient, "@contributor", "workbench_222222222222")
    assert set(outcome.outputs) == {"message_id", "resident_id"}
    mid = outcome.outputs["message_id"]
    accepted = runtime.repository.message(runtime.recipient, mid)
    assert accepted["author_id"] == runtime.owner
    text = accepted["content_parts"][0]["text"]
    assert text == studio.FLOWS[flow][1] + "\n\n" + job()["request_prompt"]
    runtime.repository.set_input_state(runtime.recipient, mid, "completed")
    assert not runtime.permissions.holds(runtime.recipient, "@contributor", WORKBENCH)


def test_interrupted_grant_resumes_same_task_and_rejects_foreign_issuer(prepared_runtime):
    from tests.cases.isa_test_support import run
    runtime = prepared_runtime
    first = []
    with pytest.raises(OSError):
        execute(runtime, "generate", captured=first, interrupted="CreateTemporaryCredential")
    receipt = first[-1][2].raise_for_error()
    args = first[0][1]
    assert runtime.repository.message(runtime.recipient, args["message"]["message_id"]) is None
    different = run("DeliverMessage", args, actor="another-user", key="foreign-release")
    assert different.status != "committed"
    calls = []
    outcome = execute(runtime, "generate", captured=calls)
    assert outcome.status == "succeeded", [(n, r.error) for n, a, r in calls]
    assert calls[1][2].result["credential_id"] == receipt["credential_id"] and calls[1][2].replayed
    reread = run("CreateTemporaryCredential", calls[1][1], actor=runtime.owner, key="read-original-task-grant").raise_for_error()
    assert reread["credential_id"] == receipt["credential_id"]
    assert reread["deadline_at"] == receipt["deadline_at"]
    assert [row["message_id"] for row in runtime.repository.input_states(runtime.recipient)] == [outcome.outputs["message_id"]]


def test_denied_grant_never_releases_input(prepared_runtime):
    runtime = prepared_runtime
    invoke = invocation(job(runtime.recipient))
    invoke.activation.payload["data"]["studio_request"]["workbench_id"] = "workbench_222222222222"
    calls = []
    outcome = execute(runtime, "generate", captured=calls, invocation_value=invoke)
    assert outcome.status == "failed"
    assert [n for n, _, _ in calls] == ["PrepareResidentInput", "CreateTemporaryCredential"]
    assert calls[-1][2].status != "committed"
    assert runtime.repository.input_states(runtime.recipient) == []
    assert not runtime.permissions.holds(runtime.recipient, "@contributor", "workbench_222222222222")


def test_lost_delivery_receipt_does_not_duplicate_or_regrant_after_end(prepared_runtime):
    from tests.cases.isa_test_support import run
    runtime = prepared_runtime
    calls = []
    with pytest.raises(OSError):
        execute(runtime, "optimize", captured=calls, interrupted="DeliverMessage")
    delivery = calls[-1][2].raise_for_error()
    assert runtime.permissions.holds(runtime.recipient, "@contributor", WORKBENCH)
    replay = []
    outcome = execute(runtime, "optimize", captured=replay)
    assert outcome.status == "succeeded"
    assert outcome.outputs["message_id"] == delivery["message_id"]
    assert all(result.replayed for _, _, result in replay)
    runtime.repository.set_input_state(runtime.recipient, delivery["message_id"], "completed")
    again = execute(runtime, "optimize", captured=[])
    assert again.outputs == outcome.outputs
    assert not runtime.permissions.holds(runtime.recipient, "@contributor", WORKBENCH)
    assert run("CreateTemporaryCredential", calls[1][1], actor=runtime.owner, key="cannot-renew-task").status != "committed"
    assert [row["message_id"] for row in runtime.repository.input_states(runtime.recipient)] == [delivery["message_id"]]


def test_cancel_prepared_input_leaves_no_runnable_task(prepared_runtime):
    from tests.cases.isa_test_support import run
    runtime = prepared_runtime
    calls = []
    with pytest.raises(OSError):
        execute(runtime, "reverse", captured=calls, interrupted="CreateTemporaryCredential")
    mid = calls[0][1]["message"]["message_id"]
    # CancelInput has its own target permission; message permission alone
    # does not grant control over another Resident's tasks.
    runtime.permissions.grant(runtime.owner, runtime.recipient, "@contributor")
    cancelled = run("CancelInput", {"resident_id": runtime.recipient, "message_id": mid}, actor=runtime.owner,
                    key="cancel-studio").raise_for_error()
    assert cancelled
    assert execute(runtime, "reverse", captured=[]).status == "failed"
    assert runtime.repository.message(runtime.recipient, mid) is None
    assert not runtime.permissions.holds(runtime.recipient, "@contributor", WORKBENCH)


def test_studio_installs_owned_scripts_in_real_graph_and_preserves_custom(future_creation_store, monkeypatch):
    from management_portal import isa_client, lineage_workflows
    from management_portal.society_service import resident_domain
    from tests.cases.isa_test_support import ok
    from tests.cases.test_resident_folder_creation_contract import _new_session_payload
    actor = "sample_admin"
    recipient = resident_domain.register_resident(_new_session_payload("Studio install"), actor)
    lineage_id, resident_id = recipient["lineage_id"], recipient["resident_id"]
    app = {"bindings": {"main": {"lineage_id": lineage_id, "residents": {"designer": resident_id}}}}
    writes = []
    def call(caller, instruction, arguments, **options):
        assert caller == actor and instruction in {"GetLineage", "ApplyLineageChanges"}
        if instruction == "ApplyLineageChanges":
            writes.append(copy.deepcopy(arguments))
        return ok(instruction, arguments, actor=caller, **options)
    monkeypatch.setattr(isa_client, "call", call)
    installed = studio.install(None, app, actor)
    initial_writes = len(writes)
    assert studio.install(None, app, actor) == installed
    assert len(writes) == initial_writes
    graph = lineage_workflows.lineage_graph(actor, lineage_id)
    for flow, binding in installed.items():
        component = next(c for c in graph["program"]["components"] if c["id"] == binding["effect_id"])
        assert component["config"]["script"] == subject.studio_script(studio.FLOWS[flow][1])
        route = next(w for w in graph["program"]["wires"] if w["target"] == binding["effect_id"])
        assert route["config"]["include_envelope"] is True
    # The installed binding ledger and IDs survive a complete stock upgrade.
    # The old route omitted include_envelope, whose former default was true.
    changes = []
    for flow, binding in installed.items():
        old = scripts.deliver_message_script(studio.FLOWS[flow][1], lend_temporary_credentials=True,
                                              activation_policy="enqueue_and_trigger")
        changes.append({"op": "update", "kind": "component", "id": binding["effect_id"],
                        "changes": {"config.script": old}})
        route = next(w for w in graph["program"]["wires"] if w["target"] == binding["effect_id"])
        changes.append({"op": "update", "kind": "wire", "id": route["id"],
                        "changes": {"config": {k: v for k, v in route["config"].items() if k != "include_envelope"}}})
    ok("ApplyLineageChanges", {"lineage_id": lineage_id, "operations": changes}, actor=actor)
    assert studio.install(None, app, actor) == installed
    migrated = lineage_workflows.lineage_graph(actor, lineage_id)
    for flow, binding in installed.items():
        assert next(c for c in migrated["program"]["components"] if c["id"] == binding["effect_id"])["config"]["script"] == subject.studio_script(studio.FLOWS[flow][1])
        assert next(w for w in migrated["program"]["wires"] if w["target"] == binding["effect_id"])["config"]["include_envelope"] is True
    after_upgrade = len(writes)
    assert studio.install(None, app, actor) == installed
    assert len(writes) == after_upgrade
    target = installed["generate"]["effect_id"]
    custom = scripts.deliver_message_script("A hand-edited task")
    ok("ApplyLineageChanges", {"lineage_id": lineage_id, "operations": [{
        "op": "update", "kind": "component", "id": target, "changes": {"config.script": custom},
    }]}, actor=actor)
    with pytest.raises(ValueError, match="自定义"):
        studio.install(None, app, actor)
    final = lineage_workflows.lineage_graph(actor, lineage_id)
    assert next(c for c in final["program"]["components"] if c["id"] == target)["config"]["script"] == custom


def test_resident_issuer_uses_own_authority_and_cannot_take_payload_actor(prepared_runtime):
    from management_portal.society_service import resident_domain
    from tests.cases.test_resident_folder_creation_contract import _new_session_payload
    runtime = prepared_runtime
    issuer = resident_domain.register_resident(_new_session_payload("Studio issuer"), "sample_admin")["resident_id"]
    runtime.permissions.grant(issuer, runtime.recipient, "@message")
    runtime.permissions.grant(issuer, WORKBENCH, "@contributor")
    runtime.owner = issuer
    saved = {**job(runtime.recipient), "created_by": issuer}
    invoke = invocation(saved)
    invoke.activation.payload["data"]["studio_request"]["actor"] = "sample_admin"
    calls = []
    result = execute(runtime, "reverse", captured=calls, invocation_value=invoke)
    assert result.status == "succeeded", [(n, r.error) for n, a, r in calls]
    assert calls[1][2].result["issued_by"] == issuer
    assert all("actor" not in arguments and "issuer" not in arguments for _, arguments, _ in calls)
    accepted = runtime.repository.message(runtime.recipient, result.outputs["message_id"])
    assert accepted["author_id"] == issuer


@pytest.mark.parametrize("flow", studio.FLOWS)
def test_generation_uses_saved_store_and_actual_trigger_event(prepared_runtime, monkeypatch, flow):
    from management_portal import isa_client
    from management_portal.permission_system.facts import Fact
    from management_portal.workbench_projections import generation_submission
    from management_portal.workbench_projections.store import Store
    from tests.cases.isa_test_support import ok
    runtime = prepared_runtime
    runtime.permissions.place(Fact("lineage", runtime.lineage_id, {"project": "StudioPrivate"}))
    runtime.permissions.grant(runtime.owner, runtime.lineage_id, "@contributor")
    calls = []
    def local_call(caller, name, arguments=None, **options):
        calls.append((caller, name, copy.deepcopy(arguments), options))
        return ok(name, arguments, actor=caller, **options)
    monkeypatch.setattr(isa_client, "call", local_call)
    binding = {"main": {"lineage_id": runtime.lineage_id, "residents": {"designer": runtime.recipient}}}
    workflows = studio.install(None, {"bindings": binding}, runtime.owner)
    saved_store = Store()
    app = saved_store.create(WORKBENCH, {"title": "Draft", "html": "<p>Before</p>"}, runtime.owner)
    candidate = {**job(runtime.recipient), **workflows[flow], "base_revision": app["revision"]}
    generation_submission.prepare(candidate)
    saved_store.job(candidate["id"], runtime.owner, candidate)
    generation_submission.advance(candidate, actor=runtime.owner, submit=True, store=saved_store)
    assert candidate["status"] == "running", candidate
    dispatched = [entry for entry in calls if entry[1] == "TriggerEvent"]
    assert len(dispatched) == 1 and dispatched[0][0] == runtime.owner
    request = dispatched[0][2]["payload"]["studio_request"]
    assert request["job_id"] == candidate["id"] and request["workbench_id"] == WORKBENCH
    assert "temporary_credentials" not in dispatched[0][2]
    assert candidate["message_id"] == request["message_id"]
    accepted = runtime.repository.message(runtime.recipient, candidate["message_id"])
    assert accepted["author_id"] == runtime.owner
    assert accepted["content_parts"][0]["text"] == studio.FLOWS[flow][1] + "\n\n" + candidate["request_prompt"]
    assert runtime.permissions.holds(runtime.recipient, "@contributor", WORKBENCH)
    saved_store.job(candidate["id"], runtime.owner, candidate)
    # Read-only recovery must retain the original event/input and need no Store
    # or new loan. It must not call TriggerEvent again.
    generation_submission.advance(candidate, actor=runtime.owner)
    assert candidate["status"] == "running", candidate
    assert len([entry for entry in calls if entry[1] == "TriggerEvent"]) == 1
    assert [row["message_id"] for row in runtime.repository.input_states(runtime.recipient)] == [request["message_id"]]


def test_generation_rejects_missing_store_foreign_actor_and_changed_receipt(monkeypatch):
    from management_portal.workbench_projections import generation_submission
    def no_dispatch(*args, **kwargs):
        pytest.fail("rejected binding must not dispatch")
    monkeypatch.setattr(generation_submission.WorkflowBindings, "trigger", no_dispatch)
    candidate = job()
    generation_submission.prepare(candidate)
    generation_submission.advance(candidate, actor=candidate["created_by"], submit=True)
    assert candidate["status"] == "submitting" and "Store" in candidate["error"]
    generation_submission.advance(candidate, actor="forged-user", submit=True, store=store(job()))
    assert "原发起人" in candidate["error"]
    candidate["receipt"]["effect_id"] = "effect_222222222222"
    generation_submission.advance(candidate, actor=candidate["created_by"], submit=True, store=store(job()))
    assert "回执" in candidate["error"] and not candidate.get("message_id")


def test_service_entry_passes_its_persisted_store_through_actual_event(prepared_runtime, monkeypatch):
    from management_portal import isa_client
    from management_portal.permission_system.facts import Fact
    from management_portal.workbench_projections.service import ProjectionService
    from management_portal.workbench_projections.store import Store
    from tests.cases.isa_test_support import ok
    runtime = prepared_runtime
    runtime.permissions.place(Fact("lineage", runtime.lineage_id, {"project": "StudioPrivate"}))
    runtime.permissions.grant(runtime.owner, runtime.lineage_id, "@contributor")
    dispatched = []
    def local_call(caller, name, arguments=None, **options):
        if name == "TriggerEvent":
            dispatched.append((caller, copy.deepcopy(arguments)))
        return ok(name, arguments, actor=caller, **options)
    monkeypatch.setattr(isa_client, "call", local_call)
    saved_store = Store()
    binding = {"main": {"lineage_id": runtime.lineage_id, "residents": {"designer": runtime.recipient}}}
    app = saved_store.create(WORKBENCH, {"title": "Existing draft", "html": "<p>Before</p>",
                                       "bindings": binding}, runtime.owner)
    service = ProjectionService(saved_store)
    # Existing factory/role discovery is outside this change; no Resident or
    # model is created here. Store, service submission, ISA Event and admission
    # remain the actual candidate implementation.
    monkeypatch.setattr(service, "initialize", lambda name, actor: app)
    monkeypatch.setattr(service, "require_role", lambda factory, actor, role, **kw: (factory, runtime.recipient))
    payload = {"request_id": "studio-service-once", "lineage_id": runtime.lineage_id,
               "workbench_id": WORKBENCH, "requirements": "Improve this draft only"}
    result = service.start_generation(payload, runtime.owner)
    assert result["status"] == "running", result
    assert saved_store.job(result["id"], runtime.owner) == result
    assert result["created_by"] == runtime.owner and result["workbench_id"] == WORKBENCH
    assert dispatched[0][0] == runtime.owner
    assert dispatched[0][1]["payload"]["studio_request"]["message_id"] == result["message_id"]
    assert runtime.repository.message(runtime.recipient, result["message_id"])["author_id"] == runtime.owner
    assert service.start_generation(payload, runtime.owner) == result
    assert len(dispatched) == 1
