"""Business workflows drawn into a team's Lineage (manual Event → DeliverMessage Effect → Resident)."""

import copy
from contextlib import nullcontext
from types import SimpleNamespace

import pytest




from management_portal import isa_client, lineage_workflows, resident_handoff
from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import lineage_primitives
from management_portal.society_service import lineage_program_execution
from management_portal.society_service.lineage_effect_script import deliver_message_script, deliver_script_parts
from tests.cases.effect_script_support import effect_config, lower_effect_call
from management_portal.workbench_projections import studio
from management_portal.workbench_projections.store import ProjectionError, Store
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401


RESIDENT = "resident_111111111111"
LINEAGE = "lineage_111111111111"


@pytest.fixture
def workflow_runtime(tmp_path, monkeypatch):
    graph = {"revision": 1, "nodes": [{"id": RESIDENT}], "program": {"components": [], "wires": []}}
    calls = []
    receipts = {}

    def call(actor, name, arguments=None, *, key="", version=None, **_options):
        if actor != "owner":
            raise PermissionError("not owner")
        if name == "GetLineage":
            return {"lineage": copy.deepcopy(graph)}
        if key in receipts:
            return copy.deepcopy(receipts[key])
        calls.append((name, copy.deepcopy(arguments)))
        if name == "CreateComponent":
            component = {"id": f"{arguments['kind']}_{len(calls):012x}", **{field: arguments[field] for field in ("kind", "title", "config")}}
            graph["program"]["components"].append(component)
            graph["revision"] += 1
            result = {"component_id": component["id"], "component": component}
        elif name == "CreateWire":
            wire = {"id": f"wire_{len(calls):012x}", **{field: arguments[field] for field in ("kind", "source", "target")}}
            for field in ("source_port", "target_port"):
                if field in arguments:
                    wire[field] = arguments[field]
            if "config" in arguments:
                wire["config"] = copy.deepcopy(arguments["config"])
            graph["program"]["wires"].append(wire)
            graph["revision"] += 1
            result = {"wire_id": wire["id"], "wire": wire}
        elif name == "DeleteComponent":
            component_id = arguments["component_id"]
            graph["program"]["components"] = [c for c in graph["program"]["components"] if c["id"] != component_id]
            graph["program"]["wires"] = [w for w in graph["program"]["wires"] if component_id not in (w["source"], w["target"])]
            result = {"component_id": component_id}
        elif name == "TriggerEvent":
            effect_id = next(wire["target"] for wire in graph["program"]["wires"] if wire["source"] == arguments["event_source_id"])
            result = {"event_id": "event_request", "trigger_path_id": "trigger_path_request", "delivery": {"results": [{
                "target_id": effect_id, "status": "executed", "invocation": {"id": "invocation_request"},
                "typed_outputs": {"resident_id": RESIDENT, "message_id": "msg_request"},
            }]}}
        else:
            raise AssertionError(name)
        receipts[key] = copy.deepcopy(result)
        return result

    monkeypatch.setattr(isa_client, "call", call)
    return SimpleNamespace(bindings=lineage_workflows.WorkflowBindings(), graph=graph, calls=calls, call=call)


def installed(runtime):
    return runtime.bindings.install(actor="owner", lineage_id=LINEAGE, resident_id=RESIDENT, name="feature",
                                    title="Feature", prompt="Fixed prompt")


def receipt_with(*results, event_id="event_request"):
    return {"event_id": event_id, "trigger_path_id": "trigger_path_request", "delivery": {"results": list(results)}}


def executed(binding, **changes):
    return {"target_id": binding["effect_id"], "status": "executed", "invocation": {"id": "invocation_request"},
            "typed_outputs": {"resident_id": binding["resident_id"], "message_id": "msg_request"}, **changes}


def test_install_draws_one_visible_flow_and_reuses_it(workflow_runtime):
    """工作流安装绘制一条可见流程且重复安装复用对象"""
    binding = installed(workflow_runtime)
    assert [name for name, _ in workflow_runtime.calls] == ["CreateComponent", "CreateComponent", "CreateWire", "CreateWire"]
    event, effect, target, invoke = [arguments for _, arguments in workflow_runtime.calls]
    assert event["kind"] == "event_source" and event["config"] == {"event_type": "manual.triggered"}
    assert effect["kind"] == "effect" and effect["config"] == {
        "script": deliver_message_script("Fixed prompt"), "endpoints": ["target"],
    }
    assert (target["kind"], target["source"], target["target"]) == ("target", binding["effect_id"], RESIDENT)
    assert (invoke["kind"], invoke["source"], invoke["target"]) == ("route", binding["event_source_id"], binding["effect_id"])
    assert installed(workflow_runtime) == binding
    assert len(workflow_runtime.calls) == 4


def test_installed_binding_validates_recorded_wire_identity(workflow_runtime):
    """已安装绑定重新核对所记录的连线身份"""
    installed(workflow_runtime)
    workflow_runtime.graph["program"]["wires"][0]["id"] = "replacement_wire"
    with pytest.raises(ValueError, match="连接"):
        installed(workflow_runtime)
    assert len(workflow_runtime.calls) == 4


def test_reusing_a_workflow_does_not_wait_for_another_installation(workflow_runtime):
    """复用现有工作流无需等待其他安装锁"""
    from concurrent.futures import ThreadPoolExecutor

    binding = installed(workflow_runtime)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with workflow_runtime.bindings.lineage_lock(LINEAGE):
            assert pool.submit(installed, workflow_runtime).result(timeout=3) == binding
    assert len(workflow_runtime.calls) == 4


@pytest.mark.parametrize("removed", ["components", "wires", "resident"])
def test_deleted_binding_is_not_silently_recreated(workflow_runtime, removed):
    """工作流绑定对象被删除后拒绝静默重建

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f
    """
    installed(workflow_runtime)
    if removed == "resident":
        workflow_runtime.graph["nodes"] = []
    else:
        workflow_runtime.graph["program"][removed] = []
    with pytest.raises(ValueError):
        installed(workflow_runtime)
    assert len(workflow_runtime.calls) == 4


