from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.test_framework import tau, tau_runtime
from management_portal.test_framework.tau_residents import TAU_ACTOR, TauResidentAssignments
from management_portal.society_service.contracts import DOMAIN_METHODS


def test_tau_configuration_uses_current_keys_and_explicit_storage(tmp_path, monkeypatch):
    """TAU 只读当前环境键，保留显式凭据隔离路径，不回退旧代理配置。"""
    for key in ("ENABLED", "PROVIDER", "MODEL"):
        monkeypatch.delenv(f"PORTAL_TEST_TAU_{key}", raising=False)
    monkeypatch.setenv("PORTAL_TEST_AGENT_USE_ENABLED", "false")
    monkeypatch.setenv("PORTAL_TEST_AGENT_PROVIDER", "retired-provider")
    monkeypatch.setenv("PORTAL_TEST_AGENT_MODEL", "retired-model")
    storage = tmp_path / "isolated-storage.json"
    monkeypatch.setenv("PORTAL_TEST_STORAGE_STATE", str(storage))
    config = tau.TauConfig.from_environment()
    assert (config.enabled, config.provider, config.model) == (True, "codex", "")
    assert config.storage_state == storage
    assert not storage.exists()

    monkeypatch.setenv("PORTAL_TEST_TAU_ENABLED", "off")
    monkeypatch.setenv("PORTAL_TEST_TAU_PROVIDER", " current-provider ")
    monkeypatch.setenv("PORTAL_TEST_TAU_MODEL", " current-model ")
    config = tau.TauConfig.from_environment()
    assert (config.enabled, config.provider, config.model) == (False, "current-provider", "current-model")
    assert config.storage_state == storage
    assert not storage.exists()


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from management_portal import resident_handoff
    monkeypatch.setattr(resident_handoff, "release_resident", mock.Mock())
    assignments = TauResidentAssignments(tmp_path / "tau-residents.json")
    assignments.record_identity("workbench.panels", resident_id="resident_0123456789ab", lineage_id="lineage_0123456789ab")
    original = assignments.assignment("workbench.panels")
    assignment = assignments.configure(["workbench.panels"], backend_route_id="five", service_tier="priority", changed_by="resident_aaaaaaaaaaaa")[0]
    state = SimpleNamespace(
        options={"source": {"provider": "codex", "account_id": "one", "generation": "source-generation"}, "restart": {}},
        runtime={"service_tier": ""},
        conversation={"intake_state": "open", "work_state": "idle"},
    )
    resident = mock.Mock(spec=list(DOMAIN_METHODS["resident"]))
    resident.get_resident_restart_options.side_effect = lambda *_args: state.options
    resident.get_resident_state.side_effect = lambda *_args: {"state": state.conversation}

    def restart(_actor, arguments, _key, _version):
        state.options["restart"] = {"status": "accepted", "target": {"account_id": arguments["account_id"]}}
        return state.options["restart"]

    # Instructions go through the ISA; each mock records (actor, arguments, key, version).
    restart_session = mock.Mock(side_effect=restart)
    update_settings = mock.Mock(side_effect=lambda _actor, arguments, *_: state.runtime.update(arguments["changes"]))
    isa = {
        "RestartSession": restart_session,
        "GetResidentSettings": lambda *_args: {"settings": dict(state.runtime)},
        "UpdateResidentSettings": update_settings,
        "CreateLineage": mock.Mock(side_effect=AssertionError("unexpected CreateLineage")),
    }

    def call(actor, instruction, arguments, *, key="", version=None, entry="web", run_id=""):
        return isa[instruction](actor, arguments, key, version)

    from management_portal import isa_client

    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(tau_runtime, "get_society_service", lambda: SimpleNamespace(resident=resident))
    monkeypatch.setattr(tau_runtime, "reserved_account_quota", lambda _route: None)
    monkeypatch.setattr(tau_runtime, "resolve_application_lineage", lambda **_: "lineage_0123456789ab")
    return SimpleNamespace(assignments=assignments, assignment=assignment, original=original, state=state,
                           resident=resident, restart=restart_session, update_settings=update_settings, isa=isa)


@pytest.mark.parametrize(("ready", "rebound"), [(True, False), (False, False), (True, True)])
def test_tau_checks_actual_business_binding_before_dispatch(configured, monkeypatch, ready, rebound):
    """TAU 发送前核对实际 Resident 绑定，配置未就绪或已换绑时不发送"""
    from management_portal import lineage_workflows, resident_handoff

    identity = {"resident_id": "resident_abcdefabcdef", "lineage_id": "lineage_abcdefabcdef"}
    bindings = {"resident_binding": identity, "workflow_binding": {"event_source_id": "event_source_probe"}}
    stages = []

    def ensure(assignments, module_id, **options):
        assert assignments is configured.assignments
        assert module_id == "workbench.panels"
        assert options["provider"] == "codex"
        stages.append("resolved")
        return assignments.record_identity(module_id, **identity)

    def prepare(assignment, **options):
        assert assignment["resident_id"] == identity["resident_id"]
        assert assignment["backend_route_id"] == configured.assignment["backend_route_id"]
        assert options["refresh_assignment"]()["resident_id"] == identity["resident_id"]
        assert stages == ["resolved"]
        stages.append("prepared")
        return {"ready": ready, "resident_id": "resident_ffffffffffff" if rebound else identity["resident_id"]}

    invoke = mock.Mock(return_value={"status": "completed"})
    monkeypatch.setattr(tau_runtime, "ensure_module_resident", ensure)
    monkeypatch.setattr(tau_runtime, "prepare_configuration", prepare)
    monkeypatch.setattr(
        lineage_workflows.WorkflowBindings,
        "install",
        lambda self, **kwargs: bindings["workflow_binding"],
    )
    monkeypatch.setattr(resident_handoff, "run_resident_turn", invoke)
    options = {"actor": TAU_ACTOR, "provider": "codex", "prompt": "probe", "metadata": {"test_module_id": "workbench.panels"}}
    if ready and not rebound:
        assert tau_runtime.invoke_configured_resident(configured.assignments, **options) == {"status": "completed"}
        invoke.assert_called_once_with(**options, **bindings)
    else:
        with pytest.raises(InterruptedError, match="未发送用例"):
            tau_runtime.invoke_configured_resident(configured.assignments, **options)
        invoke.assert_not_called()
    assert stages == ["resolved", "prepared"]
    assert configured.assignments.assignment("workbench.panels")["resident_id"] == identity["resident_id"]


