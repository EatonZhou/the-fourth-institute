"""The Agent Harness enables a feature by what the Adapter can do, never by its name.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations
from management_portal.execution.harness_artifact_recall import artifact_scope_for_invocation

import io
import json
import tokenize
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.execution import harness_capabilities as capabilities
from management_portal.execution.backend_contracts import BackendContext, BackendInvocation, BackendResult
from management_portal.execution.backend_services import AgentSessionService
from management_portal.execution.codex_backend import CodexBackend
from management_portal.execution.dsh_backend import DshBackend
from management_portal.execution.contracts import ExecutionRequest, InputMedia, SessionOptions
from management_portal.execution.harness import (
    AgentHarness,
    HarnessFeatureOutcome,
    default_agent_harness,
)
from management_portal.execution.harness_auto_policy import AdaptiveHarnessController
from management_portal.execution.harness_early_compaction import EarlyContextCompactionFeature
from management_portal.execution.harness_native_loop import NativeToolLoopGovernorFeature
from management_portal.execution import harness_policy_table
from management_portal.execution.harness_policy_table import (
    DEFAULT_POLICY,
    context_budget_tokens,
    policy_parameters,
)
from management_portal.execution.native_tool_events import (
    NativeToolEventTranslator,
)
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.execution.provider_turn_usage import (
    ObservedTurnUsage,
    kimi_turn_usage,
    read_dsh_context_window,
)
from management_portal.execution.session_manager import RuntimeSession
from tests.cases.claude_sdk_support import load_claude_module

EVERY_CAPABILITY = capabilities.HARNESS_CAPABILITIES
REQUIREMENTS = capabilities.default_feature_requirements()


@pytest.fixture(autouse=True)
def isolate_harness_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTAL_EXECUTION_STATE_ROOT", str(tmp_path / "harness-state"))


class _Adapter:
    """An Adapter that declares its own capabilities.

    It answers to the name of a real Backend whose catalog entry declares
    far less, so a test passes only if the declaration, not the name, decides.
    """

    provider_id = "kimi"

    def __init__(self, declared: frozenset[str]) -> None:
        self.harness_capabilities = declared
        self.invocations: list[BackendInvocation] = []

    def execute(self, invocation: BackendInvocation, _context: BackendContext) -> BackendResult:
        self.invocations.append(invocation)
        return BackendResult(output={"message": "done"}, backend_session_id="session")

    def interrupt(self, **_kwargs: object) -> bool:
        return False


def _invocation(
    features: dict | None = None,
    *,
    mode: str = "manual",
    provider: str = "kimi",
    recovery_checkpoint: dict | None = None,
    metadata: dict | None = None,
) -> BackendInvocation:
    route = "zero" if AGENT_PROVIDERS[provider].accepts_routes else ""
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider=provider,
        operation="turn",
        cwd="/tmp",
        runtime_options={"agent_harness": {"mode": mode, "features": features or {}}},
        session=SessionOptions(mode="persistent", binding_id="resident:capabilities", backend_route_id=route),
        metadata=metadata or {},
    )
    return BackendInvocation(
        request=request,
        session=RuntimeSession(
            mode="persistent",
            binding_id="resident:capabilities",
            backend_session_id="session",
            backend_route_id=route,
            is_new_backend_session=False,
        ),
        user_prompt=request.prompt,
        system_context="",
        attach_system_context=False,
        is_new_backend_session=False,
        recovery_checkpoint=dict(recovery_checkpoint or {}),
    )


def _context(events: list[tuple[str, dict]], plans: list[dict] | None = None) -> BackendContext:
    return BackendContext(
        emit_event=lambda event_type, details: events.append((event_type, details)),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
        harness_plan_observer=(plans.append if plans is not None else None),
    )


def _policy_invocation(
    *,
    declared: frozenset[str],
    provider: str = "backend_of_tomorrow",
    binding_id: str = "binding",
    prompt: str = "continue",
    cwd: str = "",
    metadata: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(
            provider=provider,
            operation="turn",
            cwd=cwd,
            input_media=(),
            execution_id="",
            runtime_options={"agent_harness": {"mode": "auto", "features": {}}},
            metadata=metadata or {},
        ),
        session=SimpleNamespace(binding_id=binding_id, is_new_backend_session=False),
        user_prompt=prompt,
        is_new_backend_session=False,
        harness_tools=(),
        harness_capabilities=declared,
    )


# --- every feature, with and without what it needs ---------------------------


@pytest.mark.parametrize("feature_id", sorted(REQUIREMENTS))
def test_feature_is_planned_with_its_capabilities_and_left_out_without_them(tmp_path: Path, feature_id: str) -> None:
    """功能按能力决定是否进入计划

    Adapter 具备所需能力时功能进入计划，缺少时被排除。
    """
    harness = default_agent_harness(state_root=tmp_path)
    requirement = REQUIREMENTS[feature_id]
    features = {name: {"enabled": name == feature_id} for name in REQUIREMENTS}

    capable = replace(_invocation(features), harness_capabilities=EVERY_CAPABILITY)
    assert feature_id not in harness.unavailable_features(capable)
    capable_plan, _decision = harness._effective_config(capable)
    assert capable_plan["features"][feature_id]["enabled"] is True

    bare = replace(_invocation(features), harness_capabilities=frozenset())
    plan, _decision = harness._effective_config(bare)
    if not requirement:
        # The feature needs nothing from the Adapter: it runs on every Backend.
        assert feature_id not in harness.unavailable_features(bare)
        assert plan["features"][feature_id]["enabled"] is True
        return
    reason = harness.unavailable_features(bare)[feature_id]
    assert reason == "requires_" + sorted(requirement[0])[0]
    assert plan["features"][feature_id] == {"enabled": False, "unavailable": reason}
    # Any one of the feature's alternatives is enough.
    for alternative in requirement:
        partial = replace(_invocation(features), harness_capabilities=frozenset(alternative))
        assert feature_id not in harness.unavailable_features(partial)


def test_every_registered_feature_declares_its_requirement() -> None:
    """每个登记的 Harness 功能声明所需能力

    Harness 功能注册表中的每项功能都声明自己依赖的 Adapter 能力。
    """
    assert set(REQUIREMENTS) == set(default_agent_harness()._features)
    for feature_id, requirement in REQUIREMENTS.items():
        for alternative in requirement:
            assert alternative and alternative <= EVERY_CAPABILITY, feature_id


def test_a_planned_feature_the_adapter_cannot_run_is_reported_with_its_reason(tmp_path: Path) -> None:
    """计划了但 Adapter 无法执行的功能会报告原因

    Harness 计划中的某项功能所需能力 Adapter 不具备时，结果中记录未执行及原因。
    """
    adapter = _Adapter(frozenset())
    events: list[tuple[str, dict]] = []
    service = AgentSessionService(
        {"kimi": adapter},
        harness=default_agent_harness(state_root=tmp_path),
    )
    result = service.execute(
        _invocation({
            **{name: {"enabled": False} for name in REQUIREMENTS},
            "tool_call_granularity": {"enabled": True},
            "early_context_compaction": {"enabled": True},
            "tool_output_offloading": {"enabled": True},
        }),
        _context(events),
    )

    assert result.output == {"message": "done"}
    prepared = next(details for event_type, details in events if event_type == "agent_harness_prepared")
    assert prepared["capabilities"] == []
    reports = {report["feature_id"]: report for report in prepared["features"]}
    assert reports["tool_call_granularity"]["status"] == "applied"
    for feature_id, capability in (
        ("early_context_compaction", "auto_compact_threshold"),
        ("tool_output_offloading", "portal_tools"),
    ):
        assert reports[feature_id]["lifecycle"] == "plan"
        assert reports[feature_id]["status"] == "skipped"
        assert reports[feature_id]["reason"] == f"requires_{capability}"
    # The Adapter got no Portal tools and no tool-batch hook it could not serve.
    assert adapter.invocations[0].harness_tools == ()
    assert adapter.invocations[0].tool_batch_processor is None


def test_harness_tools_are_mounted_only_for_an_adapter_that_offers_portal_tools(tmp_path: Path) -> None:
    """只为提供 Portal 工具的 Adapter 挂载 Harness 工具

    Adapter 不支持 Portal 工具时，Harness 工具不被挂载。
    """
    features = {name: {"enabled": name == "unified_resource_recall"} for name in REQUIREMENTS}
    metadata = {"resident_id": "resident_0123456789ab"}
    for declared, expected in ((frozenset({capabilities.PORTAL_TOOLS}), True), (frozenset(), False)):
        adapter = _Adapter(declared)
        service = AgentSessionService(
            {"kimi": adapter},
            harness=default_agent_harness(state_root=tmp_path),
        )
        service.execute(_invocation(features, metadata=metadata), _context([]))
        names = {tool.name for tool in adapter.invocations[0].harness_tools}
        assert ("hatg_instruction" in names) is expected
        assert ("read_portal_resource" in names) is expected


# --- the declarations themselves ---------------------------------------------




@pytest.mark.parametrize("provider", sorted(AGENT_PROVIDERS))
def test_a_provider_offers_the_slash_commands_its_declarations_allow_and_its_adapter_answers(provider: str) -> None:
    """斜杠命令菜单由 Provider 声明决定

    每个 Provider 的命令菜单由 runtime_settings、operations 和 native_goals 推出：/model、/fast、/permissions、/plan 对应设置项，/compact 对应操作，/goal 对应原生目标；菜单中的命令都能由其适配器处理。

    :module: workbench.chat
    :design: decision_5c62deb8253a
    """
    from management_portal.execution import slash_commands

    definition = AGENT_PROVIDERS[provider]
    offered = definition.native_commands()
    assert set(offered) <= definition.runtime_adapter().slash_commands
    assert ("compact" in offered) == ("compact" in definition.operations)
    assert ("goal" in offered) == definition.native_goals
    assert ("fast" in offered) == ("service_tier" in definition.runtime_settings)
    menu = [item["command"] for item in slash_commands.slash_command_catalog(backend=provider)]
    assert menu == [f"/{name}" for name in (*offered, "portal-help", "portal-tokens")]


def test_every_declared_capability_names_the_test_of_each_adapter_that_honours_it() -> None:
    """每项声明的 Harness 能力都有各适配器的契约测试

    每个 Harness 能力要么由目录测试在适配器类上核对，要么为每个声明它的 Provider 登记一项适配器测试；声明集合与登记一致，登记的测试存在，事件类能力的测试把事件交给统一检查。

    A declaration Portal relies on is exercised on every declaring Provider's Adapter.

    :module: execution.harness
    :design: decision_5c62deb8253a, decision_b1ab45c4b3ec
    """
    import importlib
    import inspect

    from tests.cases import capability_contracts as contract

    for capability in sorted(EVERY_CAPABILITY):
        if capability in contract.CLASS_CHECKED:
            continue
        tests = contract.ADAPTER_TESTS.get(capability, {})
        declaring = {name for name, definition in AGENT_PROVIDERS.items()
                     if capability in contract.declared(definition)}
        assert declaring == set(tests), capability
        for node in tests.values():
            path, *names = node.split("::")
            test = importlib.import_module(path.removesuffix(".py").replace("/", "."))
            for name in names:
                test = getattr(test, name)
            if capability in contract.EVENT_CHECKS:
                assert f"capability_contracts.{contract.EVENT_CHECKS[capability]}(" in inspect.getsource(test), node


def test_catalog_declares_what_the_adapter_class_implements() -> None:
    """按类核对的能力由声明它的 Provider 的适配器类实现

    ``CLASS_CHECKED`` 中的能力不登记适配器测试，在这里核对：声明 auto_compact_threshold 的 Provider，其适配器类定义 configure_auto_compact_threshold。

    A capability checked on the Adapter class is implemented by every declaring Provider's Adapter class.

    :module: execution.harness
    :design: decision_5c62deb8253a
    """
    import importlib

    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    from tests.cases import capability_contracts as contract

    hooks = {capabilities.AUTO_COMPACT_THRESHOLD: "configure_auto_compact_threshold"}
    assert set(hooks) == set(contract.CLASS_CHECKED)
    declaring = set()
    for name, definition in AGENT_PROVIDERS.items():
        module, _, class_name = definition.adapter.rpartition(".")
        adapter = getattr(importlib.import_module(module), class_name)
        for capability, hook in hooks.items():
            if capability in contract.declared(definition):
                declaring.add(name)
                assert callable(getattr(adapter, hook, None)), (name, hook)
    assert declaring


def test_codex_takes_a_reasoning_effort_and_an_image_detail_for_each_turn() -> None:
    """Codex 每个回合使用各自的推理强度和图片细节

    同一 Codex 线程的连续两个回合分别以各自请求中的 effort 与 input_image_detail 运行，不新建线程。

    Each turn of one Codex thread runs with its own effort and image detail, in the same thread.

    :module: execution.harness
    :design: decision_5c62deb8253a
    """

    thread_id = "019f0000-0000-7000-8000-000000000731"
    client = mock.Mock()
    client.run_turn.side_effect = lambda *_args, **_kwargs: mock.Mock(
        thread_id=thread_id, turn_id="turn-1", response="done", token_usage={})
    backend = CodexBackend(client_factory=lambda _socket_path: client)
    for effort, detail in (("high", "original"), ("low", "low")):
        request = ExecutionRequest.new(
            actor="sample_admin", prompt="inspect the image", service_type="agent_session", provider="codex",
            operation="turn", cwd="/tmp", input_media=(InputMedia("image", "local_path", "/tmp/probe.png"),),
            runtime_options={"effort": effort, "input_image_detail": detail},
            session=SessionOptions(mode="persistent", binding_id="provider:test-session", backend_route_id="one"),
        )
        backend.execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(mode="persistent", binding_id="", backend_session_id=thread_id,
                                       backend_route_id="one", is_new_backend_session=False),
                user_prompt=request.prompt, system_context="", attach_system_context=False,
                is_new_backend_session=False,
            ),
            BackendContext(emit_event=lambda *_args: None, cancellation_requested=lambda: False,
                           set_interrupt_target=lambda *_args: None),
        )

    turns = [call.kwargs for call in client.run_turn.call_args_list]
    assert [(turn["effort"], turn["image_detail"]) for turn in turns] == [("high", "original"), ("low", "low")]
    assert [call.args[0] for call in client.run_turn.call_args_list] == [thread_id, thread_id]
    client.start_thread.assert_not_called()


def test_image_input_capability_follows_the_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """图片输入能力随账号路线决定

    同一 Provider 的不同路线可以有不同的图片输入能力，计划按实际路线判断。
    """
    claude = AGENT_PROVIDERS["claude"]
    monkeypatch.setattr(type(claude), "catalog", lambda self, route="": {"capabilities": {"media": route == "with_images"}})
    assert capabilities.NATIVE_IMAGE_INPUT in claude.harness_capabilities("with_images")
    assert capabilities.NATIVE_IMAGE_INPUT not in claude.harness_capabilities("text_only")
    # An image that reaches the Provider as a path in the prompt is not a local attachment.
    assert capabilities.NATIVE_IMAGE_INPUT not in AGENT_PROVIDERS["kimi"].harness_capabilities()


def test_harness_code_names_no_backend() -> None:
    """Harness 代码不按 Backend 名称分支

    只有策略表可以按 Backend 取值，Harness 代码中不出现按后端名称的分支。

    Only the policy table may key values by Backend; no code branches on a name."""

    root = Path(capabilities.__file__).parent
    names = {f'"{provider}"' for provider in AGENT_PROVIDERS} | {f"'{provider}'" for provider in AGENT_PROVIDERS}
    offenders: list[str] = []
    for path in sorted(root.glob("harness*.py")):
        if path.name == "harness_policy_table.py":
            continue
        for token in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
            if token.type == tokenize.STRING and token.string in names:
                offenders.append(f"{path.name}:{token.start[0]} {token.string}")
    assert offenders == []


# --- the automatic policy ----------------------------------------------------


def _effective_decision(controller, invocation):
    harness = default_agent_harness()
    harness._adaptive_controller = controller
    return harness._effective_config(invocation)[1]


def _enabled(decision) -> set[str]:
    return {name for name, config in decision.features.items() if config.get("enabled") is True}


def test_auto_policy_gives_two_backends_with_the_same_capabilities_the_same_plan(tmp_path, isolated_portal_case):
    """能力相同的两个后端得到相同的自动计划

    auto 策略只依据能力和信号决策，能力相同的 Backend 得到同样的 Harness 计划。
    """
    with isolated_portal_case() as case_path:
        repo = case_path / "repo"
        (repo / ".git").mkdir(parents=True)
        controller = AdaptiveHarnessController()
        declared = AGENT_PROVIDERS["codex"].harness_capabilities()
        arguments = dict(cwd=str(repo), prompt="refactor the backend module and tests")

        known = controller.resolve(_policy_invocation(declared=declared, provider="codex", binding_id="a", **arguments), {})
        unknown = controller.resolve(_policy_invocation(declared=declared, binding_id="b", **arguments), {})

        assert _enabled(known) == _enabled(unknown)
        assert known.unavailable == unknown.unavailable == {}
        assert unknown.report()["capabilities"] == sorted(declared)


def test_auto_policy_keeps_the_reason_when_signals_choose_a_feature_the_adapter_lacks(tmp_path, isolated_portal_case):
    """信号选中但 Adapter 缺少能力时保留原因

    自动策略因信号选中某功能而 Adapter 不支持时，计划中保留选择与放弃的原因。
    """
    with isolated_portal_case() as case_path:
        controller = AdaptiveHarnessController()
        arguments = dict(prompt="continue")
        full = _policy_invocation(declared=EVERY_CAPABILITY, binding_id="full", **arguments)
        bare = _policy_invocation(declared=frozenset({capabilities.NATIVE_TOOL_EVENTS}), binding_id="bare", **arguments)
        for invocation in (full, bare):
            controller.state_store.save(artifact_scope_for_invocation(invocation), {
                "turn_count": 4,
                "last_context_pressure_percent": 75,
            })

        wanted = _effective_decision(controller, full)
        limited = _effective_decision(controller, bare)

        assert {"early_context_compaction", "tool_output_offloading", "hash_aware_read_dedup"} <= _enabled(wanted)
        assert _enabled(limited) == {"native_tool_loop_governor"}
        assert limited.unavailable == {
            "early_context_compaction": "requires_auto_compact_threshold",
            "hash_aware_read_dedup": "requires_portal_tools",
            "provider_error_continue": "requires_error_continuation",
            "jev_decision_tool": "requires_portal_tools",
            "tool_output_offloading": "requires_portal_tools",
        }
        assert limited.features["tool_output_offloading"]["unavailable"] == "requires_portal_tools"
        assert limited.report()["unavailable_features"] == limited.unavailable
        assert limited.signals["unavailable_features"] == limited.unavailable


def test_reasoning_effort_is_lowered_only_where_it_can_change_between_turns(tmp_path, isolated_portal_case):
    """只在轮间可改的地方降低推理强度

    推理强度只对能在轮与轮之间改变设置的 Backend 降低。
    """
    with isolated_portal_case() as case_path:
        controller = AdaptiveHarnessController()
        base = frozenset({capabilities.NATIVE_TOOL_EVENTS})
        plans = {}
        for name, declared in (("fixed", base), ("adjustable", base | {capabilities.TURN_REASONING_EFFORT})):
            invocation = _policy_invocation(declared=declared, binding_id=name)
            invocation.request.runtime_options["effort"] = "high"
            controller.state_store.save(artifact_scope_for_invocation(invocation), {
                "turn_count": 3,
                "last_context_pressure_percent": 68,
            })
            plans[name] = controller.resolve(invocation, {}).features["native_tool_loop_governor"]

        assert plans["adjustable"]["initial_reasoning_effort"] == "medium"
        assert plans["fixed"]["initial_reasoning_effort"] == "high"


def test_a_resident_turn_expects_portal_tools_before_they_are_mounted(tmp_path, isolated_portal_case):
    """Resident 轮次在挂载前就预期 Portal 工具

    Resident 执行的计划阶段即把 Portal 工具视为本轮能力，后续挂载与计划一致。
    """
    with isolated_portal_case() as case_path:
        controller = AdaptiveHarnessController()
        resident = {"resident_id": "resident_0123456789ab"}
        with_tools = controller.resolve(
            _policy_invocation(declared=frozenset({capabilities.PORTAL_TOOLS}), binding_id="tools", metadata=resident), {},
        )
        without_tools = controller.resolve(
            _policy_invocation(declared=frozenset(), binding_id="none", metadata=resident), {},
        )
        no_resident = controller.resolve(
            _policy_invocation(declared=frozenset({capabilities.PORTAL_TOOLS}), binding_id="plain"), {},
        )

        # The plan is made before the ISA tool is mounted; it still counts.
        assert with_tools.signals["portal_tool_count"] == 0
        assert with_tools.signals["portal_tools_expected"] is True
        assert {"tool_output_offloading", "hash_aware_read_dedup", "inline_media_history"} <= _enabled(with_tools)
        assert without_tools.signals["portal_tools_expected"] is False
        assert no_resident.signals["portal_tools_expected"] is False
        assert "tool_output_offloading" not in _enabled(no_resident)






# --- policy parameters per Backend -------------------------------------------


def test_policy_parameters_come_from_one_table() -> None:
    """策略参数只来自一张表

    Harness 策略参数集中在一张策略表中，例如 Claude Code 压缩较慢因而其界限高于 DSH。
    """
    assert policy_parameters("backend_of_tomorrow") is DEFAULT_POLICY
    assert policy_parameters("codex") == DEFAULT_POLICY
    # Claude Code compacts slowly (a summary by the session's model), so its
    # measured bound is higher than DSH's.
    for provider, bound in (("claude", 400_000), ("dsh", 200_000), ("kimi", 0)):
        own = policy_parameters(provider)
        assert own.compaction_max_context_tokens == bound
        # Their read-batch advice stays the measured prompt policy, also now
        # that they take reminders during a turn.
        assert own.granularity_reminders_during_turn is False
        # Everything else is the default until a measurement says otherwise.
        assert replace(
            own, compaction_max_context_tokens=0, granularity_reminders_during_turn=True,
        ) == DEFAULT_POLICY
    assert context_budget_tokens(DEFAULT_POLICY, 258_400) == 258_400
    assert context_budget_tokens(policy_parameters("dsh"), 1_000_000) == 285_714
    assert context_budget_tokens(policy_parameters("claude"), 1_000_000) == 571_428
    assert context_budget_tokens(policy_parameters("claude"), 200_000) == 200_000


def test_a_bounded_backend_measures_pressure_against_its_context_budget(tmp_path, isolated_portal_case):
    """有上下文预算的后端按自身预算计算压力

    声明了上下文预算的 Backend 以该预算而非模型窗口计算上下文压力。
    """
    with isolated_portal_case() as case_path:
        controller = AdaptiveHarnessController()
        declared = frozenset({capabilities.AUTO_COMPACT_THRESHOLD, capabilities.NATIVE_TOOL_EVENTS})
        plans = {}
        for provider in ("dsh", "backend_of_tomorrow"):
            invocation = _policy_invocation(declared=declared, provider=provider, binding_id=provider)
            controller.state_store.save(artifact_scope_for_invocation(invocation), {
                "turn_count": 5,
                # 160k of a one-million-token window: 16% of the window ...
                "last_context_pressure_percent": 16,
                "last_input_tokens": 160_000,
                "last_context_window_tokens": 1_000_000,
            })
            plans[provider] = controller.resolve(invocation, {})

        # ... but 56% of the bounded Backend's 285,714-token budget.
        bounded = plans["dsh"]
        assert bounded.signals["context_pressure_percent"] == 56.0
        compaction = bounded.features["early_context_compaction"]
        assert compaction["enabled"] is True
        assert compaction["max_context_tokens"] == 200_000
        assert compaction["policy_threshold_percent"] == 70
        unbounded = plans["backend_of_tomorrow"]
        assert unbounded.signals["context_pressure_percent"] == 16.0
        assert unbounded.features["early_context_compaction"]["enabled"] is False


def test_early_compaction_passes_the_policy_bound_to_the_adapter() -> None:
    """提前压缩把策略界限传给 Adapter

    提前压缩功能把策略表中的界限值原样交给 Adapter 执行。
    """
    calls: list[dict] = []

    class Adapter:
        harness_capabilities = frozenset({capabilities.AUTO_COMPACT_THRESHOLD})

        def configure_auto_compact_threshold(self, **kwargs):
            calls.append({key: value for key, value in kwargs.items() if key != "invocation"})
            return SimpleNamespace(invocation=kwargs["invocation"], applied=True, reason="configured", metrics={})

    feature = EarlyContextCompactionFeature()
    bounded = feature.apply(
        provider=Adapter(), invocation=_invocation(), context=_context([]),
        config={"enabled": True, "threshold_percent": 80, "policy_threshold_percent": 60, "max_context_tokens": 200_000},
    )
    plain = feature.apply(
        provider=Adapter(), invocation=_invocation(), context=_context([]),
        config={"enabled": True, "threshold_percent": 80},
    )

    assert bounded.status == plain.status == "applied"
    # A stale user ``threshold_percent`` never wins; the policy's value does.
    assert calls == [
        {"threshold_percent": 60, "max_context_tokens": 200_000},
        {"threshold_percent": 70},
    ]


def test_codex_compaction_limit_respects_the_token_bound(tmp_path: Path) -> None:
    """Codex 压缩上限遵守 token 界限

    Codex 的上下文压缩触发值不超过策略给出的 token 界限。
    """
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [{
        "slug": "gpt-test", "context_window": 1_000_000, "effective_context_window_percent": 95,
    }]}), encoding="utf-8")
    registry = mock.Mock()
    registry.routes.return_value = (SimpleNamespace(account_id="zero", codex_home=tmp_path),)
    backend = CodexBackend(account_registry=registry)
    invocation = _invocation(provider="codex")
    request = replace(invocation.request, runtime_options={**invocation.request.runtime_options, "model": "gpt-test"})

    def limit(**kwargs) -> int:
        transformed = backend.configure_auto_compact_threshold(
            invocation=replace(invocation, request=request), threshold_percent=70, **kwargs,
        )
        return transformed.invocation.request.runtime_options["config_overrides"]["model_auto_compact_token_limit"]

    assert limit() == 665_000
    assert limit(max_context_tokens=200_000) == 200_000
    assert limit(max_context_tokens=900_000) == 665_000


def test_dsh_turns_a_token_bound_into_a_share_of_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DSH 把 token 界限换算成窗口比例

    DSH 的压缩设置以窗口比例表达，由策略的 token 界限换算得到。
    """
    from management_portal import dsh_client

    monkeypatch.setattr(dsh_client, "STATE", tmp_path)
    session = "dsh-0123456789abcdef0123456789abcdef"
    log = tmp_path / session / "sessions" / "cwd" / session / "session.v4.jsonl"
    log.parent.mkdir(parents=True)
    options = {"auto_compact_percent": 70, "auto_compact_max_tokens": 200_000}
    backend = DshBackend()

    # A new session has no log yet: the share of the window alone applies.
    assert DshBackend._compaction_ratio(options, "") == 0.7
    assert DshBackend._compaction_ratio({}, session) == 0.0
    log.write_text(json.dumps({
        "type": "request/context", "seq": 1, "data": {"model": "m", "contextWindow": 1_000_000},
    }) + "\n", encoding="utf-8")
    assert read_dsh_context_window(log) == 1_000_000
    assert DshBackend._compaction_ratio(options, session) == 0.2
    assert backend.compaction_options(options, session) == {"auto_compact_ratio": 0.2}

    # The process keeps the earliest threshold it was started with.
    (tmp_path / session / "launch.json").write_text(json.dumps({"threshold_ratio": 0.2, "subagents": True}))
    assert backend.compaction_options({}, session) == {"auto_compact_ratio": 0.2}
    assert backend.compaction_options({"auto_compact_percent": 70}, session) == {"auto_compact_ratio": 0.2}
    closed: list[str] = []
    monkeypatch.setattr(DshBackend, "client_factory", staticmethod(
        lambda: SimpleNamespace(close_session=lambda key, session_id: closed.append(session_id))
    ))
    (tmp_path / session / "launch.json").write_text(json.dumps({"threshold_ratio": 0.8, "subagents": True}))
    monkeypatch.setattr(
        "management_portal.execution.dsh_backend.read_dsh_request_config",
        lambda path: {"model": "m", "reasoningEffort": "high", "_adapter_defaults": {}},
    )
    backend.prepare_native_session(session_key="k", backend_session_id=session, options={"model": "m", "effort": "high"})
    assert closed == []
    backend.prepare_native_session(
        session_key="k", backend_session_id=session, options={"model": "m", "effort": "high", **options},
    )
    assert closed == [session]