def test_install_resumes_with_the_same_keys_after_a_lost_reply(workflow_runtime, monkeypatch):
    """安装回复丢失后沿用相同幂等键恢复"""
    call = workflow_runtime.call
    keys = []

    def lose_first_effect(actor, name, arguments=None, **options):
        result = call(actor, name, arguments, **options)
        if name == "CreateComponent" and arguments["kind"] == "effect":
            keys.append(options["key"])
            if len(keys) == 1:
                raise TimeoutError("reply lost after the component was created")
        return result

    monkeypatch.setattr(isa_client, "call", lose_first_effect)
    with pytest.raises(TimeoutError):
        installed(workflow_runtime)
    binding = installed(workflow_runtime)
    assert keys[0] == keys[1]
    assert len(workflow_runtime.calls) == 4
    assert binding["effect_id"] in {item["id"] for item in workflow_runtime.graph["program"]["components"]}


def accept_component_updates(runtime, monkeypatch):
    call = runtime.call

    def with_updates(actor, name, arguments=None, **options):
        if name == "UpdateWire":
            runtime.calls.append((name, copy.deepcopy(arguments)))
            wire = next(item for item in runtime.graph["program"]["wires"] if item["id"] == arguments["wire_id"])
            wire.update(copy.deepcopy(arguments["changes"]))
            runtime.graph["revision"] += 1
            return {"wire": copy.deepcopy(wire)}
        if name != "UpdateComponent":
            return call(actor, name, arguments, **options)
        runtime.calls.append((name, copy.deepcopy(arguments)))
        component = next(item for item in runtime.graph["program"]["components"] if item["id"] == arguments["component_id"])
        component["config"].update(copy.deepcopy(arguments["changes"]))
        runtime.graph["revision"] += 1
        return {"component": copy.deepcopy(component)}

    monkeypatch.setattr(isa_client, "call", with_updates)


def effect_message(runtime, binding):
    effect = next(item for item in runtime.graph["program"]["components"] if item["id"] == binding["effect_id"])
    endpoint, message, constants = deliver_script_parts(effect["config"]["script"])
    return message, {"endpoint": endpoint, **constants}


def install_with(runtime, prompt):
    return runtime.bindings.install(actor="owner", lineage_id=LINEAGE, resident_id=RESIDENT, name="feature",
                                    title="Feature", prompt=prompt)


def test_a_changed_business_prompt_reaches_the_effect_this_workflow_wrote(workflow_runtime, monkeypatch):
    """业务功能流程的固定 Prompt 修改后，仍是流程所写文字的 Effect 随之更新

    流程记录写入 Effect 的 Prompt 摘要；业务代码的固定 Prompt 改变且 Effect 仍是流程写入的文字时，经 UpdateComponent 只替换 message，保留其他参数；之后不重复更新。

    :importance: P1
    :aspects: functional, behavior
    """
    accept_component_updates(workflow_runtime, monkeypatch)
    binding = installed(workflow_runtime)
    effect = next(item for item in workflow_runtime.graph["program"]["components"] if item["id"] == binding["effect_id"])
    effect["config"]["script"] = deliver_message_script("Fixed prompt", activation_policy="enqueue_and_trigger")
    assert install_with(workflow_runtime, "Revised prompt") == binding
    message, arguments = effect_message(workflow_runtime, binding)
    assert message == "Revised prompt"
    assert arguments == {"endpoint": "target", "activation_policy": "enqueue_and_trigger"}
    assert [name for name, _ in workflow_runtime.calls].count("UpdateComponent") == 1
    install_with(workflow_runtime, "Revised prompt")
    assert [name for name, _ in workflow_runtime.calls].count("UpdateComponent") == 1


def test_an_effect_message_changed_in_the_lineage_is_kept(workflow_runtime, monkeypatch):
    """在 Lineage 中被修改过的流程 Effect 文字不被业务代码覆盖

    Effect 文字与流程记录的摘要不同（有人在 Lineage 中改过）时，业务固定 Prompt 的更新不覆盖它。

    :importance: P1
    :aspects: functional, behavior
    """
    accept_component_updates(workflow_runtime, monkeypatch)
    binding = installed(workflow_runtime)
    effect = next(item for item in workflow_runtime.graph["program"]["components"] if item["id"] == binding["effect_id"])
    effect["config"]["script"] = deliver_message_script("The owner's own wording")
    install_with(workflow_runtime, "Revised prompt")
    assert effect_message(workflow_runtime, binding)[0] == "The owner's own wording"
    assert "UpdateComponent" not in [name for name, _ in workflow_runtime.calls]


def test_an_effect_without_a_recorded_prompt_is_not_rewritten(workflow_runtime, monkeypatch):
    """没有记录 Prompt 摘要的旧流程 Effect 不被自动改写

    旧流程记录没有摘要、Effect 文字又与当前固定 Prompt 不同时，来源无法确认，不自动改写。

    :importance: P1
    :aspects: functional, behavior
    """
    accept_component_updates(workflow_runtime, monkeypatch)
    binding = installed(workflow_runtime)
    store = workflow_runtime.bindings.store
    state = store.get(LINEAGE, RESIDENT, "feature")
    state.pop("prompt_sha256")
    store.save(state)
    assert "prompt_sha256" not in store.get(LINEAGE, RESIDENT, "feature")
    effect = next(item for item in workflow_runtime.graph["program"]["components"] if item["id"] == binding["effect_id"])
    effect["config"]["script"] = deliver_message_script("Wording of unknown origin")
    install_with(workflow_runtime, "Revised prompt")
    assert effect_message(workflow_runtime, binding)[0] == "Wording of unknown origin"
    assert "UpdateComponent" not in [name for name, _ in workflow_runtime.calls]


def test_workflow_adds_task_credentials_once_and_preserves_custom_effects(workflow_runtime, monkeypatch):
    """只升级仍由流程持有的 Effect；普通流程不转交凭证，自定义流程先修绑定。"""
    runtime = workflow_runtime
    accept_component_updates(runtime, monkeypatch)
    binding = installed(runtime)
    def install():
        return runtime.bindings.install(actor="owner", lineage_id=LINEAGE, resident_id=RESIDENT,
                                        name="feature", title="Feature", prompt="Fixed prompt",
                                        lend_temporary_credentials=True)
    assert install() == binding
    assert effect_message(runtime, binding)[1]["lend_temporary_credentials"] is True
    assert install() == binding
    assert [name for name, _ in runtime.calls].count("UpdateComponent") == 1
    credentials = [{"a": "@contributor", "resource_id": "workbench_aaaaaaaaaaaa"}]
    runtime.bindings.trigger(binding, actor="owner", prompt="Generate", request_id="req-credentials",
                             temporary_credentials=credentials)
    assert runtime.calls[-1][1]["payload"] == {"temporary_credentials": credentials}
    effect = next(item for item in runtime.graph["program"]["components"] if item["id"] == binding["effect_id"])
    effect["config"]["script"] = deliver_message_script("Custom instructions")
    with pytest.raises(ValueError, match="已自定义"):
        install()
    assert effect_message(runtime, binding)[0] == "Custom instructions"
    assert [name for name, _ in runtime.calls].count("UpdateComponent") == 1