@pytest.mark.parametrize("sticks", [True, False])
def test_tau_campaign_runtime_is_applied_and_verified_before_dispatch(configured, monkeypatch, sticks):
    """运行指定的 Resident 模型设置在发送前写入并核对，未生效时不发送用例

    :aspects: functional, behavior"""
    from management_portal import lineage_workflows, resident_handoff

    identity = {"resident_id": "resident_abcdefabcdef", "lineage_id": "lineage_abcdefabcdef"}
    settings = {"model": "gpt-6-astra", "reasoning_effort": "low", "permission_preset": "default", "service_tier": ""}
    runtime = {"model": "gpt-5.6-sol", "reasoning_effort": "medium", "permission_preset": "full-access"}

    def update(_actor, arguments, _key, _version):
        assert arguments == {"resident_id": identity["resident_id"], "changes": runtime}
        if sticks:
            settings.update(arguments["changes"])
        return {"settings": dict(settings)}

    configured.isa["GetResidentSettings"] = mock.Mock(side_effect=lambda *_args: {"settings": dict(settings)})
    configured.isa["UpdateResidentSettings"] = mock.Mock(side_effect=update)
    invoke = mock.Mock(return_value={"status": "completed"})
    monkeypatch.setattr(tau_runtime, "ensure_module_resident",
                        lambda assignments, module_id, **_: assignments.record_identity(module_id, **identity))
    monkeypatch.setattr(tau_runtime, "prepare_configuration",
                        lambda assignment, **_: {"ready": True, "resident_id": assignment["resident_id"]})
    monkeypatch.setattr(lineage_workflows.WorkflowBindings, "install", lambda self, **_: {"event_source_id": "event_source_probe"})
    monkeypatch.setattr(resident_handoff, "run_resident_turn", invoke)
    options = {"actor": TAU_ACTOR, "provider": "codex", "prompt": "probe", "metadata": {"test_module_id": "workbench.panels"}}

    if sticks:
        tau_runtime.invoke_configured_resident(configured.assignments, **options, resident_runtime=runtime)
        assert "resident_runtime" not in invoke.call_args.kwargs
        assert {key: settings[key] for key in runtime} == runtime
    else:
        with pytest.raises(InterruptedError, match="未发送用例"):
            tau_runtime.invoke_configured_resident(configured.assignments, **options, resident_runtime=runtime)
        invoke.assert_not_called()
    configured.isa["UpdateResidentSettings"].assert_called_once()


def test_tau_prompt_carries_the_campaign_resident_runtime(tmp_path):
    """TAU 提示词写明运行指定的模型、推理强度和权限预设，未指定时要求保留原设置

    :aspects: functional, behavior"""
    storage = tmp_path / "test-actor.json"
    storage.write_text("{}", encoding="utf-8")
    storage.chmod(0o600)
    planned = {
        "case_id": "test_000000000009", "case_title": "t", "description": "d", "module": "workbench.panels",
        "aspects": [], "implementation": {"task_prompt": "检查页面。"}, "resident_account_id": "one",
    }
    from tests.cases.test_test_framework import _verified_dev_target

    plain = tau.build_prompt(planned, tau.TauConfig(storage), tmp_path / "evidence", _verified_dev_target(), "testrun_0000000000000003")
    pinned = tau.build_prompt(
        {**planned, "resident_runtime": {"model": "gpt-5.6-sol", "reasoning_effort": "medium", "permission_preset": "full-access"}},
        tau.TauConfig(storage), tmp_path / "evidence", _verified_dev_target(), "testrun_0000000000000003",
    )
    assert "保留原模型与速度设置" in plain
    # Executors judged loading regions as missing, empty or stuck within a second.
    assert "该区域尚无结论" in plain and "严格模式错误属于辅助脚本错误" in plain
    assert "保留原模型与速度设置" not in pinned
    assert "模型 gpt-5.6-sol、推理强度 medium、权限预设 full-access" in pinned
    # Fork offers no account or model choice and keeps the source's settings,
    # and WALL starts the members it creates itself: neither is a failure.
    assert "不因页面没有选择器判定失败" in pinned
    assert "不以上述约束判定失败" in pinned
    assert "不要记为覆盖缺口或失败" in pinned and "本用例无权修改的共享业务成员" in pinned


def test_tau_reuses_fixed_module_resident_without_creation(configured, monkeypatch):
    """TAU 复用模块固定的 Resident，不新建 Lineage 或 Resident"""
    from management_portal import resident_handoff

    configured.resident.get_resident.return_value = {
        "resident_id": configured.assignment["resident_id"],
        "lineage_id": configured.assignment["lineage_id"],
    }
    create_lineage = configured.isa["CreateLineage"]
    create_resident = mock.Mock(side_effect=AssertionError("fixed TAU Resident must be reused"))
    monkeypatch.setattr(resident_handoff, "ensure_invocation_resident", create_resident)

    first = tau_runtime.ensure_module_resident(configured.assignments, "workbench.panels")
    second = tau_runtime.ensure_module_resident(configured.assignments, "workbench.panels")

    assert first == configured.assignment
    assert second == configured.assignment
    assert configured.resident.get_resident.call_count == 2
    create_lineage.assert_not_called()
    create_resident.assert_not_called()


