"""Composition root for native Agent backends; callers consume declarations.

Native SDKs, route parsing and model constraints stay behind these adapters.
Adding a backend must not add provider tests to Resident or ExecutionRuntime.
"""
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from .harness_capabilities import (
    ACTIVE_TURN_INPUT,
    AUTO_COMPACT_THRESHOLD,
    CONTEXT_USAGE_EVENTS,
    ERROR_CONTINUATION,
    INPUT_IMAGE_DETAIL,
    NATIVE_IMAGE_INPUT,
    NATIVE_TOOL_EVENTS,
    PORTAL_TOOLS,
    SUBAGENT_EVENTS,
    SUBAGENT_STOP,
    SUBAGENTS,
    TURN_REASONING_EFFORT,
)


# The slash commands a Provider's session may answer, in menu order.  Which
# of them it answers follows from what it declares (``native_commands``).
NATIVE_COMMANDS = ("status", "goal", "plan", "model", "fast", "permissions", "compact")


@dataclass(frozen=True)
class AgentProvider:
    # The one name of this Provider: ISA arguments, a Resident's backend,
    # execution requests, usage and on-disk runtime folders all use it.
    provider: str
    label: str
    operations: frozenset[str]
    runtime_settings: tuple[str, ...]
    catalog_module: str = ""
    default_route: str = ""
    media: bool = False
    active_turn_input: bool = False
    account_adapter: str = ""
    options_adapter: str = "NativeOptions"
    accepts_routes: bool = True
    resume_without_session: bool = False
    native_review: bool = False
    native_goals: bool = False
    local_catalog: bool = True
    # The Provider's Backend, as ``module.Class`` (``build_backend``).
    adapter: str = ""
    runtime_package: str = ""
    default_concurrency: int = 8
    # Where a Resident's resident.md travels: ``user_prompt`` in front of the
    # turn's user message, or ``system_instructions`` with Portal's system
    # instructions (resident_execution_provider.INITIAL_INSTRUCTION_TRANSPORTS).
    initial_instructions_transport: str = "user_prompt"
    # Portal's system instructions reach this Provider as process
    # configuration its session history does not keep: Claude Code is given
    # its system prompt anew at every CLI start, and a resumed process given
    # none runs with an empty one.  Every turn sends the same instructions, so
    # every process of the session has them and the daemon reuses the process
    # from one turn to the next.
    system_context_every_turn: bool = False
    system_context_survives_compaction: bool = False
    image_input_transport: str = "native"
    # The Backend can receive an inherited context and compact it before the
    # first turn, so a Fork may start from a compacted copy of its source.
    seeded_compaction: bool = False
    # A Worker handover leaves the running turn to the Provider's own process:
    # the retiring Worker detaches without interrupting it, and the next
    # Worker attaches to the same turn (same session and turn id).  A turn
    # that is no longer running (its Provider process restarted) is continued
    # in the same session with "continue"; every Backend recovers that way,
    # and none sends a turn's input again once the Provider has taken it.
    handover_keeps_turn: bool = False
    # What the Agent Harness may rely on in this Backend's Adapter, beyond the
    # fields above (names and meanings in ``harness_capabilities``).  The
    # Harness enables a feature only when the capabilities it needs are here.
    harness: frozenset[str] = frozenset()
    # A new Resident of this Provider may start subagents (needs ``subagents``).
    subagents_by_default: bool = False
    # The service the model requests go to (``provider_egress.UPSTREAMS``); a
    # route's catalog may name another (``upstream_provider``), as Claude
    # Code's DeepSeek routes do.
    upstream: str = ""
    # The Model API wire protocol (``ai_provider_registry.HARNESS_PROTOCOLS``)
    # an ``api_profile`` account of this Provider speaks; "" when its API-key
    # routes are fixed to one service.
    api_protocol: str = ""
    # A session keeps the Portal tools it started with: resuming or forking
    # it cannot add one, so a session started without a required tool needs
    # a new one (``session_tools``).
    session_tools_fixed: bool = False
    # Where the Provider keeps Portal's system instructions, as the prompt
    # injection settings tell a user (``prompt_injection.placements``).
    system_instructions_place: str = "Provider 的系统位置"
    # Home-relative globs of the Provider's own credentials and session
    # stores; scripts Portal runs for others never see them.
    credential_globs: tuple[str, ...] = ()
    # Provider-owned function returning absolute credential/session directories.
    credential_paths_provider: str = ""
    # ``module.function(agent_data, scheduler)`` that counts the requests of
    # turns the Provider takes on its own, outside Portal's turns; the
    # ExecutionService calls it periodically.
    self_started_usage: str = ""
    # The class (in ``provider_updates``) that checks, stages and switches
    # the Provider's shared local runtime; none for a Provider Portal does
    # not install.
    runtime_updater: str = ""

    def __post_init__(self) -> None:
        if self.subagents_by_default and SUBAGENTS not in self.harness:
            raise ValueError(f"{self.provider} cannot start subagents by default without the subagents capability")

    def allows_subagents(self, options: Any) -> bool:
        """Whether a turn with these runtime options lets the model start subagents."""

        value = options.get("allow_subagents", self.subagents_by_default) if isinstance(options, dict) else False
        return SUBAGENTS in self.harness and value is not False

    def native_commands(self) -> tuple[str, ...]:
        """The slash commands this Provider's session answers, from what it declares."""

        offered = {
            "status": True,
            "goal": self.native_goals,
            "plan": "collaboration_mode" in self.runtime_settings,
            "model": "model" in self.runtime_settings,
            "fast": "service_tier" in self.runtime_settings,
            "permissions": "permissions" in self.runtime_settings,
            "compact": "compact" in self.operations,
        }
        return tuple(name for name in NATIVE_COMMANDS if offered[name])

    def upstream_for(self, route: str = "") -> str:
        """The service this route sends its model requests to."""

        if not self.catalog_module:
            hook = self._account_hook("route_upstream")
            return (hook(route) if hook else "") or self.upstream
        try:
            return str(self.catalog(route).get("upstream_provider") or self.upstream)
        except Exception:  # noqa: BLE001 - an unknown route keeps the Provider's own service
            return self.upstream

    def harness_capabilities(self, route: str = "") -> frozenset[str]:
        """Every Harness capability of this Backend, for the given route."""

        capabilities = set(self.harness)
        if self.active_turn_input:
            capabilities.add(ACTIVE_TURN_INPUT)
        if self.media and self.image_input_transport == "native" and self._route_takes_images(route):
            capabilities.add(NATIVE_IMAGE_INPUT)
        return frozenset(capabilities)

    def _route_takes_images(self, route: str) -> bool:
        """Some Backends reach several model routes; not all of them read images."""

        if not self.catalog_module:
            hook = self._account_hook("route_takes_images")
            return hook(route) if hook else True
        try:
            return bool(self.catalog(route)["capabilities"]["media"])
        except Exception:  # noqa: BLE001 - an unknown route offers no image input
            return False

    def _account_hook(self, name: str):
        """A route-level answer from a Provider without a catalog module, if its account adapter gives one."""

        if not self.account_adapter:
            return None
        return getattr(self._adapter_class(self.account_adapter, "management_portal.execution.account_adapters"), name, None)

    @staticmethod
    def _adapter_class(path: str, default_module: str):
        module, _, name = path.rpartition(".")
        return getattr(import_module(module or default_module), name or path)

    def runtime_adapter(self):
        return self._adapter_class(self.options_adapter, "management_portal.execution.native_options")()

    def updater(self):
        """The Provider's runtime updater, ``None`` when Portal does not install its runtime."""

        if not self.runtime_updater:
            return None
        return self._adapter_class(self.runtime_updater, "management_portal.execution.provider_updates")()

    def self_started_usage_counter(self):
        """The function counting the turns the Provider takes on its own, ``None`` when it takes none."""

        if not self.self_started_usage:
            return None
        module, _, name = self.self_started_usage.rpartition(".")
        return getattr(import_module(module), name)

    def runtime_capabilities(self) -> dict[str, Any]:
        return self.runtime_adapter().capabilities(self)

    def build_backend(self):
        module, _, name = self.adapter.rpartition(".")
        return getattr(import_module(module), name)()

    def catalog(self, route: str = "") -> dict[str, Any]:
        if not self.catalog_module:
            from . import account_adapters
            adapter = getattr(account_adapters, self.account_adapter)()
            return adapter.catalog(adapter.resolve(route, {}))
        return import_module(self.catalog_module).catalog(route or self.default_route)

    def routes(self) -> list[dict[str, Any]]:
        return import_module(self.catalog_module).routes() if self.catalog_module else []

    def validate_settings(self, route: str, model: str, effort: str = "") -> tuple[str, str]:
        catalog = self.catalog(route)
        model = model or catalog["default_model"]
        if model not in catalog["models"]:
            raise ValueError(f"Model {model!r} is not enabled for {self.label}")
        if effort and effort not in catalog.get("efforts_by_model", {}).get(model, []):
            raise ValueError(f"Effort {effort!r} is not supported for {model!r}")
        return model, effort