@pytest.mark.parametrize("upgrade", [False, True])
def test_workflow_credentials_survive_manual_event_envelope(workflow_runtime, monkeypatch, upgrade):
    """真实手动事件封装与 Effect 输入边界仍能转交类型化凭证；旧连线升级保留其他配置。"""
    from management_portal.society_service import lineage_program_backlog as backlog

    runtime = workflow_runtime
    accept_component_updates(runtime, monkeypatch)
    if upgrade:
        installed(runtime)
        route = next(w for w in runtime.graph["program"]["wires"] if w["kind"] == "route")
        route["config"]["queue_limit"] = 17
    options = dict(actor="owner", lineage_id=LINEAGE, resident_id=RESIDENT,
                   name="feature", title="Feature", prompt="Fixed prompt", lend_temporary_credentials=True)
    binding = runtime.bindings.install(**options)
    route = next(w for w in runtime.graph["program"]["wires"] if w["kind"] == "route")
    assert route["config"]["include_envelope"] is True
    assert route["config"]["queue_limit"] == (17 if upgrade else 100)
    before = len(runtime.calls)
    assert runtime.bindings.install(**options) == binding
    assert len(runtime.calls) == before
    assert sum(name == "UpdateWire" for name, _ in runtime.calls) == int(upgrade)

    captured = []
    def dispatch(lineage_id, source_id, event, actor, **kwargs):
        assert (lineage_id, source_id, actor) == (LINEAGE, binding["event_source_id"], "owner")
        invocation = lineage_program_execution._make_program_effect_invocation(
            event, source_kind="event", source_wire_id=route["id"],
            include_envelope=route["config"]["include_envelope"],
        )
        effect = next(c for c in runtime.graph["program"]["components"] if c["id"] == binding["effect_id"])
        definition = {**effect["config"], "endpoints": {"target": RESIDENT}}
        captured.append(lower_effect_call(LINEAGE, binding["effect_id"], definition, invocation))
        return {"event": event.to_dict()}

    authority = SimpleNamespace(
        lineage_scope=lambda _: nullcontext(), read_lineage=lambda *a, **kw: copy.deepcopy(runtime.graph),
        ensure_manager=lambda graph, actor: None, dispatch_lineage_program_event=dispatch,
        observe_trigger_path=lambda *a: {}, get_lineage=lambda *a: copy.deepcopy(runtime.graph),
    )
    monkeypatch.setattr(backlog, "drain_event_backlog", lambda *a, **kw: {})
    credentials = [{"a": "@contributor", "resource_id": "workbench_aaaaaaaaaaaa"}]
    runtime.bindings.trigger(binding, actor="owner", prompt="Generate", request_id="envelope-proof",
                             temporary_credentials=credentials)
    arguments = runtime.calls[-1][1]
    backlog.emit_manual_event(LINEAGE, arguments, "owner", authority=authority)
    assert captured[0]["arguments"]["temporary_credentials"] == credentials
    assert captured[0]["arguments"]["resident_id"] == RESIDENT
    assert captured[0]["arguments"]["message"]["content_parts"] == [{"type": "text", "text": "Fixed prompt\n\nGenerate"}]


def test_every_studio_flow_delivers_explicit_task_credentials(monkeypatch):
    """生成、优化、反投影均通过普通消息流程转交凭证，其他业务流程仍默认不转交。"""
    from management_portal.workbench_projections import studio
    calls = []
    monkeypatch.setattr(studio.WorkflowBindings, "install", lambda self, **kwargs: calls.append(kwargs) or kwargs)
    studio.install(None, {"bindings": {"main": {"lineage_id": LINEAGE, "residents": {"designer": RESIDENT}}}}, "owner")
    assert {item["name"] for item in calls} == {"studio:generate", "studio:optimize", "studio:reverse"}
    assert all(item["lend_temporary_credentials"] is True for item in calls)


@pytest.mark.parametrize("committed", [False, True])
def test_credential_route_upgrade_resumes_after_lost_reply(workflow_runtime, monkeypatch, committed):
    """Effect 已更新而连线回复丢失时继续核对同一连线，不触发任务或改写其他配置。"""
    runtime = workflow_runtime
    binding = installed(runtime)
    accept_component_updates(runtime, monkeypatch)
    call = isa_client.call
    attempts = []

    def interrupted(actor, name, arguments=None, **options):
        if name == "UpdateWire":
            attempts.append(options["key"])
            if len(attempts) == 1:
                if committed:
                    call(actor, name, arguments, **options)
                raise TimeoutError("route reply lost")
        return call(actor, name, arguments, **options)

    monkeypatch.setattr(isa_client, "call", interrupted)
    options = dict(actor="owner", lineage_id=LINEAGE, resident_id=RESIDENT,
                   name="feature", title="Feature", prompt="Fixed prompt", lend_temporary_credentials=True)
    with pytest.raises(TimeoutError, match="route reply lost"):
        runtime.bindings.install(**options)
    assert runtime.bindings.install(**options) == binding
    assert len(attempts) == (1 if committed else 2)
    assert len(set(attempts)) == 1
    assert sum(name == "UpdateComponent" for name, _ in runtime.calls) == 1
    route = next(w for w in runtime.graph["program"]["wires"] if w["kind"] == "route")
    assert route["config"] == {"include_envelope": True, "unavailable_policy": "queue", "queue_limit": 100}
    assert not any(name == "TriggerEvent" for name, _ in runtime.calls)