def test_claude_session_restarts_only_for_an_earlier_compaction() -> None:
    """Claude 会话只因更早的压缩点重启

    Claude Code 会话只在需要回到更早的压缩点时重启，其他情况继续原会话。
    """
    daemon = load_claude_module("claude_code_daemon")
    handle = SimpleNamespace(auto_compact_window=0)

    assert daemon._auto_compact_window({"auto_compact_window_tokens": 200_000}) == 200_000
    assert daemon._auto_compact_window({"auto_compact_window_tokens": 500}) == 0
    assert daemon._wants_earlier_compaction(handle, {}) is False
    assert daemon._wants_earlier_compaction(handle, {"auto_compact_window_tokens": 200_000}) is True
    handle.auto_compact_window = 200_000
    assert daemon._wants_earlier_compaction(handle, {"auto_compact_window_tokens": 200_000}) is False
    assert daemon._wants_earlier_compaction(handle, {"auto_compact_window_tokens": 300_000}) is False
    assert daemon._wants_earlier_compaction(handle, {}) is False
    assert daemon._wants_earlier_compaction(handle, {"auto_compact_window_tokens": 150_000}) is True


def test_kimi_and_dsh_usage_carry_the_context_window() -> None:
    """Kimi 与 DSH 的用量记录携带上下文窗口

    Kimi Code 和 DSH 报告的用量中包含上下文窗口大小，供压力计算使用。
    """
    observed = ObservedTurnUsage(
        turn={"input_tokens": 900, "output_tokens": 100, "total_tokens": 1000},
        context={"input_tokens": 400, "total_tokens": 400},
        context_window=262_144,
    )
    usage = kimi_turn_usage({}, observed)
    assert usage["context"]["inputTokens"] == 400
    assert usage["modelContextWindow"] == 262_144


