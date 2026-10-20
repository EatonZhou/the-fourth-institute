"""System execution policies follow public Event commands and durable recovery.

:importance: P2
"""
from concurrent.futures import ThreadPoolExecutor
from itertools import count
import threading

import pytest

from management_portal.execution.service_settings import ExecutionSettings
from management_portal.hatg import catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service import invariant_locks, lineage_domain, lineage_program_runtime
from management_portal.society_service import system_workflow_switches as switches
from management_portal.society_service.isa import build_kernel
from tests.cases.test_isa_audit_g4 import make_lineage, registered_members  # noqa: F401
from tests.cases.test_lineage_generic_primitives import primitive_store, ACTOR  # noqa: F401


TIMING = "system:request-timing:enabled"
HARNESS = "system:harness-recording:enabled"
FIELDS = {TIMING: "model_request_timing_enabled", HARNESS: "harness_recording_enabled"}


def switch_values(settings):
    return {field: settings[field] for field in FIELDS.values()}


def test_fresh_execution_and_lineage_kernels_agree_with_catalog():
    """execution 与 lineage 内核的处理器与指令目录一致，设置读写分属两个内核"""
    from management_portal.execution import isa as execution_isa
    from management_portal.society_service import isa as lineage_isa

    declarations = catalog.instruction_catalog()
    bindings = {module.binding().owner: module.binding() for module in (execution_isa, lineage_isa)}
    for module in (execution_isa, lineage_isa):
        binding = module.binding()
        assert module.build_kernel().owner == binding.owner
        assert set(binding.handlers) == {
            name for name, declaration in declarations.items()
            if binding.owner in declaration.owners and not declaration.streaming
        }
    for name, owner in (("GetExecutionSettings", "execution"), ("UpdateExecutionSettings", "lineage")):
        assert declarations[name].owner == owner
        assert [candidate for candidate, binding in bindings.items() if name in binding.handlers] == [owner]


@pytest.fixture
def system(primitive_store, monkeypatch):
    lineage_id = make_lineage()["lineage_id"]
    monkeypatch.setattr(switches, "workflow_switches", lambda: {
        key: (lineage_id, field) for key, field in FIELDS.items()
    })
    monkeypatch.setattr("management_portal.permission_system.allows", lambda actor, instruction, arguments=None: actor == ACTOR)
    kernel = build_kernel()
    sequence = count()

    def call(name, arguments=None, *, key=None):
        return kernel.execute(InstructionCall.from_payload({
            "instruction": name, "arguments": arguments or {},
            "idempotency_key": key or f"switch-{next(sequence)}",
        }), InstructionContext(caller=ACTOR, entry="web"))

    return lineage_id, call, ExecutionSettings()


def install(system, key):
    lineage_id, call, _settings = system
    result = call("ApplyLineageChanges", {"lineage_id": lineage_id, "operations": [
        {"op": "create", "kind": "component", "ref": "switch", "value": {
            "kind": "event_source", "title": key, "client_key": key,
            "config": {"event_type": "manual.triggered"},
        }},
    ]}).raise_for_error()
    return result["refs"]["switch"]


def toggle(system, component_id, paused, *, key=None):
    lineage_id, call, _settings = system
    return call("PauseComponent", {
        "lineage_id": lineage_id, "component_id": component_id, "paused": paused,
    }, key=key)


@pytest.mark.parametrize("workflow", [TIMING, HARNESS])
def test_public_switch_projects_before_return_and_definition_edits_keep_pause(system, workflow):
    """暂停或恢复工作流组件在返回前同步开关，编辑组件定义不改变暂停状态"""
    lineage_id, call, settings = system
    component_id = install(system, workflow)
    field = FIELDS[workflow]
    revision = switches._definition(lineage_id)["revision"]
    assert settings.get()[field] is True
    assert workflow in settings.managed_workflow_keys()
    assert toggle(system, component_id, True).raise_for_error()["runtime"]["paused"] is True
    assert ExecutionSettings().get()[field] is False
    assert switches._definition(lineage_id)["revision"] == revision
    call("UpdateComponent", {"lineage_id": lineage_id, "component_id": component_id,
                             "changes": {"title": "Renamed while closed"}}).raise_for_error()
    assert settings.get()[field] is False
    assert toggle(system, component_id, False).raise_for_error()["runtime"]["paused"] is False
    assert settings.get()[field] is True