@pytest.mark.parametrize("lost_receipt", [False, True])
def test_dynamic_wall_dispatch_installs_only_requested_flow_without_triggering(workflow_runtime, monkeypatch, lost_receipt):
    """动态 WALL 分派只安装请求流程且不触发执行"""
    from management_portal.workbench_projections import native
    from management_portal.wall_workflows import work_flow

    runtime = workflow_runtime
    identity = {"lineage_id": LINEAGE, "resident_id": RESIDENT}
    binding_calls = []

    def resolve(*args, **kwargs):
        binding_calls.append((args, kwargs))
        return identity

    monkeypatch.setattr(native, "binding", resolve)
    monkeypatch.setattr(lineage_workflows, "WorkflowBindings", lambda: runtime.bindings)
    scope = "wall-work:project-fixture:design-point"
    attempts = []
    if lost_receipt:
        from management_portal.society_service import SocietyServiceBusy

        call = runtime.call

        def recover(actor, name, arguments=None, **options):
            if name == "CreateComponent" and arguments["kind"] == "event_source":
                attempts.append(copy.deepcopy((name, arguments, options)))
                result = call(actor, name, arguments, **options)
                if len(attempts) == 1:
                    raise SocietyServiceBusy("committed response lost")
                if len(attempts) == 2:
                    raise InstructionError("instruction.in_progress", "pending", http_status=409)
                return result
            return call(actor, name, arguments, **options)

        monkeypatch.setattr(isa_client, "call", recover)
        monkeypatch.setattr(native.time, "sleep", lambda _: None)

    def prepare():
        return native.execution_bindings("wall", {"actor": "owner", "resident_scope": scope,
            "create_role": True, "workflow": work_flow()}, "codex")

    first = prepare()
    assert first["resident_binding"] == identity
    assert len(binding_calls) == 1
    assert len(runtime.calls) == 4
    assert {arguments["title"] for name, arguments in runtime.calls if arguments.get("kind") == "effect"} == {
        work_flow()["title"] + " · 执行"
    }
    assert prepare() == first
    assert len(runtime.calls) == 4
    assert all(name != "TriggerEvent" for name, _ in runtime.calls)
    if lost_receipt:
        assert len(attempts) == 3
        assert attempts[0] == attempts[1] == attempts[2]


@pytest.mark.parametrize("lost_receipt", [False, True])
def test_trigger_is_invocation_local_and_replays_its_key(workflow_runtime, monkeypatch, lost_receipt):
    """工作流触发保持调用局部输入并按请求键重放"""
    runtime = workflow_runtime
    binding = installed(runtime)
    definition = copy.deepcopy(runtime.graph)
    attempts = []
    if lost_receipt:
        from management_portal.society_service import SocietyServiceBusy

        call = runtime.call

        def recover(actor, name, arguments=None, **options):
            attempts.append(copy.deepcopy((name, arguments, options)))
            if name == "GetInstructionExecution":
                assert arguments == {"instruction": "TriggerEvent", "idempotency_key": attempts[0][2]["key"]}
                raise InstructionError("instruction.not_found", "original receipt not yet visible")
            # Commit once, then lose the response. Recovery only queries the original key.
            result = call(actor, name, arguments, **options)
            if len(attempts) == 1:
                raise SocietyServiceBusy("response lost after delivery")
            if len(attempts) == 2:
                raise InstructionError("instruction.in_progress", "still executing", http_status=409)
            return result

        monkeypatch.setattr(isa_client, "call", recover)
    if lost_receipt:
        with pytest.raises(lineage_workflows.WorkflowPending) as pending:
            runtime.bindings.trigger(binding, actor="owner", prompt="First", request_id="one")
        assert len(attempts) == 2 and len(runtime.calls) == 5
        assert pending.value.handle["request_id"] == "one"
        assert "message_id" not in pending.value.handle
        return  # An unavailable original receipt stays pending; never reissue TriggerEvent.
    first = runtime.bindings.trigger(binding, actor="owner", prompt="First", request_id="one")
    second = runtime.bindings.trigger(binding, actor="owner", prompt="Second", request_id="two")
    assert first["message_id"] == second["message_id"] == "msg_request"
    assert first["trigger_path_id"] == "trigger_path_request"
    assert runtime.graph == definition
    assert [arguments["prompt"] for name, arguments in runtime.calls if name == "TriggerEvent"] == ["First", "Second"]
    runtime.bindings.trigger(binding, actor="owner", prompt="First", request_id="one")
    assert len(runtime.calls) == 6
    with pytest.raises(PermissionError, match="not owner"):
        runtime.bindings.trigger(binding, actor="outsider", prompt="First", request_id="one")
    assert len(runtime.calls) == 6


def _submission_path(binding, *, event_id="event_request", message_id="msg_request"):
    steps = [{"step_id": "effect_step", "kind": "effect", "node_id": binding["effect_id"], "status": "failed"}]
    edges = []
    if message_id:
        steps.append({"step_id": "resident_step", "kind": "resident", "node_id": binding["resident_id"],
                      "input_message_id": message_id})
        edges.append({"source_step_id": "effect_step", "target_step_id": "resident_step"})
    return {"path": {"trigger_path_id": "trigger_path_request", "lineage_id": binding["lineage_id"],
                    "root_kind": "event", "root_node_id": binding["event_source_id"], "occurrence_id": event_id},
            "steps": {"items": steps, "next_offset": None}, "edges": {"items": edges, "next_offset": None}}


def test_a_replayed_trigger_reads_its_run(workflow_runtime, monkeypatch):
    """重放触发通过原 Trigger Path 找回运行身份"""
    binding = installed(workflow_runtime)
    reads = []

    def replayed(actor, name, arguments=None, **options):
        if name == "TriggerEvent":
            return {"event_id": "event_request", "trigger_path_id": "trigger_path_request", "delivery": {}}
        if name == "GetInputState":
            return {"input_state": {"message_id": "msg_request", "state": "running"}}
        assert name == "GetTriggerPath"
        reads.append(arguments)
        return _submission_path(binding)

    monkeypatch.setattr(isa_client, "call", replayed)
    handle = workflow_runtime.bindings.trigger(binding, actor="owner", prompt="request", request_id="replay")
    assert reads == [{"lineage_id": LINEAGE, "trigger_path_id": "trigger_path_request", "offset": 0, "limit": 200}]
    assert (handle["message_id"], handle["event_id"]) == ("msg_request", "event_request")