@pytest.mark.parametrize("failure", ["busy", "in_progress", "forbidden", "cancelled"])
def test_tau_binding_recovery_preserves_intent_before_first_send(configured, monkeypatch, failure):
    """TAU 创建回执恢复保持原任务且可取消

    绑定服务繁忙或 Instruction 执行中时恢复同一创建意图；权限拒绝与取消正确终结，首次消息只发送一次。

    :importance: P1
    :aspects: functional, behavior
    """
    from management_portal import resident_handoff, lineage_workflows
    from management_portal.society_service import SocietyServiceBusy
    from management_portal.hatg.contracts import InstructionError

    configured.assignments.rotate(
        "workbench.panels",
        changed_by=TAU_ACTOR,
        backend_route_id="five",
    )
    configured.assignments.configure(
        ["workbench.panels"],
        backend_route_id="five",
        service_tier="priority",
        changed_by=TAU_ACTOR,
    )
    attempts = []
    stopped = False

    def create_resident(**arguments):
        attempts.append(dict(arguments))
        assert arguments["actor"] == TAU_ACTOR
        if len(attempts) == 1:
            if failure in {"busy", "cancelled"}:
                raise SocietyServiceBusy("response lost")
            raise InstructionError("instruction.in_progress" if failure == "in_progress" else "instruction.forbidden", "pending", http_status=409)
        return {"resident_id": "resident_abcdefabcdef", "lineage_id": arguments["lineage_binding"]}

    def sleep(_delay):
        nonlocal stopped
        stopped = failure == "cancelled"

    invoke = mock.Mock(return_value={"status": "completed"})
    monkeypatch.setattr(resident_handoff, "ensure_invocation_resident", create_resident)
    monkeypatch.setattr(lineage_workflows.WorkflowBindings, "install", lambda self, **_: {"event_source_id": "original-event"})
    monkeypatch.setattr(tau_runtime.time, "sleep", sleep)
    monkeypatch.setattr(tau_runtime, "prepare_configuration", lambda assignment, **_: {"ready": True, "resident_id": assignment["resident_id"]})
    monkeypatch.setattr(resident_handoff, "run_resident_turn", invoke)
    options = dict(actor=TAU_ACTOR, provider="codex", business_ref="testrun_original:test_original", metadata={"test_module_id": "workbench.panels"}, cancellation_requested=lambda: stopped)
    if failure in {"forbidden", "cancelled"}:
        with pytest.raises(InstructionError if failure == "forbidden" else InterruptedError):
            tau_runtime.invoke_configured_resident(configured.assignments, **options)
        assert len(attempts) == 1
        invoke.assert_not_called()
    else:
        tau_runtime.invoke_configured_resident(configured.assignments, **options)
        assert attempts[0] == attempts[1]
        configured.isa["CreateLineage"].assert_not_called()
        invoke.assert_called_once()
        assert invoke.call_args.kwargs["business_ref"] == options["business_ref"]


def test_tau_configuration_preserves_identity_and_is_idempotent(configured):
    """重复配置同一模块不改变 TAU Resident、Lineage 和槽位"""
    repeated = configured.assignments.configure(["workbench.panels", "workbench.panels"], backend_route_id="five", service_tier="priority", changed_by="resident_aaaaaaaaaaaa")
    assert repeated == [configured.assignment]
    for field in ("resident_id", "lineage_id", "slot_id"):
        assert configured.assignment[field] == configured.original[field]


def test_tau_configuration_migrates_with_generation_and_stable_request(configured):
    """账号不同时 TAU 配置以固定请求键和源代次重启 Resident，重复配置不再重启"""
    result = tau_runtime.apply_configuration(configured.assignment)
    assert result["status"] == "migrating"
    assert result["ready"] is False
    call = configured.restart.call_args
    resident_id = configured.assignment["resident_id"]
    assert call.args == (TAU_ACTOR, {
        "resident_id": resident_id, "provider": "codex", "account_id": "five",
        "context_strategy": "smart", "active_execution_policy": "wait",
    }, f"tau-config:{configured.assignment['configuration_id']}:{resident_id}", {resident_id: "source-generation"})
    assert configured.state.runtime["service_tier"] == "priority"
    tau_runtime.apply_configuration(configured.assignment)
    configured.restart.assert_called_once()


def test_tau_configuration_updates_fast_without_restarting_matching_account(configured):
    """账号相同时 TAU 配置直接就绪，不重启 Resident"""
    configured.state.options["source"]["account_id"] = "five"
    assert tau_runtime.apply_configuration(configured.assignment)["ready"] is True
    configured.restart.assert_not_called()


def test_tau_configuration_restarts_closed_matching_account(configured):
    """账号相同但 Resident 已关闭时，TAU 配置仍发起重启"""
    configured.state.options["source"]["account_id"] = "five"
    configured.state.conversation["intake_state"] = "closed"

    assert tau_runtime.resident_status(configured.assignment)["status"] == "closed"
    result = tau_runtime.apply_configuration(configured.assignment)

    assert result["status"] == "migrating"
    assert result["ready"] is False
    call = configured.restart.call_args
    resident_id = configured.assignment["resident_id"]
    assert call.args == (TAU_ACTOR, {
        "resident_id": resident_id, "provider": "codex", "account_id": "five",
        "context_strategy": "smart", "active_execution_policy": "wait",
    }, f"tau-config:{configured.assignment['configuration_id']}:{resident_id}:closed:source-generation",
        {resident_id: "source-generation"})


def test_tau_configuration_reports_current_settings_after_migration(configured):
    """账号迁移完成后 TAU 状态报告实际账号和速度"""
    tau_runtime.apply_configuration(configured.assignment)
    configured.state.options["source"]["account_id"] = "five"
    configured.state.options["restart"]["status"] = "completed"
    status = tau_runtime.resident_status(configured.assignment)
    assert status["ready"] is True
    assert status["actual_account"] == "five"
    assert status["actual_service_tier"] == "priority"
    assert status["restart"] == {}


def test_tau_configuration_does_not_replace_conflicting_migration(configured):
    """Resident 正在迁移到其他账号时，TAU 配置报错且不覆盖该迁移"""
    configured.state.options["restart"] = {"status": "accepted", "target": {"account_id": "three"}}
    with pytest.raises(ValueError, match="其他账号"):
        tau_runtime.apply_configuration(configured.assignment)
    configured.update_settings.assert_not_called()
    configured.restart.assert_not_called()


def test_tau_configuration_for_future_resident_keeps_creation_settings(configured):
    """尚未创建 Resident 的模块配置为待创建，保留所配账号"""
    assignment = configured.assignments.configure(["resident.conversation"], backend_route_id="five", service_tier="priority", changed_by="resident_aaaaaaaaaaaa")[0]
    status = tau_runtime.apply_configuration(assignment)
    assert status["status"] == "pending_create"
    assert status["ready"] is True
    assert status["backend_route_id"] == "five"
    configured.resident.get_resident_restart_options.assert_not_called()