@pytest.mark.parametrize("workflow", [TIMING, HARNESS])
def test_delete_disables_setting_and_requires_restoring_event_before_direct_update(system, workflow):
    """删除工作流事件源后开关变为关闭，重新安装前 UpdateExecutionSettings 返回冲突"""
    lineage_id, call, settings = system
    component_id = install(system, workflow)
    call("DeleteComponent", {"lineage_id": lineage_id, "component_id": component_id}).raise_for_error()
    assert settings.get()[FIELDS[workflow]] is False
    for enabled in (True, False):
        result = call("UpdateExecutionSettings", {FIELDS[workflow]: enabled})
        assert result.status == "failed"
        assert result.error["code"] == "instruction.conflict"
        assert result.error["details"]["binding_state"] == "missing"
    replacement = install(system, workflow)
    assert replacement != component_id and settings.get()[FIELDS[workflow]] is True
    call("UpdateExecutionSettings", {FIELDS[workflow]: False}).raise_for_error()
    assert lineage_program_runtime.read_runtime(lineage_id)["event_sources"][replacement]["paused"] is True
    call("UpdateExecutionSettings", {FIELDS[workflow]: True}).raise_for_error()
    assert settings.get()[FIELDS[workflow]] is True


def test_uninstalled_settings_remain_compatible_and_both_fields_route_once_installed(system):
    """未安装时开关直接保存，安装后改为暂停事件源，任一绑定缺失则整个请求被拒绝"""
    lineage_id, call, settings = system
    assert catalog.instruction_catalog()["UpdateExecutionSettings"].owner == "lineage"
    assert catalog.instruction_catalog()["GetExecutionSettings"].owner == "execution"
    changes = {field: False for field in FIELDS.values()}
    assert switch_values(call("UpdateExecutionSettings", changes).raise_for_error()["settings"]) == changes
    assert settings.managed_workflow_keys() == set()
    switches.restore()
    assert switch_values(settings.get()) == changes
    components = [install(system, key) for key in FIELDS]
    call("UpdateExecutionSettings", changes).raise_for_error()
    states = lineage_program_runtime.read_runtime(lineage_id)["event_sources"]
    assert all(states[component_id]["paused"] for component_id in components)
    # Preflight the whole request: a missing binding must not partially update
    # another, still valid switch, nor a retention period sent with it.
    call("DeleteComponent", {"lineage_id": lineage_id, "component_id": components[1]}).raise_for_error()
    assert call("UpdateExecutionSettings", {**{field: True for field in FIELDS.values()},
                                            "harness_retention_days": 90}).status == "failed"
    assert settings.get() == {**changes, "harness_retention_days": 30, "permission_system_enabled": True}


def test_harness_retention_updates_without_switches(system):
    """Harness保留期独立校验并更新；不产生工作流开关或认领工作流key。"""
    _lineage_id, call, settings = system
    before = settings.get()
    result = call("UpdateExecutionSettings", {"harness_retention_days": 45})
    assert result.raise_for_error()["settings"] == {**before, "harness_retention_days": 45}
    assert settings.get() == {**before, "harness_retention_days": 45}
    assert settings.managed_workflow_keys() == set()


@pytest.mark.parametrize("arguments", [{}, {"model_request_timing_enabled": None},
                                       {"harness_recording_enabled": "false"},
                                       {"harness_retention_days": 7}, {"harness_retention_days": 400},
                                       {"harness_retention_days": "30"}, {"removed_resident_retention_days": 7},
                                       {"model_request_timing_enabled": False, "harness_retention_days": 0}])
def test_direct_settings_require_a_boolean_field(system, arguments):
    """UpdateExecutionSettings 缺少布尔开关或保留天数非法时拒绝，设置保持不变"""
    _lineage_id, call, settings = system
    before = settings.get()
    result = call("UpdateExecutionSettings", arguments)
    assert result.status == "failed" and result.error["code"] == "instruction.invalid_arguments"
    assert settings.get() == before


def test_direct_settings_keep_admin_check_without_adding_it_to_component_commands(system, monkeypatch):
    """直接调用 UpdateExecutionSettings 仍按该指令判定，暂停组件不额外要求它"""
    _lineage_id, call, settings = system
    component_id = install(system, TIMING)
    monkeypatch.setattr("management_portal.permission_system.allows", lambda actor, instruction, arguments=None: False)
    result = call("UpdateExecutionSettings", {FIELDS[TIMING]: False})
    assert result.status == "failed" and result.error["code"] == "instruction.forbidden"
    assert settings.get()[FIELDS[TIMING]] is True
    toggle(system, component_id, True).raise_for_error()
    assert settings.get()[FIELDS[TIMING]] is False