def test_trigger_preserves_failed_effect_route_error(workflow_runtime, monkeypatch):
    """Effect 路由失败时保留待确认句柄及事件身份"""
    binding = installed(workflow_runtime)
    receipt = receipt_with({"target_id": binding["effect_id"], "status": "failed", "error": "resident intake closed"},
                           event_id="event_failure")
    calls = []

    def failed_path(actor, name, arguments=None, **options):
        calls.append(name)
        if name == "TriggerEvent":
            return receipt
        assert name == "GetTriggerPath"
        return _submission_path(binding, event_id="event_failure", message_id="")

    monkeypatch.setattr(isa_client, "call", failed_path)
    from management_portal.lineage_workflows import WorkflowPending
    with pytest.raises(WorkflowPending) as failure:
        workflow_runtime.bindings.trigger(binding, actor="owner", prompt="request", request_id="failure")
    assert failure.value.handle["event_id"] == "event_failure"
    assert failure.value.handle["submission_state"] == "pending"
    assert "message_id" not in failure.value.handle  # Effect failure alone cannot disprove an earlier delivery.
    assert calls == ["TriggerEvent", "GetTriggerPath"]


def test_install_needs_the_callers_own_authority(workflow_runtime):
    """安装工作流要求调用者自身具备权限"""
    installed(workflow_runtime)
    with pytest.raises(PermissionError):
        workflow_runtime.bindings.install(actor="outsider", lineage_id=LINEAGE, resident_id=RESIDENT,
                                          name="feature", title="Feature", prompt="Fixed prompt")


def test_trigger_reports_pending_delivery_without_claiming_identity_corruption(workflow_runtime, monkeypatch):
    """工作流投递排队时报告待确认且不虚构消息身份"""
    binding = installed(workflow_runtime)
    calls = []

    def queued(*args, **kwargs):
        calls.append((args, kwargs))
        return receipt_with({"target_id": binding["effect_id"], "status": "queued"}, event_id="event_waiting")

    monkeypatch.setattr(isa_client, "call", queued)
    from management_portal.lineage_workflows import WorkflowPending
    with pytest.raises(WorkflowPending, match="工作流提交待确认：event_waiting.*勿重复提交") as pending:
        workflow_runtime.bindings.trigger(binding, actor="owner", prompt="request", request_id="waiting")
    assert pending.value.handle["event_id"] == "event_waiting"
    assert pending.value.handle["submission_state"] == "pending" and "message_id" not in pending.value.handle
    assert len(calls) == 2  # one submission, then one read


@pytest.mark.parametrize("invalid", ["resident", "duplicate", "message", "event", "other_effect_only"])
def test_workflow_trigger_rejects_unbound_or_ambiguous_receipts(workflow_runtime, monkeypatch, invalid):
    """工作流触发拒绝未绑定或存在歧义的回执

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f, decision_34158fdd4078
    """
    binding = installed(workflow_runtime)
    result = executed(binding)
    if invalid == "resident":
        result["typed_outputs"]["resident_id"] = "resident_unrelated"
    elif invalid == "message":
        result["typed_outputs"]["message_id"] = ""
    elif invalid == "other_effect_only":
        result["target_id"] = "effect_unrelated"
    receipt = receipt_with(*([result, copy.deepcopy(result)] if invalid == "duplicate" else [result]))
    if invalid == "event":
        receipt["event_id"] = ""
    monkeypatch.setattr(isa_client, "call", lambda actor, name, args, **kwargs: ({"steps": {"items": [], "next_offset": None}} if name == "GetTriggerPath" else receipt))
    with pytest.raises(RuntimeError):
        workflow_runtime.bindings.trigger(binding, actor="owner", prompt="Request", request_id="request")


def test_workflow_trigger_does_not_mix_other_effect_results(workflow_runtime, monkeypatch):
    """工作流触发只取绑定 Effect 的执行结果

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f, decision_34158fdd4078
    """
    binding = installed(workflow_runtime)
    receipt = receipt_with(
        {"target_id": "effect_unrelated", "status": "failed", "typed_outputs": {"message_id": "msg_other"}},
        executed(binding, typed_outputs={"resident_id": binding["resident_id"], "message_id": "msg_selected"}),
    )
    monkeypatch.setattr(isa_client, "call", lambda *args, **kwargs: receipt)
    handle = workflow_runtime.bindings.trigger(binding, actor="owner", prompt="Request", request_id="request")
    assert handle["message_id"] == "msg_selected"
    assert "invocation_id" not in handle


def test_invocation_preserves_media_and_context_without_changing_prompt_contracts():
    """调用保留媒体和上下文并拼接固定提示与本次输入

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f
    """
    parts = [{'type': 'image', 'source_type': 'local_path', 'source': '/tmp/figure.png'},
             {'type': 'context_reference', 'context': {'working_directory': '/tmp', 'context_text': 'request context'}}]
    event = lineage_primitives.make_event('manual.triggered', payload={'prompt': 'Question', 'content_parts': parts})
    invocation = lineage_program_execution._make_program_effect_invocation(event, source_kind='event')
    assert invocation.arguments == {'prompt': 'Question', 'content_parts': parts}
    definition = {**effect_config("DeliverMessage", {"resident_id": {"from": "endpoint.target"}, "message": {"value": "Fixed"}}),
                  "endpoints": {"target": RESIDENT}}
    call = lower_effect_call(LINEAGE, "effect_111111111111", definition, invocation)
    assert call["arguments"]["message"]["content_parts"] == [{'type': 'text', 'text': 'Fixed\n\nQuestion'}, *parts]


def test_reverse_references_only_prior_receipts():
    """反向计划仅可引用先前步骤的回执"""
    plan = {'summary': 'Add flow', 'steps': [
        {'id': 'event', 'instruction': 'CreateComponent', 'arguments': {'kind': 'event_source', 'title': 'Request', 'config': {'event_type': 'manual.triggered'}}},
        {'id': 'wire', 'instruction': 'CreateWire', 'arguments': {'kind': 'route', 'source': {'$ref': 'event.component_id'}, 'target': 'existing'}},
    ]}
    assert studio.validate_plan(plan) == plan
    assert studio.resolve_references(plan['steps'][1]['arguments'], {'event': {'component_id': 'actual'}})['source'] == 'actual'
    plan['steps'].reverse()
    with pytest.raises(ValueError):
        studio.validate_plan(plan)