def test_tau_deleted_resident_status_is_recoverable(configured):
    """TAU Resident 已删除时状态报告为 stale 且未就绪"""
    configured.resident.get_resident_restart_options.side_effect = KeyError(configured.assignment["resident_id"])
    status = tau_runtime.resident_status(configured.assignment)
    assert status["status"] == "stale"
    assert status["ready"] is False
    assert status["resident_id"] == configured.assignment["resident_id"]


def test_tau_preparation_replaces_deleted_resident_and_preserves_configuration(configured):
    """TAU Resident 被删除时准备阶段换成待创建的新槽位并保留配置"""
    deleted_id = configured.assignment["resident_id"]
    configured.resident.get_resident_restart_options.side_effect = KeyError(deleted_id)
    output = []

    def recover(stale):
        replacement = configured.assignments.replace_unusable_resident(
            "workbench.panels",
            expected_resident_id=stale["resident_id"],
            changed_by="resident_aaaaaaaaaaaa",
        )
        configured.resident.get_resident_restart_options.side_effect = None
        return replacement

    status = tau_runtime.prepare_configuration(
        configured.assignment,
        cancelled=lambda: False,
        emit=output.append,
        poll_interval=0,
        refresh_assignment=lambda: configured.assignments.assignment("workbench.panels"),
        replace_unusable_assignment=recover,
    )
    replacement = configured.assignments.assignment("workbench.panels")
    assert status["status"] == "pending_create"
    assert status["ready"] is True
    assert replacement["resident_id"] == ""
    assert replacement["lineage_id"] == ""
    assert replacement["slot_id"] != configured.assignment["slot_id"]
    assert replacement["backend_route_id"] == configured.assignment["backend_route_id"]
    assert replacement["runtime_options"] == configured.assignment["runtime_options"]
    assert replacement["configuration_id"] == configured.assignment["configuration_id"]
    assert any("已被删除" in message for message in output)


def test_tau_deleted_resident_recovery_does_not_replace_a_newer_identity(configured):
    """替换已删除的 TAU Resident 时，模块已有更新的身份则保留新身份"""
    configured.assignments.record_identity(
        "workbench.panels",
        resident_id="resident_fedcbafedcba",
        lineage_id="lineage_fedcbafedcba",
    )
    current = configured.assignments.replace_unusable_resident(
        "workbench.panels",
        expected_resident_id=configured.assignment["resident_id"],
        changed_by="resident_aaaaaaaaaaaa",
    )
    assert current["resident_id"] == "resident_fedcbafedcba"
    assert current["lineage_id"] == "lineage_fedcbafedcba"


def test_tau_preparation_replaces_closed_resident_when_restart_is_a_noop(configured):
    """已关闭的 TAU Resident 重启无效时换成待创建的新槽位，保留账号与速度"""
    configured.state.options["source"]["account_id"] = "five"
    configured.state.conversation["intake_state"] = "closed"
    configured.restart.side_effect = lambda *_args: {}
    output = []

    def replace(unusable):
        return configured.assignments.replace_unusable_resident(
            "workbench.panels",
            expected_resident_id=unusable["resident_id"],
            changed_by="resident_aaaaaaaaaaaa",
        )

    result = tau_runtime.prepare_configuration(
        configured.assignment,
        cancelled=lambda: False,
        emit=output.append,
        poll_interval=0,
        refresh_assignment=lambda: configured.assignments.assignment("workbench.panels"),
        replace_unusable_assignment=replace,
    )

    replacement = configured.assignments.assignment("workbench.panels")
    assert result["status"] == "pending_create"
    assert result["ready"] is True
    assert replacement["resident_id"] == ""
    assert replacement["slot_id"] != configured.assignment["slot_id"]
    assert replacement["backend_route_id"] == "five"
    assert replacement["runtime_options"] == {"service_tier": "priority"}
    assert any("已关闭且无法重启" in message for message in output)


def test_tau_preparation_waits_for_identical_restart_instruction(configured):
    """相同的重启指令正在执行时，TAU 准备等待其完成"""
    from management_portal.hatg.contracts import InstructionError

    configured.state.options["source"]["account_id"] = "five"
    configured.state.conversation["intake_state"] = "closed"
    configured.restart.side_effect = InstructionError(
        "instruction.in_progress",
        "相同 Instruction 正在执行",
        http_status=409,
    )
    output = []

    def emit(message):
        output.append(message)
        if "instruction_in_progress" in message:
            configured.state.conversation["intake_state"] = "open"

    result = tau_runtime.prepare_configuration(
        configured.assignment,
        cancelled=lambda: False,
        emit=emit,
        poll_interval=0,
    )

    assert result["ready"] is True
    assert any("instruction_in_progress" in message for message in output)


def test_tau_preparation_waits_while_a_lifecycle_operation_finishes(configured):
    """TAU 账号准备等待 Resident 生命周期操作收尾

    RestartSession 因上一次重启仍在恢复 Resident（resident.session_restart_in_progress）被拒绝时，TAU 继续等待而不是让整轮失败；其他冲突照常报错。

    :aspects: functional, behavior
    """
    from management_portal.hatg.contracts import InstructionError

    configured.state.options["source"]["account_id"] = "five"
    configured.state.conversation["intake_state"] = "closed"
    # A restart reported completed while its lifecycle operation still resumes the Resident.
    configured.restart.side_effect = InstructionError(
        "instruction.conflict",
        "Resident 生命周期操作正在完成：three → three。本次新迁移未执行，请查看当前进度。",
        http_status=409,
        details={"reason": "resident.session_restart_in_progress"},
    )
    output = []

    def emit(message):
        output.append(message)
        if "lifecycle_finishing" in message:
            configured.state.conversation["intake_state"] = "open"

    result = tau_runtime.prepare_configuration(
        configured.assignment, cancelled=lambda: False, emit=emit, poll_interval=0,
    )
    assert result["ready"] is True
    assert any("lifecycle_finishing" in message for message in output)

    configured.state.conversation["intake_state"] = "closed"
    configured.restart.side_effect = InstructionError("instruction.conflict", "其他冲突", http_status=409)
    with pytest.raises(InstructionError):
        tau_runtime.prepare_configuration(
            configured.assignment, cancelled=lambda: False, emit=output.append, poll_interval=0,
        )