AGENT_PROVIDERS = {
    p.provider: p for p in (
        AgentProvider("antigravity", "Google Antigravity", frozenset({"create", "turn", "release"}),
                      ("model", "permissions"), "management_portal.antigravity_accounts",
                      account_adapter="AntigravityAccounts",
                      adapter="management_portal.execution.antigravity_backend.AntigravityBackend",
                      options_adapter="management_portal.execution.antigravity_backend.AntigravityOptions",
                      initial_instructions_transport="system_instructions", system_context_every_turn=True,
                      harness=frozenset({PORTAL_TOOLS, NATIVE_TOOL_EVENTS, CONTEXT_USAGE_EVENTS}),
                      upstream="google", default_concurrency=4,
                      system_instructions_place="ACP session 的 systemPrompt.append，恢复时完整替换",
                      credential_globs=(".gemini*",),
                      runtime_updater="management_portal.antigravity_runtime.AntigravityRuntime"),
        AgentProvider("codex", "Codex", frozenset({"create", "turn", "fork", "compact", "release"}),
                      ("model", "reasoning_effort", "service_tier", "permissions", "collaboration_mode"), media=True, active_turn_input=True, adapter="management_portal.execution.codex_backend.CodexBackend", account_adapter="CodexAccounts", options_adapter="CodexOptions", native_review=True, native_goals=True, local_catalog=False, seeded_compaction=True,
                      # App-server owns the running turn and its native processes.
                      # Interrupting a follower's handover can abandon those processes.
                      handover_keeps_turn=True, upstream="openai", api_protocol="openai_responses", session_tools_fixed=True,
                      system_context_survives_compaction=True,
                      system_instructions_place="thread 的开发者指令，原生压缩后仍保留",
                      credential_globs=(".codex*",), runtime_updater="CodexRuntime",
                      # Codex subagents stay off until a Resident turns them on.
                      # A subagent runs in a thread of its own, whose turn is interrupted alone.
                      harness=frozenset({CONTEXT_USAGE_EVENTS, PORTAL_TOOLS, NATIVE_TOOL_EVENTS, AUTO_COMPACT_THRESHOLD,
                                         ERROR_CONTINUATION, INPUT_IMAGE_DETAIL, TURN_REASONING_EFFORT,
                                         SUBAGENTS, SUBAGENT_EVENTS, SUBAGENT_STOP})),
        AgentProvider("claude", "Claude Code", frozenset({"turn", "fork", "compact", "release"}),
                      ("model", "reasoning_effort", "permissions"), "management_portal.claude_provider_catalog", "deepseek_primary", adapter="management_portal.execution.claude_backend.ClaudeBackend", account_adapter="ClaudeAccounts", options_adapter="ClaudeOptions", resume_without_session=True,
                      initial_instructions_transport="system_instructions", system_context_every_turn=True, media=True,
                      # The CLI takes a user message that arrives during a turn after the
                      # turn's current step (the daemon's ``turn/steer``).
                      active_turn_input=True,
                      # The daemon keeps the turn across Worker connections (``turn_reattach``).
                      handover_keeps_turn=True,
                      harness=frozenset({CONTEXT_USAGE_EVENTS, PORTAL_TOOLS, NATIVE_TOOL_EVENTS, AUTO_COMPACT_THRESHOLD, ERROR_CONTINUATION,
                                         SUBAGENTS, SUBAGENT_EVENTS, SUBAGENT_STOP}),
                      subagents_by_default=True, upstream="anthropic", api_protocol="anthropic_messages",
                      system_instructions_place="系统提示，每次进程启动都带；关闭以外的时机效果相同",
                      credential_globs=(".claude*",), runtime_updater="ClaudeRuntime",
                      # Claude Code starts turns by itself (task notifications, messages).
                      self_started_usage="management_portal.execution.unattributed_usage.record_self_started_claude_usage"),
        AgentProvider("kimi", "Kimi Code", frozenset({"create", "turn", "fork", "compact", "release"}),
                      ("model", "permissions"), media=True, adapter="management_portal.execution.kimi_backend.KimiBackend", account_adapter="KimiAccounts", options_adapter="KimiOptions", api_protocol="openai_chat",
                      # Kimi Code steers a prompt queued during a turn into the running one.
                      active_turn_input=True,
                      # The Kimi Code server runs the prompt; a Worker only follows it.
                      handover_keeps_turn=True,
                      initial_instructions_transport="system_instructions", image_input_transport="path_reference",
                      # A subagent is a task of the main agent, which Kimi Code cancels alone.
                      harness=frozenset({CONTEXT_USAGE_EVENTS, PORTAL_TOOLS, NATIVE_TOOL_EVENTS, ERROR_CONTINUATION, SUBAGENTS, SUBAGENT_EVENTS,
                                         SUBAGENT_STOP}),
                      subagents_by_default=True, upstream="moonshot",
                      system_instructions_place="新 session 的系统提示；之后到期时附在本轮消息前",
                      credential_globs=(".kimi*",),
                      credential_paths_provider="management_portal.kimi_code_client.credential_paths",
                      runtime_updater="KimiRuntime"),
        AgentProvider("dsh", "DeepSeek Harness", frozenset({"turn", "compact", "release"}),
                      ("model", "reasoning_effort"), "management_portal.dsh_provider_catalog", "deepseek", adapter="management_portal.execution.dsh_backend.DshBackend", runtime_package="management_portal/dsh_runtime", resume_without_session=True,
                      # The agent takes steering at the running turn's next step (bridge ``turn/steer``).
                      active_turn_input=True,
                      # The bridge keeps the turn across Worker connections.
                      handover_keeps_turn=True,
                      # A subagent is an agent of its own, which the bridge cancels alone.
                      harness=frozenset({CONTEXT_USAGE_EVENTS, PORTAL_TOOLS, NATIVE_TOOL_EVENTS, AUTO_COMPACT_THRESHOLD, ERROR_CONTINUATION,
                                         SUBAGENTS, SUBAGENT_EVENTS, SUBAGENT_STOP}),
                      subagents_by_default=True, upstream="deepseek", api_protocol="anthropic_messages",
                      credential_paths_provider="management_portal.dsh_client.credential_paths",
                      system_context_every_turn=True,
                      system_instructions_place="每轮完整替换运行时系统提示", runtime_updater="DshRuntime"),
        AgentProvider("opencode", "OpenCode", frozenset({"turn", "compact", "release"}),
                      ("model", "reasoning_effort"), "management_portal.opencode_provider_catalog", "zen_free", api_protocol="openai_chat",
                      adapter="management_portal.opencode_backend.OpenCodeBackend", resume_without_session=True,
                      # OpenCode keeps a message's system instructions with that message only.
                      initial_instructions_transport="system_instructions", system_context_every_turn=True,
                      # A message sent during a run is taken at the run's next step.
                      active_turn_input=True,
                      # The bridge keeps the turn across Worker connections.
                      handover_keeps_turn=True,
                      # A subagent runs in a child session, which the bridge aborts alone.
                      harness=frozenset({CONTEXT_USAGE_EVENTS, PORTAL_TOOLS, NATIVE_TOOL_EVENTS, ERROR_CONTINUATION, SUBAGENTS, SUBAGENT_EVENTS,
                                         SUBAGENT_STOP}),
                      subagents_by_default=True, upstream="opencode",
                      system_instructions_place="每轮消息的 system 字段（OpenCode 只让它作用于这一轮）",
                      credential_paths_provider="management_portal.opencode_client.credential_paths",
                      runtime_updater="management_portal.opencode_runtime.OpenCodeRuntime"),
    )
}


def provider_name(value: object) -> str:
    """A stored or requested backend name; empty when there is none (deleted Residents may have none)."""

    return str(value or "").strip().lower()