def test_invocation_rejects_untyped_parts(subtests, isolated_portal_case):
    """调用拒绝未按类型定义的内容片段

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f
    """
    for parts in [[{"type": "text", "text": "replacement"}], {"actor": "owner"}, [None]]:
        with subtests.test(parts=parts), isolated_portal_case() as case_path:
            with pytest.raises(ValueError):
                lineage_primitives.make_event("manual.triggered", payload={"content_parts": parts})


def test_reverse_analysis_cannot_request_execution_or_destructive_change(subtests, isolated_portal_case):
    """反向分析计划拒绝执行和破坏性修改指令

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f
    """
    for operation in ["DeliverMessage", "DeleteComponent", "UpdateLineage", "TriggerEvent"]:
        with subtests.test(operation=operation), isolated_portal_case() as case_path:
            with pytest.raises(ValueError):
                studio.validate_plan({"summary": "unsafe", "steps": [{"id": "step", "instruction": operation, "arguments": {}}]})


def test_submission_returns_only_canonical_workflow_handle(monkeypatch):
    """工作流投递保留规范输入身份与附件

    验证请求重放沿用原幂等键，回执使用真实 Resident message_id，并保留本次图像和上下文而不混入运行图。

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f
    """
    binding = {"lineage_id": "lineage_111111111111", "resident_id": "resident_111111111111",
               "event_source_id": "event_source_input", "effect_id": "effect_input"}
    submitted = []
    assigned = []
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: SimpleNamespace(resident=object()))
    monkeypatch.setattr(resident_handoff, "ensure_invocation_resident", lambda **kwargs: binding)

    def trigger(self, actual_binding, **kwargs):
        assert actual_binding == binding
        submitted.append(kwargs)
        return {**binding, "event_id": "event_input", "trigger_path_id": "trigger_path_123456789abc",
                "message_id": "msg_canonical", "receipt": {"runtime_state": {}}}

    monkeypatch.setattr(lineage_workflows.WorkflowBindings, "trigger", trigger)
    handle = resident_handoff.submit_invocation(actor="owner", message="Question", resident_scope="scope",
        business_ref="request-one", workflow_binding=binding, runtime_context="Request context",
        input_media=[{"type": "image", "source_type": "local_path", "source": "/tmp/image.png"}],
        on_assigned=assigned.append)
    assert all(handle[key] == value for key, value in {**binding, "event_id": "event_input",
        "trigger_path_id": "trigger_path_123456789abc", "message_id": "msg_canonical"}.items())
    assert handle["submission_state"] == "submitted" and "receipt" not in handle
    assert [(item["resident_id"], item["lineage_id"]) for item in assigned] == [(binding["resident_id"], binding["lineage_id"])]
    assert assigned[0]["request_id"].startswith("msg_") and "message_id" not in assigned[0]
    assert assigned[0]["submission_state"] == "pending"
    assert all(assigned[0][key] == value for key, value in binding.items())
    assert submitted[0]["prompt"] == "Question"
    assert [part["type"] for part in submitted[0]["content_parts"]] == ["image", "context_reference"]
    assert submitted[0]["request_id"] != handle["message_id"]
    resident_handoff.submit_invocation(actor="owner", message="Question", resident_scope="scope",
        business_ref="request-one", workflow_binding=binding)
    assert submitted[0]["request_id"] == submitted[1]["request_id"]


@pytest.mark.usefixtures("projection_input_reader")
def test_generation_resumes_original_event_and_preserves_terminal_failure(tmp_path, monkeypatch):
    """生成任务恢复原请求并保留额度失败

    验证提交中断后使用原 Event 请求键恢复观察；额度失败保持终态，不新建输入或偷偷切换身份。

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f
    """
    from management_portal.workbench_projections import service
    from tests.cases.workbench_support import use_controlled_projection_account_check
    use_controlled_projection_account_check(monkeypatch)

    store = Store()
    runtime = service.ProjectionService(store)
    binding = {"lineage_id": "lineage_111111111111", "resident_id": "resident_111111111111",
               "event_source_id": "event_source_input", "effect_id": "effect_input"}
    job = {"id": "resume-job", "status": "submitting", "message_id": "", "request_prompt": "Original request", **binding}
    store.job(job["id"], "owner", job)
    observed = []

    # Recovery observes the original submission key; it never resubmits (c934491c).
    def observe(self, handle, **kwargs):
        assert {key: handle[key] for key in binding} == binding
        observed.append(kwargs)
        return {**handle, "message_id": "msg_canonical", "event_id": "event_original", "status": "running"}

    def input_state(actor, name, arguments=None, **_options):
        assert name == "GetInputState"
        assert arguments == {"resident_id": binding["resident_id"], "message_id": "msg_canonical"}
        return {"state": "failed", "error": {"code": "provider_usage_limit", "message": "Quota exhausted"}}

    monkeypatch.setattr(lineage_workflows.WorkflowBindings, "trigger", lambda *_args, **_kwargs: pytest.fail("recovery must not resubmit"))
    monkeypatch.setattr(lineage_workflows.WorkflowBindings, "observe", observe)
    monkeypatch.setattr(service, "isa", input_state)
    result = runtime.generation(job["id"], "owner")
    assert result["status"] == "failed"
    assert result["input_error"]["code"] == "provider_usage_limit"
    assert result["event_id"] == "event_original"
    assert runtime.generation(job["id"], "owner") == result
    assert observed == [{"actor": "owner"}]