def test_tau_adapter_never_sends_before_account_configuration_is_ready(configured, tmp_path, monkeypatch):
    """账号配置就绪前运行被取消时，TAU 适配器不发送用例"""
    from management_portal.test_framework.dev_target import VerifiedDevTarget
    from management_portal.test_framework.runner_adapters import TauAdapter
    from management_portal.test_framework.tau import TauConfig

    config = TauConfig(tmp_path / "storage.json")
    monkeypatch.setattr(TauConfig, "unavailable_reason", lambda _self: "")
    invoke = mock.Mock()
    target = VerifiedDevTarget(
        "dev",
        "http://portal.test",
        "dev-current",
        "dev-current",
        "component-v2",
        "test-actor",
    )
    adapter = TauAdapter(
        config,
        invoke,
        lambda _storage: target,
        assignments=configured.assignments,
    )
    result = adapter.execute(
        run_id="testrun_0123456789abcdef", planned={"module": "workbench.panels"},
        run_dir=tmp_path, actor="resident_aaaaaaaaaaaa", emit=mock.Mock(), cancelled=lambda: True,
    )
    assert result.status == "cancelled"
    invoke.assert_not_called()


@pytest.mark.parametrize(("module_ids", "fast"), [([], True), (["unknown"], True), (["workbench.panels"], "true")])
def test_tau_configuration_validates_request_before_changing_assignments(configured, module_ids, fast):
    """TAU 配置的模块为空、未知或 fast 不是布尔值时拒绝，分配保持不变"""
    from management_portal.test_framework.service import TestFrameworkService

    service = object.__new__(TestFrameworkService)
    service.tau_residents = configured.assignments
    with pytest.raises(ValueError):
        service.configure_tau_residents(module_ids, actor="resident_aaaaaaaaaaaa", backend_route_id="five", fast=fast)
    assert configured.assignments.assignment("workbench.panels") == configured.assignment


def test_tau_configuration_rejects_active_campaign_before_mutation(configured, monkeypatch):
    """模块有 TAU 运行进行中时拒绝切换账号，配置保持不变"""
    from management_portal.execution.account_adapters import CodexRoutes
    from management_portal.test_framework.service import TestFrameworkService

    monkeypatch.setattr(CodexRoutes, "get", lambda _self, _route: SimpleNamespace(account_id="three"))
    service = object.__new__(TestFrameworkService)
    service.tau_residents = configured.assignments
    service.records = mock.Mock()
    service.records.recent.return_value = [{"status": "running", "plan": {"selected": [{"module": "workbench.panels", "runner": {"id": "tau"}}]}}]
    with pytest.raises(ValueError, match="运行中"):
        service.configure_tau_residents(["workbench.panels"], actor="resident_aaaaaaaaaaaa", backend_route_id="three", fast=False)
    assert configured.assignments.assignment("workbench.panels") == configured.assignment
    configured.update_settings.assert_not_called()


@pytest.mark.parametrize("run_status", ["queued", "running", "cancelling"])
def test_tau_configuration_changes_live_speed_without_changing_account_or_session(configured, run_status):
    """模块有 TAU 运行排队或进行中时，配置只更新速度，不改账号也不重启"""
    from management_portal.test_framework.service import TestFrameworkService

    service = object.__new__(TestFrameworkService)
    service.tau_residents = configured.assignments
    service.records = mock.Mock()
    service.records.recent.return_value = [{"status": run_status, "plan": {"selected": [{"module": "workbench.panels", "runner": {"id": "tau"}}]}}]
    configured.state.runtime["service_tier"] = "priority"
    result = service.configure_tau_residents(["workbench.panels"], actor="resident_aaaaaaaaaaaa", backend_route_id="five", fast=False)
    assert result["status"] == "configured"
    assert result["runtime_updates"][0]["status"] == "applied"
    assert result["runtime_updates"][0]["actual_service_tier"] == ""
    current = result["residents"][0]
    assert current["backend_route_id"] == "five"
    assert current["runtime_options"] == {"service_tier": ""}
    for field in ("resident_id", "lineage_id", "slot_id"):
        assert current[field] == configured.assignment[field]
    configured.restart.assert_not_called()
    configured.resident.get_resident_restart_options.assert_not_called()


def test_tau_configuration_reports_live_update_failure_and_allows_retry(configured):
    """TAU 速度更新失败时返回部分配置状态，重试后完成且不重启"""
    from management_portal.test_framework.service import TestFrameworkService

    service = object.__new__(TestFrameworkService)
    service.tau_residents = configured.assignments
    service.records = mock.Mock()
    service.records.recent.return_value = []
    configured.update_settings.side_effect = RuntimeError("temporarily unavailable")
    result = service.configure_tau_residents(["workbench.panels"], actor="resident_aaaaaaaaaaaa", backend_route_id="five", fast=False)
    assert result["status"] == "partially_configured"
    assert result["runtime_updates"][0]["error"] == "temporarily unavailable"
    assert result["residents"][0]["runtime_options"] == {"service_tier": ""}
    configured.update_settings.side_effect = lambda _actor, arguments, *_: configured.state.runtime.update(arguments["changes"])
    retried = service.configure_tau_residents(["workbench.panels"], actor="resident_aaaaaaaaaaaa", backend_route_id="five", fast=False)
    assert retried["status"] == "configured"
    assert retried["residents"] == result["residents"]
    configured.restart.assert_not_called()