# --- one Execution, one plan -------------------------------------------------


def test_a_recovered_attempt_keeps_the_first_plan_and_does_not_rebuild_the_prompt(tmp_path: Path) -> None:
    """恢复的一次沿用首次计划且不重建提示

    恢复执行时 Agent Harness 沿用首次保存的功能计划，不再记录新计划，改写提示的功能不介入，Adapter 只接回或续接这一轮。

    The Provider still holds a recovered turn: the Adapter attaches to it or continues it."""

    features = {name: {"enabled": name == "tool_call_granularity"} for name in REQUIREMENTS}
    adapter = _Adapter(frozenset())
    service = AgentSessionService({"kimi": adapter}, harness=default_agent_harness(state_root=tmp_path))
    plans: list[dict] = []
    service.execute(_invocation(features), _context([], plans))
    assert len(plans) == 1 and set(plans[0]["features"]) == {"tool_call_granularity"}
    assert "prompt_prefix" not in plans[0]
    recovered = _invocation(features, recovery_checkpoint={"turn_id": "turn-1", "harness_plan": plans[0]})
    service.execute(recovered, _context([], plans))
    # The recovered attempt records nothing new: the plan is already kept.
    assert len(plans) == 1
    first, second = [invocation.user_prompt for invocation in adapter.invocations]
    assert first != "continue" and second == "continue"