@pytest.fixture
def reverse_service(tmp_path, monkeypatch):
    from management_portal.workbench_projections import service
    from tests.cases.workbench_support import use_controlled_projection_account_check
    use_controlled_projection_account_check(monkeypatch)

    store = Store()
    app = store.create("workbench_111111111111", {"title": "Example", "html": "<section>Example</section>", "bindings": {"main": {"lineage_id": "lineage_111111111111", "residents": {"coordinator": "resident_111111111111"}}}}, "owner")
    graph = {"revision": 1, "program": {"components": [], "wires": []}}
    calls = []
    applied = {}

    def execute(actor, name, arguments=None, *, key="", version=None):
        if actor not in {"owner", "test-actor"}:
            raise PermissionError("not owner")
        if name == "GetLineage":
            return {"lineage": copy.deepcopy(graph)}
        if key in applied:
            return copy.deepcopy(applied[key])
        assert version is None  # Lineage instructions take no version precondition
        calls.append(name)
        if name == "CreateComponent":
            component = {"id": f"{arguments['kind']}_created", **{field: arguments[field] for field in ("kind", "title", "config")}}
            graph["program"]["components"].append(component)
            result = {"component_id": component["id"], "component": component}
        else:
            assert name == "CreateWire"
            wire = {"id": f"wire_{len(calls)}", **{field: arguments[field] for field in ("kind", "source", "target")}}
            if "config" in arguments:
                wire["config"] = copy.deepcopy(arguments["config"])
            graph["program"]["wires"].append(wire)
            result = {"wire_id": wire["id"], "wire": wire}
        graph["revision"] += 1
        applied[key] = copy.deepcopy(result)
        return result

    monkeypatch.setattr(service, "isa", execute)
    monkeypatch.setattr(isa_client, "call", execute)
    projection = SimpleNamespace(store=store, lock=lambda _: nullcontext(), update=lambda app_id, payload, actor: store.update(app_id, payload, actor, payload["expected_revision"]))
    plan = {"summary": "Create manual workflow", "steps": [
        {"id": "event", "instruction": "CreateComponent", "arguments": {"kind": "event_source", "title": "Input", "config": {"event_type": "manual.triggered"}}},
        {"id": "effect", "instruction": "CreateComponent", "arguments": {"kind": "effect", "title": "Handle", "config": {
            "script": 'DeliverMessage resident_id="${endpoint.target}"', "endpoints": ["target"]}}},
        {"id": "target", "instruction": "CreateWire", "arguments": {"kind": "target", "source": {"$ref": "effect.component_id"}, "source_port": "target", "target": RESIDENT}},
        {"id": "invoke", "instruction": "CreateWire", "arguments": {"kind": "route", "source": {"$ref": "event.component_id"}, "target": {"$ref": "effect.component_id"}}},
    ], "page_update": {"html": "<section>Updated draft</section>", "events": {"validate": {"$ref": "event.component_id"}}}}
    job = {"id": "reverse_job", "kind": "reverse", "workbench_id": app["workbench_id"], "base_revision": app["revision"], "target_lineage_id": LINEAGE, "target_revision": 1, "plan": plan, "plan_hash": studio.plan_hash(plan), "status": "completed"}
    store.job(job["id"], "owner", job)
    return SimpleNamespace(service=projection, job=job, graph=graph, calls=calls)


@pytest.mark.parametrize("actor", ["owner", "test-actor"])
def test_reverse_apply_preserves_team_and_only_saves_draft(reverse_service, actor):
    """反向应用保留团队所有者且仅保存页面草稿

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f
    """
    runtime = reverse_service
    payload = {"job_id": runtime.job["id"], "plan_hash": runtime.job["plan_hash"], "confirm": True}
    result = studio.apply_reverse(runtime.service, payload, actor)
    assert result["applied"]
    app = runtime.service.store.get(runtime.job["workbench_id"], "owner")
    assert app["bindings"]["main"]["events"] == {"validate": "event_source_created"}
    assert app["created_by"] == "owner"
    assert not app["published_revision"]
    assert runtime.calls == ["CreateComponent", "CreateComponent", "CreateWire", "CreateWire"]
    assert studio.apply_reverse(runtime.service, payload, actor)["applied"]
    assert len(runtime.calls) == 4


@pytest.mark.parametrize("change", ["page", "graph", "hash", "confirmation", "actor"])
def test_reverse_apply_rejects_changed_baseline_or_authority(reverse_service, change):
    """反向应用拒绝基线、计划摘要或权限已变化的请求

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f
    """
    runtime = reverse_service
    payload = {"job_id": runtime.job["id"], "plan_hash": runtime.job["plan_hash"], "confirm": True}
    if change == "page":
        runtime.service.store.update(runtime.job["workbench_id"], {"title": "Changed"}, "owner", runtime.job["base_revision"])
    elif change == "graph":
        runtime.graph["revision"] += 1
    elif change == "hash":
        payload["plan_hash"] = "different"
    elif change == "confirmation":
        payload["confirm"] = False
    with pytest.raises((ValueError, PermissionError, ProjectionError)):
        studio.apply_reverse(runtime.service, payload, "outsider" if change == "actor" else "owner")
    assert runtime.calls == []


@pytest.mark.parametrize("actor", ["owner", "test-actor"])
def test_manual_plan_is_explicitly_attributed_and_retains_ai_failure(reverse_service, actor):
    """手工修订计划记录作者并保留模型失败历史

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f
    """
    runtime = reverse_service
    job = runtime.job
    job.pop("plan")
    job.pop("plan_hash")
    job.update(status="failed", error="provider_usage_limit")
    runtime.service.store.job(job["id"], "owner", job)
    result = studio.revise_plan(runtime.service, {"job_id": job["id"], "expected_plan_hash": "", "plan": {"summary": "Manually inspected", "steps": []}}, actor)
    assert result["plan_source"] == "user"
    assert result["plan_author"] == actor
    assert result["plan_history"][0]["error"] == "provider_usage_limit"
    assert runtime.calls == []


def test_authenticated_access_preserves_creator_and_request_identity(tmp_path, permissions, monkeypatch):
    """管理员修改他人的工作台时保留创建者及原始请求身份

    :design: decision_f67ae5e70793, decision_4bc03b20c30f, decision_34158fdd4078"""
    from management_portal.workbench_projections import service

    store = Store()
    app = store.create("workbench_111111111111", {"title": "Private", "html": "<section>Private</section>"}, "owner")
    permissions.users("owner", "test-actor")
    permissions.users("visitor", category="intern")
    permissions.administrator("test-actor")
    permissions.place(Fact("project", "Demo", creator="owner"), Fact("lineage", "lineage_111111111111", {"project": "Demo"}),
                      Fact("workbench", app["workbench_id"], {"lineage": "lineage_111111111111"}, creator="owner"))
    assert [item["workbench_id"] for item in store.list("test-actor")] == [app["workbench_id"]]
    assert store.list("visitor") == []  # a draft is listed only to those who may change it
    changed = store.update(app["workbench_id"], {"title": "Managed"}, "test-actor", app["revision"])
    assert changed["created_by"] == "owner"
    store.publish(app["workbench_id"], "test-actor", changed["revision"])
    assert store.get(app["workbench_id"], "test-actor", published=True)["title"] == "Managed"
    assert store.get(app["workbench_id"], "visitor")["created_by"] == "owner"
    job = store.job("original", "owner", {"id": "original", "workbench_id": app["workbench_id"], "status": "submitting", "request_prompt": "Original prompt"})
    assert store.job("original", "test-actor")["created_by"] == "owner"
    assert store.job("original", "visitor")["created_by"] == "owner"
    monkeypatch.setattr(lineage_workflows.WorkflowBindings, "trigger", lambda *args, **kwargs: pytest.fail("Cannot resubmit another principal's pending request"))
    result = service.ProjectionService(store).generation("original", "test-actor")
    assert result["status"] == "submitting"
    assert result["recovery_notice"]
    assert store.job("original", "owner") == job