def test_tau_preparation_reconciles_speed_changed_during_configuration(configured):
    """TAU 准备期间速度配置被改动时，按最新配置收敛"""
    configured.state.options["source"]["account_id"] = "five"

    def update(_actor, arguments, *_):
        configured.state.runtime.update(arguments["changes"])
        if arguments["changes"]["service_tier"] == "priority":
            configured.assignments.configure(["workbench.panels"], backend_route_id="five", service_tier="", changed_by="resident_aaaaaaaaaaaa")

    configured.update_settings.side_effect = update
    result = tau_runtime.prepare_configuration(
        configured.assignment, cancelled=lambda: False, emit=mock.Mock(), poll_interval=0,
        refresh_assignment=lambda: configured.assignments.assignment("workbench.panels"),
    )
    assert result["ready"] is True
    assert result["actual_service_tier"] == ""
    assert configured.state.runtime["service_tier"] == ""
    configured.restart.assert_not_called()


def test_tau_adapter_preserves_live_speed_when_sending_existing_resident(configured, tmp_path, monkeypatch):
    """向已有 TAU Resident 发送用例时采用最新速度配置，运行参数不带 service_tier"""
    from management_portal.test_framework import runner_adapters

    configured.state.options["source"]["account_id"] = "five"
    configured.state.runtime["service_tier"] = "priority"

    def prepare(_assignment, **_kwargs):
        latest = configured.assignments.configure(["workbench.panels"], backend_route_id="five", service_tier="", changed_by="resident_aaaaaaaaaaaa")[0]
        tau_runtime.apply_runtime_options(latest)
        return {"ready": True}

    monkeypatch.setattr(tau_runtime, "prepare_configuration", prepare)
    monkeypatch.setattr(tau.TauConfig, "unavailable_reason", lambda _self: "")
    prompt = mock.Mock(return_value="test prompt")
    monkeypatch.setattr(runner_adapters, "build_prompt", prompt)
    target = mock.Mock(instance_id="dev", actual_build_id="dev-test", actor=TAU_ACTOR)
    target.result_details.return_value = {}
    invoke = mock.Mock(return_value={
        "resident_id": configured.assignment["resident_id"], "lineage_id": configured.assignment["lineage_id"],
        "message": 'PORTAL_TAU_RESULT: {"status":"passed","summary":"done","issues":[],"evidence":[]}',
    })
    adapter = runner_adapters.TauAdapter(tau.TauConfig(tmp_path / "storage.json"), invoke, lambda _storage: target, assignments=configured.assignments)
    result = adapter.execute(
        run_id="testrun_0123456789abcdef", planned={"module": "workbench.panels", "case_id": "test_0123456789ab",
                                                  "implementation": {"task_prompt": "检查速度配置。"}},
        run_dir=tmp_path, actor="resident_aaaaaaaaaaaa", emit=mock.Mock(), cancelled=lambda: False,
    )
    assert result.status == "passed", result.details
    invoke.assert_called_once()
    assert "service_tier" not in invoke.call_args.kwargs["runtime_options"]
    from management_portal.society_service.resident_runtime_payload import runtime_settings_updates_from_payload
    settings = runtime_settings_updates_from_payload(invoke.call_args.kwargs["runtime_options"])
    assert settings["permission_preset"] == "full-access"
    assert prompt.call_args.kwargs["assignment"]["runtime_options"] == {"service_tier": ""}
    assert configured.state.runtime["service_tier"] == ""


def test_tau_configuration_acknowledges_without_waiting_for_provider(configured):
    """TAU 快速配置立即确认，不等待查询 Resident 重启选项"""
    from management_portal.test_framework.service import TestFrameworkService

    service = object.__new__(TestFrameworkService)
    service.tau_residents = configured.assignments
    service.records = mock.Mock()
    service.records.recent.return_value = []
    result = service.configure_tau_residents(["workbench.panels"], actor="resident_aaaaaaaaaaaa", backend_route_id="five", fast=True)
    assert result["status"] == "configured"
    assert result["residents"][0] == configured.assignment
    configured.resident.get_resident_restart_options.assert_not_called()


def test_tau_preparation_waits_for_migration_and_reports_progress(configured):
    """TAU 准备等待账号迁移完成并输出配置进度"""
    output = []

    def emit(text):
        output.append(text)
        if "配置进度" in text:
            configured.state.options["source"]["account_id"] = "five"
            configured.state.options["restart"]["status"] = "completed"

    result = tau_runtime.prepare_configuration(configured.assignment, cancelled=lambda: False, emit=emit, poll_interval=0)
    assert result["ready"] is True
    assert any("配置进度" in text for text in output)
    configured.restart.assert_called_once()


def test_tau_preparation_stops_on_failed_migration(configured):
    """Resident 账号迁移失败时 TAU 准备报错停止"""
    def fail(_actor, _arguments, _key, _version):
        configured.state.options["restart"] = {"status": "failed", "error": "handoff failed"}

    configured.restart.side_effect = fail
    with pytest.raises(RuntimeError, match="handoff failed"):
        tau_runtime.prepare_configuration(configured.assignment, cancelled=lambda: False, emit=mock.Mock(), poll_interval=0)


def test_tau_preparation_reconciles_after_dependency_recovers_without_restarting(configured):
    """依赖服务重启期间 TAU 准备等待其恢复，恢复后直接就绪不重启"""
    from management_portal.society_service import SocietyServiceUnavailable

    configured.state.options["source"]["account_id"] = "five"
    configured.state.runtime["service_tier"] = "priority"
    configured.resident.get_resident_restart_options.side_effect = [SocietyServiceUnavailable("restarting"), configured.state.options]
    output = []
    result = tau_runtime.prepare_configuration(configured.assignment, cancelled=lambda: False, emit=output.append, poll_interval=0)
    assert result["ready"] is True
    assert any("等待依赖服务恢复" in text for text in output)
    configured.restart.assert_not_called()
    configured.update_settings.assert_not_called()