# --- native tool events ------------------------------------------------------


def test_provider_tool_events_are_translated_to_one_shape() -> None:
    """各 Provider 的工具事件转换为统一形状

    不同 Provider 的工具调用与结果事件被转换成同一种事件结构。
    """
    translator = NativeToolEventTranslator("/work")
    read = {"file_path": "/repo/a.py", "offset": 2001, "limit": 2000}

    started = translator.translate({"type": "response_item", "payload": {
        "type": "function_call", "name": "Read", "call_id": "c1", "arguments": read,
    }})
    completed = translator.translate({"type": "response_item", "payload": {
        "type": "function_call_output", "call_id": "c1", "output": [{"type": "text", "text": "line\n" * 10}],
    }})

    assert started[0]["method"] == "item/started"
    item = completed[0]["params"]["item"]
    assert item["type"] == "commandExecution" and item["id"] == "c1"
    # A later part of the same file is another read, not a repeated one.
    assert item["commandActions"] == [{"type": "read", "path": "/repo/a.py#offset=2001,limit=2000"}]
    assert item["aggregatedOutput"] == "line\n" * 10 and item["exitCode"] == 0

    # A shell command that starts as an item and ends as a function output.
    translator.translate({"method": "item/started", "params": {"item": {
        "type": "commandExecution", "id": "c2", "command": "rg -n needle", "status": "running",
    }}})
    shell = translator.translate({"type": "response_item", "payload": {
        "type": "function_call_output", "call_id": "c2", "output": "boom", "is_error": True,
    }})[0]["params"]["item"]
    assert shell["type"] == "commandExecution" and shell["command"] == "rg -n needle"
    assert shell["status"] == "failed" and shell["exitCode"] == 1

    # A tool item with JSON-string arguments and text content items.
    translator.translate({"method": "item/started", "params": {"item": {
        "type": "dynamicToolCall", "id": "c3", "tool": "read", "arguments": json.dumps({"file_path": "/repo/b.py"}),
    }}})
    dynamic = translator.translate({"method": "item/completed", "params": {"item": {
        "type": "dynamicToolCall", "id": "c3", "tool": "read", "arguments": json.dumps({"file_path": "/repo/b.py"}),
        "status": "completed", "success": True, "contentItems": [{"type": "inputText", "text": "body"}],
    }}})[0]["params"]["item"]
    assert dynamic["commandActions"] == [{"type": "read", "path": "/repo/b.py"}]
    assert dynamic["aggregatedOutput"] == "body"

    # An image result keeps its bytes countable.
    translator.translate({"type": "response_item", "payload": {
        "type": "function_call", "name": "Read", "call_id": "c4", "arguments": {"file_path": "/repo/a.png"},
    }})
    image = translator.translate({"type": "response_item", "payload": {
        "type": "function_call_output", "call_id": "c4",
        "output": [{"type": "image", "source": {"type": "base64", "data": "QUJD"}}],
    }})[0]["params"]["item"]
    assert image["type"] == "dynamicToolCall"
    assert image["contentItems"] == [{"type": "inputImage", "imageUrl": "data:image/unknown;base64,QUJD"}]

    assert translator.translate({"method": "turn/completed", "params": {}}) == [{"method": "turn/completed", "params": {}}]
    assert translator.translate({"method": "item/completed", "params": {"item": {"type": "agentMessage"}}}) == []