def test_reverse_analysis_honors_global_owner_without_starting_ai(reverse_service):
    """反向分析缺少请求标识时拒绝启动且不改动团队

    :importance: P1
    :design: decision_f67ae5e70793, decision_4bc03b20c30f, decision_34158fdd4078
    """
    runtime = reverse_service
    with pytest.raises(ValueError, match="request_id"):
        studio.start_reverse(runtime.service, {"id": runtime.job["workbench_id"]}, "test-actor")
    assert runtime.graph["revision"] == 1
    assert runtime.calls == []


def test_reverse_analysis_requires_saved_nonempty_page(reverse_service):
    """反向分析要求页面已保存且内容非空"""
    runtime = reverse_service
    app = runtime.service.store.get(runtime.job["workbench_id"], "owner")
    runtime.service.store.update(app["workbench_id"], {"html": ""}, "owner", app["revision"])

    with pytest.raises(ProjectionError) as error:
        studio.start_reverse(runtime.service, {"id": app["workbench_id"], "request_id": "stable-request"}, "owner")

    assert error.value.code == "projection.empty"
    assert runtime.calls == []


@pytest.fixture
def projection_input_reader(monkeypatch):
    from tests.cases.workbench_support import bind_resident_observation
    from management_portal.workbench_projections import service
    bind_resident_observation(monkeypatch, lambda *args, **kwargs: service.isa(*args, **kwargs))


def test_projection_and_workflow_commands_use_the_ordinary_isa_client(monkeypatch):
    """投影和工作流指令使用普通 ISA 客户端并保留身份"""
    from management_portal.hatg import routing
    from management_portal.workbench_projections import service
    calls = []

    def execute(payload, context):
        calls.append((copy.deepcopy(payload), context.caller, context.entry))
        if context.caller == "denied":
            raise PermissionError("revoked")
        return {"lineage_version": 8}

    monkeypatch.setattr(routing, "execute_or_raise", execute)
    assert service.isa("owner", "CreateComponent", {"lineage_id": LINEAGE, "kind": "effect"},
                       key="same-request", version={LINEAGE: 7}) == {"lineage_version": 8}
    assert calls == [({"instruction": "CreateComponent", "arguments": {"lineage_id": LINEAGE, "kind": "effect"},
                       "idempotency_key": "same-request", "expected_version": {LINEAGE: 7}}, "owner", "web")]
    with pytest.raises(PermissionError):
        service.isa("denied", "GetLineage", {"lineage_id": LINEAGE})


def test_workflow_follows_moved_resident_without_duplicating_graph(workflow_runtime, subtests):
    """普通迁移后保留工作流对象；不完整迁移不能触发重建。

    普通迁移后保留工作流对象；不完整迁移不能触发重建。"""
    from management_portal.workflow_bindings_store import WorkflowBindingConflict
    original = installed(workflow_runtime)
    target = "lineage_222222222222"
    graph = workflow_runtime.graph
    graph["lineage_id"] = target
    complete = copy.deepcopy(graph["program"])
    for part in ["components", "wires"]:
        with subtests.test(missing=part):
            graph["program"] = {**copy.deepcopy(complete), part: []}
            with pytest.raises(WorkflowBindingConflict):
                workflow_runtime.bindings.install(actor="owner", lineage_id=target, resident_id=RESIDENT,
                                                  name="feature", title="Feature", prompt="Fixed prompt")
            assert len(workflow_runtime.calls) == 4
    graph["program"] = complete
    moved = workflow_runtime.bindings.install(actor="owner", lineage_id=target, resident_id=RESIDENT,
                                             name="feature", title="Feature", prompt="Fixed prompt")
    assert moved == {**original, "lineage_id": target}
    assert len(workflow_runtime.calls) == 4
    store = workflow_runtime.bindings.store
    assert store.get(LINEAGE, RESIDENT, "feature") is None
    assert store.get(target, RESIDENT, "feature")["prompt_sha256"]
    assert workflow_runtime.bindings.install(actor="owner", lineage_id=target, resident_id=RESIDENT,
                                            name="feature", title="Feature", prompt="Fixed prompt") == moved


def test_wall_actions_use_one_entry(workflow_runtime):
    """WALL 所有操作共用任务入口；再次安装不再增长节点。"""
    from management_portal.wall_workflows import work_flow, definitions
    runtime = workflow_runtime
    shared = work_flow()
    first = runtime.bindings.install(actor="owner", lineage_id=LINEAGE, resident_id=RESIDENT, **shared)
    assert len(runtime.graph["program"]["components"]) == 2
    assert len(runtime.graph["program"]["wires"]) == 2
    for scope in ["wall:point", "wall-work:project", "wall:project:regression"]:
        assert definitions(scope) == [shared]
        assert runtime.bindings.install(actor="owner", lineage_id=LINEAGE, resident_id=RESIDENT, **shared) == first
    assert len(runtime.graph["program"]["components"]) == 2


def test_a_deleted_residents_bindings_are_forgotten(workflow_runtime):
    """删除 Resident 时一并删除它的工作流绑定。"""
    installed(workflow_runtime)
    store = workflow_runtime.bindings.store
    assert store.get(LINEAGE, RESIDENT, "feature")
    assert store.forget_residents([RESIDENT]) == 1
    assert store.get(LINEAGE, RESIDENT, "feature") is None
    assert store.forget_residents([RESIDENT]) == 0