@pytest.mark.parametrize("resuming", [False, True])
@pytest.mark.parametrize("required_account", [None, "five", "three"])
def test_tau_waits_for_transient_dev_health_and_keeps_confirmed_message(configured, tmp_path, monkeypatch, resuming, required_account):
    """DEV 预检短暂失败时 TAU 等待恢复，续跑沿用已确认的消息，账号不符时不发送"""
    import json
    from management_portal.test_framework.dev_target import DevTargetError, VerifiedDevTarget
    from management_portal.test_framework import runner_adapters
    from management_portal.test_framework.tau import TauConfig

    monkeypatch.setattr(TauConfig, "unavailable_reason", lambda _self: "")
    monkeypatch.setattr(runner_adapters.time, "sleep", lambda _delay: None)
    target = VerifiedDevTarget("dev", "http://portal.test", "dev-current", "dev-current", "component-v2", "test-actor")
    verify = mock.Mock(side_effect=[DevTargetError("temporarily unavailable", details={"health_error_type": "TimeoutError"}), target, target])
    handle = {"resident_id": configured.assignment["resident_id"], "lineage_id": configured.assignment["lineage_id"], "message_id": "msg_existing"}
    result = {**handle, "message": 'PORTAL_TAU_RESULT: {"status":"passed","summary":"completed","issues":[],"evidence":[]}'}
    invoke, resume = mock.Mock(return_value=result), mock.Mock(return_value=result)
    planned = {"case_id": "test_0123456789ab", "case_title": "recovery", "module": "workbench.panels", "implementation": {"task_prompt": "Use the page"}}
    if required_account:
        planned["resident_account_id"] = required_account
    if resuming:
        planned["_resume_invocation"] = handle
        (tmp_path / 'run-context.json').write_text(json.dumps({'build_id': 'original-build', 'original_evidence': True}))
    else:
        configured.state.options["source"]["account_id"] = "five"
        configured.state.runtime["service_tier"] = "priority"
    output = []
    adapter = runner_adapters.TauAdapter(TauConfig(tmp_path / "storage.json"), invoke, verify, configured.assignments, resume)
    outcome = adapter.execute(run_id="testrun_0123456789abcdef", planned=planned, run_dir=tmp_path, actor="resident_aaaaaaaaaaaa", emit=output.append, cancelled=lambda: False)
    if required_account == "three" and not resuming:
        # The case was not sent: a precondition, not a failed verdict.
        assert outcome.status == "unsupported"
        assert outcome.details["required_account"] == "three"
        invoke.assert_not_called()
        resume.assert_not_called()
        return
    assert outcome.status == "passed"
    context = json.loads((tmp_path / 'run-context.json').read_text())
    assert len(context['resource_lifecycle_id']) == 64
    assert invoke.call_count == (0 if resuming else 1)
    assert resume.call_count == (1 if resuming else 0)
    if resuming:
        assert resume.call_args.kwargs["message_id"] == "msg_existing"
        assert context['build_id'] == 'original-build' and context['original_evidence'] is True
        configured.resident.get_resident_restart_options.assert_not_called()
    else:
        if required_account:
            assert "全部 Codex Resident 必须使用账号 five" in invoke.call_args.kwargs["prompt"]
            assert "被测业务成员、讨论成员" in invoke.call_args.kwargs["prompt"]
            assert "不约束被测业务" not in invoke.call_args.kwargs["prompt"]
        else:
            assert "Codex 账号 five" in invoke.call_args.kwargs["prompt"]
            assert "仅用于执行测试的 TAU Resident" in invoke.call_args.kwargs["prompt"]
            assert "仅当用例明确指定账号或速度时" in invoke.call_args.kwargs["prompt"]
        assert "同一账号与 service tier" not in invoke.call_args.kwargs["prompt"]
        assert "正常 build 更新本身不构成功能失败" in invoke.call_args.kwargs["prompt"]
        assert "不能盲目重发" in invoke.call_args.kwargs["prompt"]
    assert sum("等待 DEV 预检连接恢复" in text for text in output) == 1