def test_loop_governor_observes_without_steering_and_withholds_its_reminders() -> None:
    """循环守卫只观察不插话并扣留提醒

    Loop governor 在观察模式下不向会话插话，生成的提醒被扣留并记录。
    """
    feature = NativeToolLoopGovernorFeature()
    outcome = feature.apply(
        provider=SimpleNamespace(harness_capabilities=frozenset({capabilities.NATIVE_TOOL_EVENTS})),
        invocation=_invocation(), context=_context([]),
        config={"enabled": True, "duplicate_read_threshold": 2, "initial_reasoning_effort": "low",
                "configured_reasoning_effort": "high", "adaptive_reasoning_effort": True},
    )
    assert outcome.status == "applied"
    assert outcome.metrics["runtime_control_available"] is False
    # Without the capability the effort the user configured stays.
    assert outcome.metrics["reasoning_effort_adjusted"] is False
    processor = outcome.invocation.provider_event_processor
    translator = NativeToolEventTranslator("/work")
    for index in range(4):
        for event in (
            {"type": "response_item", "payload": {"type": "function_call", "name": "Read", "call_id": f"c{index}",
                                                  "arguments": {"file_path": "/repo/a.py"}}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": f"c{index}", "output": "x"}},
        ):
            for translated in translator.translate(event):
                processor.process_event(translated)

    report = processor.report()
    assert report["native_tool_calls_observed"] == 4
    assert report["duplicate_read_calls_observed"] == 3
    assert report["runtime_control_available"] is False
    assert "duplicate_read_pressure" in report["runtime_control_reasons_withheld"]
    assert report["runtime_controls_enqueued"] == 0
    assert processor.next_control() is None