def test_recovery_and_delayed_definition_callbacks_read_current_runtime(system, monkeypatch):
    """恢复和延迟的定义回调按当前运行状态投影开关，旧回调不影响替换后的事件源"""
    lineage_id, call, settings = system
    component_id = install(system, TIMING)
    old = switches._definition(lineage_id)
    callbacks = []
    with monkeypatch.context() as patch:
        patch.setattr(invariant_locks, "defer_until_state_unlock", callbacks.append)
        switches.defer_definition_projection(lineage_id, old)
    toggle(system, component_id, True).raise_for_error()
    callbacks[0]()
    assert settings.get()[FIELDS[TIMING]] is False
    settings.project_workflow_switch(TIMING, True)  # stale projection on restart
    switches.restore()
    assert settings.get()[FIELDS[TIMING]] is False
    call("DeleteComponent", {"lineage_id": lineage_id, "component_id": component_id}).raise_for_error()
    settings.project_workflow_switch(TIMING, True)
    switches.restore()
    assert settings.get()[FIELDS[TIMING]] is False
    install(system, TIMING)
    callbacks[0]()  # An old callback cannot close the replacement Event.
    assert settings.get()[FIELDS[TIMING]] is True


def test_state_locks_release_before_projection_and_competing_toggles_serialize(system, monkeypatch):
    """投影开关前释放状态锁，并发的暂停与恢复按先后顺序生效

    :importance: P1"""
    _lineage_id, _call, settings = system
    component_id = install(system, TIMING)
    original = ExecutionSettings.project_workflow_switch
    reading, release, resumed = threading.Event(), threading.Event(), threading.Event()

    def project(self, key, enabled):
        assert not invariant_locks.lock_held()
        if key == TIMING and not enabled:
            reading.set()
            assert release.wait(5)
        return original(self, key, enabled)

    monkeypatch.setattr(ExecutionSettings, "project_workflow_switch", project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        closing = pool.submit(toggle, system, component_id, True)
        assert reading.wait(5)

        def reopen():
            result = toggle(system, component_id, False)
            resumed.set()
            return result

        opening = pool.submit(reopen)
        try:
            assert not resumed.wait(0.1)
        finally:
            release.set()
        closing.result(timeout=5).raise_for_error()
        opening.result(timeout=5).raise_for_error()
    assert settings.get()[FIELDS[TIMING]] is True


def test_projection_retry_reuses_receipt_and_does_not_replay_old_pause(system, monkeypatch):
    """开关投影失败后用同一幂等键重试复用原回执，不重放旧的暂停"""
    lineage_id, _call, settings = system
    component_id = install(system, TIMING)
    original = ExecutionSettings.project_workflow_switch
    failures = [True]

    def project(self, key, enabled):
        if failures:
            failures.pop()
            raise RuntimeError("projection database temporarily unavailable")
        return original(self, key, enabled)

    monkeypatch.setattr(ExecutionSettings, "project_workflow_switch", project)
    first = toggle(system, component_id, True, key="same-close")
    assert first.status == "failed" and first.error["commit_state"] == "committed"
    assert first.error["code"] == "instruction.projection_pending"
    toggle(system, component_id, False).raise_for_error()
    toggle(system, component_id, True, key="same-close").raise_for_error()
    assert settings.get()[FIELDS[TIMING]] is True
    assert not lineage_program_runtime.read_runtime(lineage_id)["event_sources"][component_id].get("paused")


def test_missing_lineage_recovery_closes_only_previously_installed_setting(system, monkeypatch):
    """工作流 Lineage 缺失时，恢复只关闭曾经安装过的开关"""
    _lineage_id, _call, settings = system
    settings.project_workflow_switch(TIMING, True)
    monkeypatch.setattr(switches, "workflow_switches", lambda: {
        key: ("lineage_000000000099", field) for key, field in FIELDS.items()
    })
    switches.restore()
    assert switch_values(settings.get()) == {FIELDS[TIMING]: False, FIELDS[HARNESS]: True}
    assert settings.managed_workflow_keys() == {TIMING}