def test_tau_cancellation_stops_waiting_for_dev_without_submitting_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """等待 DEV 恢复期间取消运行，TAU 停止等待且不发送用例"""
    with subtests.test(contract='test_tau_cancellation_stops_waiting_for_dev_without_submitting'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            from management_portal.test_framework.dev_target import DevTargetError
            from management_portal.test_framework.runner_adapters import TauAdapter
            from management_portal.test_framework.tau import TauConfig
            monkeypatch.setattr(TauConfig, 'unavailable_reason', lambda _self: '')
            invoke = mock.Mock()
            verify = mock.Mock(side_effect=DevTargetError('restarting', details={'health_http_status': '503'}))
            adapter = TauAdapter(TauConfig(tmp_path / 'storage.json'), invoke, verify)
            result = adapter.execute(run_id='testrun_0123456789abcdef', planned={}, run_dir=tmp_path, actor='resident_aaaaaaaaaaaa', emit=mock.Mock(), cancelled=lambda: True)
            assert result.status == 'cancelled'
            invoke.assert_not_called()


def test_tau_modules_and_rotated_slots_share_one_lineage(configured, monkeypatch):
    """不同模块与替换槽位只创建 Resident，均使用同一职责绑定团队。

    不同模块与替换槽位只创建 Resident，均使用同一职责绑定团队。"""
    from management_portal import resident_handoff
    created = []
    def create(**arguments):
        created.append(arguments)
        return {"resident_id": f"resident_{len(created):012x}", "lineage_id": arguments["lineage_binding"]}
    monkeypatch.setattr(resident_handoff, "ensure_invocation_resident", create)
    for module in ["execution.service", "workflow.paper", "execution.service"]:
        if module == "execution.service" and created:
            configured.assignments.rotate(module, changed_by=TAU_ACTOR)
        assigned = tau_runtime.ensure_module_resident(configured.assignments, module)
        assert assigned["lineage_id"] == "lineage_0123456789ab"
    assert len({row["resident_scope"] for row in created}) == 3
    assert {row["lineage_binding"] for row in created} == {"lineage_0123456789ab"}
    configured.isa["CreateLineage"].assert_not_called()


def test_tau_moved_identity_is_reconciled_without_recreation(configured, monkeypatch):
    """正式迁移后刷新缓存归属，保留执行槽位、Resident 和账号配置。

    正式迁移后刷新缓存归属，保留执行槽位、Resident 和账号配置。"""
    monkeypatch.setattr(tau_runtime, "resolve_application_lineage", lambda **_: "lineage_abcdefabcdef")
    configured.resident.get_resident.return_value = {"lineage_id": "lineage_abcdefabcdef"}
    result = tau_runtime.ensure_module_resident(configured.assignments, "workbench.panels")
    assert result["lineage_id"] == "lineage_abcdefabcdef"
    for key in ["resident_id", "slot_id", "configuration_id", "runtime_options", "backend_route_id"]:
        assert result[key] == configured.assignment[key]
    assert configured.assignments.assignment("workbench.panels") == result
    configured.isa["CreateLineage"].assert_not_called()


def test_tau_missing_binding_or_unmoved_member_cannot_create_team(configured, monkeypatch, subtests):
    """职责未登记或成员未迁移时明确失败，不旁路创建新团队。

    职责未登记或成员未迁移时明确失败，不旁路创建新团队。"""
    from management_portal.application_targets import ApplicationUnconfigured
    def missing(**_):
        raise ApplicationUnconfigured("tau")
    with subtests.test(case="missing"):
        monkeypatch.setattr(tau_runtime, "resolve_application_lineage", missing)
        with pytest.raises(ApplicationUnconfigured):
            tau_runtime.ensure_module_resident(configured.assignments, "execution.service")
    with subtests.test(case="unmoved"):
        monkeypatch.setattr(tau_runtime, "resolve_application_lineage", lambda **_: "lineage_abcdefabcdef")
        configured.resident.get_resident.return_value = {"lineage_id": configured.assignment["lineage_id"]}
        with pytest.raises(RuntimeError, match="尚未迁入"):
            tau_runtime.ensure_module_resident(configured.assignments, "workbench.panels")
    configured.isa["CreateLineage"].assert_not_called()


def test_tau_rotation_during_creation_rejects_old_receipt(configured, monkeypatch):
    """创建期间更换槽位时，旧回执不能覆盖新槽位或派发给旧执行器。

    创建期间更换槽位时，旧回执不能覆盖新槽位或派发给旧执行器。"""
    from management_portal import resident_handoff
    def create(**arguments):
        configured.assignments.rotate("execution.service", changed_by=TAU_ACTOR)
        return {"resident_id": "resident_abcdefabcdef", "lineage_id": arguments["lineage_binding"]}
    monkeypatch.setattr(resident_handoff, "ensure_invocation_resident", create)
    with pytest.raises(RuntimeError, match="槽位已更换"):
        tau_runtime.ensure_module_resident(configured.assignments, "execution.service")
    assert not configured.assignments.assignment("execution.service")["resident_id"]


def test_tau_case_keeps_only_the_evidence_its_report_cites(tmp_path):
    """TAU 用例结束后只保留报告引用的证据

    有结论（passed/failed）的 TAU 用例结束后，证据目录中未被 evidence 引用的文件和空目录被删除；没有结论（error/cancelled）时全部保留以便诊断。

    :aspects: functional, behavior
    """
    from management_portal.test_framework.runner_adapters import TauAdapter

    adapter = object.__new__(TauAdapter)
    case_dir = tmp_path / "case"
    for name in ("run-context.json", "shots/010-final.png", "shots/005-step.png",
                 "network.json", "dumps/state.json", "framework-helper-cleanup.json"):
        (case_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (case_dir / name).write_bytes(b"x" * 10)
    details = {"evidence": ["run-context.json", "./shots/010-final.png", "framework-helper-cleanup.json"]}

    # Without a parsed report nothing is removed.
    for status in ("error", "cancelled"):
        assert adapter.prune_evidence(run_dir=case_dir, status=status, details={"evidence": []}) is None
    assert len([p for p in case_dir.rglob("*") if p.is_file()]) == 6

    assert adapter.prune_evidence(run_dir=case_dir, status="failed", details=details) == {"files": 3, "bytes": 30}
    assert sorted(p.relative_to(case_dir).as_posix() for p in case_dir.rglob("*")) == [
        "framework-helper-cleanup.json", "run-context.json", "shots", "shots/010-final.png",
    ]


@pytest.mark.parametrize(("names", "stale"), [
    (["jev_decide", "read_portal_resource", "search_portal_resource"], True),
    (["hatg_instruction", "jev_decide"], False),
    # Bindings recorded before tool names were kept are older than the tool.
    ([], True),
])
def test_tau_replaces_a_session_that_never_offered_the_isa_tool(configured, monkeypatch, names, stale):
    """TAU 替换从未提供 ISA 工具的旧 Codex 会话

    执行器的 Codex 会话若在 hatg_instruction 上线前启动，状态报告 session_tools_stale 且配置时重启会话；已带该工具或无会话记录的执行器保持原会话。

    A Codex thread keeps the tools it started with: module Residents whose
    threads predate the hatg_instruction tool could only use the CLI, and a
    case that requires the tool could not pass.

    :aspects: functional, behavior
    """
    from management_portal.execution import agent_data_store

    resident_id = configured.assignment["resident_id"]
    configured.state.options["source"].update(account_id="five", provider_session_ref="ref-old")
    configured.state.runtime["service_tier"] = "priority"
    bindings = {f"resident:{resident_id}": {
        "provider_session_ref": "ref-old", "provider": "codex", "backend_session_id": "thread-old",
        "harness_tool_names": names,
    }}
    monkeypatch.setattr(agent_data_store, "get_agent_data_store",
                        lambda: SimpleNamespace(provider_binding=lambda binding_id: bindings.get(binding_id, {})))

    status = tau_runtime.resident_status(configured.assignment)
    assert status["session_tools_stale"] is stale
    assert status["ready"] is not stale
    result = tau_runtime.apply_configuration(configured.assignment)
    if not stale:
        configured.restart.assert_not_called()
        assert result["ready"] is True
        return
    call = configured.restart.call_args
    assert call.args == (TAU_ACTOR, {
        # A native fork would keep the old tools.
        "resident_id": resident_id, "provider": "codex", "account_id": "five",
        "context_strategy": "compact", "active_execution_policy": "wait",
    }, f"tau-config:{configured.assignment['configuration_id']}:{resident_id}:tools:source-generation",
        {resident_id: "source-generation"})
    # While the replacement runs the old session is not judged again.
    assert tau_runtime.resident_status(configured.assignment)["status"] == "migrating"
    tau_runtime.apply_configuration(configured.assignment)
    configured.restart.assert_called_once()
