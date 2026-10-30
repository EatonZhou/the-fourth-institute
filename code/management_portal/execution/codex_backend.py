"""The Codex Adapter: Portal's sessions on the Codex app-server.

``CodexBackend`` resolves an execution's account route and app-server,
prepares its thread and runs each operation: create, turn, fork, compact
and release.  A turn runs in ``codex_turn_run``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from management_common.product_environment import mirror_product_env
from management_portal.codex_app_server_client import client_for_socket
from management_portal.codex_app_server_compaction import (
    DEFAULT_COMPACT_NO_PROGRESS_TIMEOUT_SECONDS,
    DEFAULT_COMPACT_TOTAL_TIMEOUT_SECONDS,
)
from management_portal.codex_app_server_protocol import (
    CodexAppServerError,
    CodexAppServerUnavailable,
    CodexThreadNotFound,
    CodexTurnDetached,
    thread_sandbox_from_policy,
)
from management_portal.resident_shell_environment import (
    may_inherit_portal_database,
    portal_database_environment,
)

from .account_adapters import CodexAccountRoute, CodexRoutes
from .backend_contracts import BackendContext, BackendInvocation, BackendResult, _positive_int
from .codex_rollout_history import (
    CodexCompaction,
    codex_subagent_rollouts,
    read_codex_context_window,
    read_codex_latest_compaction,
    read_codex_request_usage,
    read_codex_turn_messages,
)
from .codex_turn_run import CodexSession, CodexTurnRun, codex_goal_command
from .context_seed import preserve_structured_seed
from .contracts import ExecutionRecoveryDeferred, ExecutionRequest
from .harness import (
    AgentLoopToolBatch,
    AgentLoopToolBatchProcessor,
    AgentLoopToolResult,
    ContextWindowObservation,
    HarnessTool,
    InvocationTransformResult,
    tool_call_scope,
)
from .provider_catalog import AGENT_PROVIDERS

LOGGER = logging.getLogger(__name__)


def _bounded_wait_seconds(name: str, default: float) -> float:
    try:
        configured = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        configured = default
    return max(1.0, configured)


_CODEX_COMPACTION_PROMPT = (
    "Summarize the current task state using only established facts. Include the active "
    "request and completion criteria, constraints, verified progress and results, "
    "remaining work, confirmed blockers, and essential resource references. Preserve "
    "the distinction between completed actions and planned actions. Keep the summary "
    "concise and factual."
)


def _model_api_entry(api_id: str, model: str) -> dict[str, Any]:
    from management_portal.ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry
    try:
        entry = shared_ai_provider_registry.get(api_id)
    except ValueError:
        return {}
    return dict(AIProviderRegistry.model_entry(entry, model))


class CodexBackend:

    provider_id = "codex"

    def prepare_context_seed(
        self,
        invocation: BackendInvocation,
    ) -> BackendInvocation:
        return preserve_structured_seed(invocation)

    def _canonical_replay_turn(
        self, account_id: str, thread_id: str, turn: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve snapshot messages at the Provider boundary, before fan-out."""
        # A message without text or questions, such as an empty final answer,
        # has no rollout identity (read_codex_turn_messages) and nothing to replay.
        if not isinstance(turn.get("items"), list):
            return turn
        items = [item for item in turn["items"] if not (
            isinstance(item, dict) and item.get("type") == "agentMessage"
            and not str(item.get("text") or "").strip() and not item.get("questions")
        )]
        expected = sum(isinstance(item, dict) and item.get("type") == "agentMessage"
                       for item in items)
        if not expected:
            return {**turn, "items": items}
        path = self.account_registry.find_thread_rollout(
            account_id, thread_id, include_archived=True,
        )
        native = (read_codex_turn_messages(path, str(turn.get("id") or ""), thread_id=thread_id)
                  if isinstance(path, (str, Path)) else None)
        if str(turn.get("status") or "").casefold() in {"inprogress", "in_progress"}:
            messages = iter(native or ())
            canonical = []
            for item in items:
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    completed_message = next(messages, None)
                    if completed_message is not None:
                        canonical.append({**completed_message, "status": "completed"})
                else:
                    canonical.append(item)
            canonical.extend({**message, "status": "completed"} for message in messages)
            return {**turn, "items": canonical}
        if native is None or len(native) < expected:
            # Do not manufacture a second message ID or re-send the input.
            # Scheduler retains the accepted-turn checkpoint and retries it.
            raise ExecutionRecoveryDeferred(
                "Codex native message identities are not yet available for replay",
                retry_after_seconds=1,
            )
        messages = iter(native)
        canonical = [next(messages) if isinstance(item, dict) and item.get("type") == "agentMessage"
                     else item for item in items]
        canonical.extend(messages)
        return {**turn, "items": canonical}

    def __init__(
        self,
        *,
        account_registry: CodexRoutes | None = None,
        client_factory: Callable[[Path | str], Any] = client_for_socket,
    ) -> None:
        self.account_registry = account_registry or CodexRoutes()
        self.client_factory = client_factory

    def _latest_compaction(
        self,
        route: CodexAccountRoute,
        thread_id: str,
    ) -> CodexCompaction | None:
        rollout_path = self.account_registry.find_thread_rollout(
            route.account_id,
            thread_id,
            include_archived=True,
        )
        if not isinstance(rollout_path, (str, Path)):
            return None
        return read_codex_latest_compaction(rollout_path).compaction


    @contextmanager
    def _compaction_recorded_on_failure(
        self,
        *,
        context: BackendContext,
        route: CodexAccountRoute,
        thread_id: Callable[[], str],
        previous_compaction: Callable[[], CodexCompaction | None],
    ):
        """Record a compaction committed by a turn that did not complete.

        Codex compacts a thread automatically in the middle of a long turn.  A
        completed turn records that compaction afterwards; a turn that fails,
        is cancelled or is handed to another Worker records it here, otherwise
        AgentData keeps the uncompacted context and a recovered attempt takes
        the compacted rollout as its new baseline.
        """

        try:
            yield
        except BaseException:
            try:
                previous = previous_compaction()
                self._record_new_compaction(
                    context=context,
                    route=route,
                    thread_id=thread_id(),
                    previous_fingerprint=previous.fingerprint if previous is not None else "",
                    wait_seconds=0.0,
                    required=False,
                )
            except Exception:  # noqa: BLE001 - the turn's own failure is what the caller reports
                LOGGER.warning(
                    "Codex compaction of an unfinished turn could not be recorded for %s",
                    thread_id(),
                    exc_info=True,
                )
            raise

    def _record_new_compaction(
        self,
        *,
        context: BackendContext,
        route: CodexAccountRoute,
        thread_id: str,
        previous_fingerprint: str,
        wait_seconds: float,
        required: bool,
    ) -> CodexCompaction | None:
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            compaction = self._latest_compaction(route, thread_id)
            if (
                compaction is not None
                and compaction.fingerprint != previous_fingerprint
            ):
                # Codex compacts in the middle of a turn; what the turn did
                # after the compaction stays after it in the context.
                context.record_compaction(
                    provider=self.provider_id,
                    backend_session_id=thread_id,
                    backend_route_id=route.account_id,
                    content={
                        "replacement_history": [
                            dict(item)
                            for item in compaction.replacement_history
                        ]
                    },
                    created_at=compaction.created_at,
                    retain_from=compaction.created_at,
                    provider_compaction_ref=compaction.fingerprint,
                )
                return compaction
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        if required:
            raise RuntimeError(
                "Codex compact completed without a committed replacement context"
            )
        return None

    @staticmethod
    def _thread_config(options: dict[str, Any]) -> dict[str, Any]:
        raw_overrides = options.get("config_overrides")
        if raw_overrides is not None and not isinstance(
            raw_overrides,
            dict,
        ):
            raise ValueError("Codex config_overrides must be an object")
        config = dict(raw_overrides or {})
        # Ask for readable information through supported Provider controls.
        # These do not decrypt opaque reasoning or override explicit choices.
        config.setdefault("show_raw_agent_reasoning", True)
        config.setdefault("model_reasoning_summary", "auto")
        # Codex no longer registers update_plan by default. Portal projects
        # its native plan events, so enable it unless this session overrides
        # the choice (Codex accepts nested and dotted config keys).
        if "tools.update_plan.enabled" not in config and "tools.update_plan" not in config:
            raw_tools = config.get("tools")
            if raw_tools is not None and not isinstance(raw_tools, dict):
                raise ValueError("Codex config_overrides.tools must be an object")
            tools = dict(raw_tools or {})
            raw_plan = tools.get("update_plan")
            if raw_plan is not None and not isinstance(raw_plan, dict):
                raise ValueError("Codex config_overrides.tools.update_plan must be an object")
            plan = dict(raw_plan or {})
            plan.setdefault("enabled", True)
            tools["update_plan"] = plan
            config["tools"] = tools
        raw_agents = config.get("agents")
        if raw_agents is not None and not isinstance(raw_agents, dict):
            raise ValueError("Codex config_overrides.agents must be an object")
        codex = AGENT_PROVIDERS["codex"]
        if not isinstance(options.get("allow_subagents", codex.subagents_by_default), bool):
            raise ValueError("Codex allow_subagents must be a boolean")
        agents = dict(raw_agents or {})
        agents["enabled"] = codex.allows_subagents(options)
        agents["interrupt_message"] = False
        config["agents"] = agents
        config["compact_prompt"] = _CODEX_COMPACTION_PROMPT
        if isinstance(options.get("goal"), dict):
            raw_features = config.get("features")
            if raw_features is not None and not isinstance(raw_features, dict):
                raise ValueError(
                    "Codex config_overrides.features must be an object"
                )
            features = dict(raw_features or {})
            features["goals"] = True
            config["features"] = features
        config["project_doc_max_bytes"] = 0
        return config

    @classmethod
    def _runtime_thread_config(cls, request: ExecutionRequest) -> dict[str, Any]:
        config = cls._thread_config(request.runtime_options)
        database = portal_database_environment(may_inherit_portal_database(request))
        environment = {**config.get("shell_environment_policy.set", {}), **database}
        environment.pop("PORTAL_EXECUTION_ID", None)
        mirror_product_env(environment, "PORTAL_EXECUTION_CONTEXT_FILE", str(cls._execution_context_path(request)))
        config["shell_environment_policy.set"] = environment
        return config

    @staticmethod
    def _execution_context_path(request: ExecutionRequest) -> Path:
        import hashlib
        identity = request.session.binding_id or request.execution_id
        digest = hashlib.sha256(identity.encode()).hexdigest()
        from .config import execution_state_root
        root = execution_state_root() / "codex_context"
        return root / (digest + ".json")

    @classmethod
    def _write_execution_context(cls, request: ExecutionRequest) -> None:
        from management_portal.shared_state import atomic_replace_json

        # The turn's tools report which execution they belong to; permissions
        # are the Resident's own, so nothing else travels with them.
        path = cls._execution_context_path(request)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_replace_json(path, {"execution_id": request.execution_id})

    @staticmethod
    def _harness_tool_schemas(
        harness_tools: tuple[HarnessTool, ...] = (),
    ) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        names: set[str] = set()
        for harness_tool in harness_tools:
            if not bool(getattr(harness_tool, "expose_schema", True)):
                continue
            name = str(harness_tool.name or "").strip()
            if (
                not name
                or not isinstance(harness_tool.description, str)
                or not isinstance(harness_tool.input_schema, dict)
                or not callable(harness_tool.handler)
            ):
                raise ValueError("Codex Harness tools require a valid contract")
            if name in names:
                raise ValueError(f"Duplicate Codex dynamic tool: {name}")
            names.add(name)
            tools.append({
                "type": "function",
                "name": name,
                "description": harness_tool.description,
                "inputSchema": dict(harness_tool.input_schema),
            })
        return tools

    @staticmethod
    def _harness_tool_dispatcher(
        harness_tools: tuple[HarnessTool, ...] = (),
        tool_batch_processor: AgentLoopToolBatchProcessor | None = None,
    ) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
        harness_handlers = {
            str(tool.name): tool.handler
            for tool in harness_tools
        }
        if not harness_handlers:
            return None

        def process_result(
            params: dict[str, Any],
            *,
            tool_name: str,
            arguments: dict[str, Any],
            result: dict[str, Any],
            source: str,
        ) -> dict[str, Any]:
            if tool_batch_processor is None:
                return result
            try:
                processed = tool_batch_processor.process(
                    AgentLoopToolBatch(items=(AgentLoopToolResult(
                        call_id=str(params.get("callId") or ""),
                        tool_name=tool_name,
                        arguments=dict(arguments),
                        result=dict(result),
                        source=source,
                    ),))
                )
                if (
                    not isinstance(processed, AgentLoopToolBatch)
                    or len(processed.items) != 1
                    or not isinstance(processed.items[0].result, dict)
                ):
                    raise TypeError(
                        "tool batch processor must preserve one result"
                    )
                return dict(processed.items[0].result)
            except InterruptedError:
                raise
            except Exception:
                # Result governance is optional. Never hide an otherwise valid
                # Provider tool result when a Harness lifecycle hook fails.
                logging.getLogger(__name__).exception("Codex Harness tool result processing failed")
                return result

        def dispatch(params: dict[str, Any]) -> dict[str, Any]:
            tool_name = str(params.get("tool") or "").strip()
            arguments = params.get("arguments")
            if not tool_name or not isinstance(arguments, dict):
                raise ValueError(
                    "Dynamic tool call requires tool and object arguments"
                )
            harness_handler = harness_handlers.get(tool_name)
            if harness_handler is not None:
                try:
                    with tool_call_scope(params.get("callId")):
                        content = str(harness_handler(arguments) or "")
                except Exception as exc:
                    return {
                        "success": False,
                        "contentItems": [{
                            "type": "inputText",
                            "text": (
                                "Harness tool failed: "
                                f"{type(exc).__name__}: {str(exc)[:1000]}"
                            ),
                        }],
                    }
                result = {
                    "success": True,
                    "contentItems": [{
                        "type": "inputText",
                        "text": content,
                    }],
                }
                return process_result(
                    params,
                    tool_name=tool_name,
                    arguments=arguments,
                    result=result,
                    source="harness",
                )
            return {
                "success": False,
                "contentItems": [{"type": "inputText", "text": f"Unknown Harness tool: {tool_name}"}],
            }

        return dispatch

    @staticmethod
    def _sandbox_options(
        options: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        sandbox = str(options.get("sandbox") or "").strip()
        raw_policy = options.get("sandbox_policy")
        sandbox_policy = (
            dict(raw_policy)
            if isinstance(raw_policy, dict)
            else None
        )
        if raw_policy is not None and not isinstance(raw_policy, dict):
            raise ValueError("Codex sandbox_policy must be an object")
        permissions = str(options.get("permissions") or "").strip()
        if permissions:
            # A named Codex permission profile is a complete, higher-level
            # policy. Never send either low-level sandbox representation with
            # it to app-server.
            return "", None
        if sandbox and sandbox_policy is not None:
            raise ValueError(
                "Codex runtime options cannot combine sandbox and sandbox_policy"
            )
        return sandbox, sandbox_policy


    @classmethod
    def _start_thread(
        cls,
        client: Any,
        request: ExecutionRequest,
        cancellation_requested: Callable[[], bool] | None = None,
        harness_tools: tuple[HarnessTool, ...] = (),
        developer_instructions: str = "",
    ) -> str:
        options = request.runtime_options
        params = {
            "cwd": request.cwd,
            "ephemeral": bool(options.get("ephemeral", False)),
            "threadSource": str(options.get("thread_source") or "user"),
        }
        thread_config = cls._runtime_thread_config(request)
        params["config"] = thread_config
        if str(developer_instructions or "").strip():
            params["developerInstructions"] = str(developer_instructions)
        dynamic_tools = cls._harness_tool_schemas(harness_tools)
        if dynamic_tools:
            params["dynamicTools"] = dynamic_tools
        permissions = str(options.get("permissions") or "")
        sandbox, sandbox_policy = cls._sandbox_options(options)
        if not sandbox:
            sandbox = thread_sandbox_from_policy(sandbox_policy)
        if permissions:
            params["permissions"] = permissions
        elif sandbox:
            params["sandbox"] = sandbox
        for source, target in (
            ("model", "model"),
            ("service_tier", "serviceTier"),
            ("approvals_reviewer", "approvalsReviewer"),
            ("approval_policy", "approvalPolicy"),
        ):
            value = options.get(source)
            if value not in (None, ""):
                params[target] = value
        result = client.start_thread(
            params,
            cancellation_requested=cancellation_requested,
        )
        thread = result.get("thread") if isinstance(result, dict) else {}
        thread_id = str(
            thread.get("id") if isinstance(thread, dict) else ""
        ).strip()
        if not thread_id:
            raise RuntimeError("Codex thread/start returned no thread id")
        return thread_id


    def _resolve_client(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> tuple[CodexAccountRoute, Any, str, bool]:
        request = invocation.request
        session = invocation.session
        if session.backend_session_id:
            if not session.backend_route_id:
                raise ValueError(
                    "existing Codex session requires backend_route_id"
                )
            route = self.account_registry.get(
                session.backend_route_id
            )
            client = self.client_factory(route.socket_path)
            thread_id = session.backend_session_id
            return (
                route,
                client,
                thread_id,
                False,
            )
        candidates = self.account_registry.candidates(
            requested_account_id=session.backend_route_id,
            allow_fallback=bool(
                request.runtime_options.get(
                    "allow_backend_route_fallback",
                    True,
                )
            ),
        )
        current = candidates[0]
        unavailable = None
        for index, route in enumerate(candidates):
            if index:
                if not context.reassign_backend_route(route.account_id):
                    continue
                context.emit(
                    "backend_route_fallback",
                    {
                        "provider": self.provider_id,
                        "from_route_id": current.account_id,
                        "to_route_id": route.account_id,
                        "reason": type(unavailable).__name__,
                    },
                )
                current = route
            client = self.client_factory(route.socket_path)
            try:
                return (
                    route,
                    client,
                    self._start_thread(
                        client,
                        request,
                        context.is_cancelled,
                        invocation.harness_tools,
                        invocation.system_context if invocation.attach_system_context else "",
                    ),
                    bool(invocation.attach_system_context and invocation.system_context),
                )
            except CodexAppServerUnavailable as exc:
                unavailable = exc
        raise unavailable

    def configure_auto_compact_threshold(
        self,
        *,
        invocation: BackendInvocation,
        threshold_percent: int,
        max_context_tokens: int = 0,
    ) -> InvocationTransformResult:
        """Configure Codex's native compactor from model catalog metadata.

        ``max_context_tokens`` bounds the limit that the share of the window
        gives (0: no bound).
        """

        if invocation.request.operation not in {"create", "turn"}:
            return InvocationTransformResult(
                invocation,
                False,
                "operation_not_supported",
            )
        observation = self.observe_context_window(invocation=invocation)
        if not observation.tokens:
            return InvocationTransformResult(
                invocation,
                False,
                observation.reason or "model_context_window_unavailable",
                dict(observation.metrics),
            )
        options = dict(invocation.request.runtime_options)
        raw_overrides = options.get("config_overrides")
        if raw_overrides is not None and not isinstance(
            raw_overrides,
            dict,
        ):
            raise ValueError("Codex config_overrides must be an object")
        config_overrides = dict(raw_overrides or {})
        effective_context_window = observation.tokens
        requested_percent = max(1, min(99, int(threshold_percent)))
        requested_limit = max(
            1,
            (effective_context_window * requested_percent) // 100,
        )
        if _positive_int(max_context_tokens):
            requested_limit = min(requested_limit, _positive_int(max_context_tokens))
        previous_limit = _positive_int(
            config_overrides.get("model_auto_compact_token_limit")
        )
        # Enabling a supposedly earlier policy must not accidentally relax an
        # already more aggressive explicit limit.
        applied_limit = (
            min(previous_limit, requested_limit)
            if previous_limit
            else requested_limit
        )
        previous_scope = str(
            config_overrides.get("model_auto_compact_token_limit_scope")
            or ""
        ).strip()
        config_overrides["model_auto_compact_token_limit"] = applied_limit
        config_overrides["model_auto_compact_token_limit_scope"] = "total"
        options["config_overrides"] = config_overrides
        request = replace(
            invocation.request,
            runtime_options=options,
        )
        metrics: dict[str, Any] = {
            **dict(observation.metrics),
            "requested_threshold_percent": requested_percent,
            "auto_compact_token_limit": applied_limit,
            "effective_threshold_percent": round(
                (applied_limit / effective_context_window) * 100,
                2,
            ),
        }
        if previous_limit:
            metrics["previous_auto_compact_token_limit"] = previous_limit
        if previous_scope:
            metrics["previous_auto_compact_scope"] = previous_scope
        return InvocationTransformResult(
            replace(invocation, request=request),
            True,
            "native_auto_compact_threshold_configured",
            metrics,
        )


    def observe_context_window(
        self,
        *,
        invocation: BackendInvocation,
    ) -> ContextWindowObservation:
        """Resolve the effective model context window without a model call."""

        options = dict(invocation.request.runtime_options)
        model = str(options.get("model") or "").strip()
        if not model:
            return ContextWindowObservation(0, "model_unspecified")
        route_id, model_info = self._model_catalog_entry(
            model=model,
            preferred_route_id=invocation.session.backend_route_id,
        )
        raw_overrides = options.get("config_overrides")
        if raw_overrides is not None and not isinstance(
            raw_overrides,
            dict,
        ):
            raise ValueError("Codex config_overrides must be an object")
        config_overrides = dict(raw_overrides or {})
        configured_context_window = _positive_int(
            config_overrides.get("model_context_window")
        )
        catalog_context_window = _positive_int(
            model_info.get("context_window")
        ) or _positive_int(model_info.get("max_context_window"))
        context_window = configured_context_window or catalog_context_window
        if not context_window:
            session = invocation.session
            if session.backend_session_id and session.backend_route_id and not invocation.is_new_backend_session:
                rollout = self.account_registry.find_thread_rollout(
                    session.backend_route_id, session.backend_session_id,
                    include_archived=True,
                )
                observed_window = read_codex_context_window(rollout, model=model) if rollout else 0
                if observed_window:
                    return ContextWindowObservation(
                        observed_window,
                        metrics={
                            "model": model,
                            "effective_context_window": observed_window,
                            "context_window_source": "provider_thread_observation",
                            "model_catalog_route_id": session.backend_route_id,
                        },
                    )
            return ContextWindowObservation(
                0,
                "model_context_window_unavailable",
                {"model": model},
            )
        effective_percent = _positive_int(
            model_info.get("effective_context_window_percent")
        ) or 95
        effective_percent = max(1, min(100, effective_percent))
        effective_context_window = (
            context_window * effective_percent
        ) // 100
        metrics: dict[str, Any] = {
            "model": model,
            "model_catalog_route_id": route_id,
            "catalog_context_window": catalog_context_window,
            "effective_context_window": effective_context_window,
            "effective_context_window_percent": effective_percent,
        }
        if configured_context_window:
            metrics["configured_context_window"] = configured_context_window
        return ContextWindowObservation(
            effective_context_window,
            metrics=metrics,
        )

    def _model_catalog_entry(
        self,
        *,
        model: str,
        preferred_route_id: str,
    ) -> tuple[str, dict[str, Any]]:
        routes = list(self.account_registry.routes())
        clean_route_id = str(preferred_route_id or "").strip()
        if not clean_route_id:
            try:
                clean_route_id = str(
                    self.account_registry.default_for_new_session().account_id
                    or ""
                ).strip()
            except Exception:
                clean_route_id = ""
        if clean_route_id:
            routes.sort(
                key=lambda route: route.account_id != clean_route_id
            )
        for route in routes:
            if getattr(route, "kind", "") == "api_profile":
                # A Model API declares its models' context windows itself.
                declared = _model_api_entry(route.api_id, model)
                if declared:
                    return route.account_id, declared
                continue
            try:
                payload = json.loads(
                    (route.codex_home / "models_cache.json").read_text(
                        encoding="utf-8"
                    )
                )
            except Exception:
                continue
            entries = (
                payload.get("models")
                if isinstance(payload, dict)
                and isinstance(payload.get("models"), list)
                else []
            )
            for raw_entry in entries:
                if not isinstance(raw_entry, dict):
                    continue
                slug = str(
                    raw_entry.get("slug")
                    or raw_entry.get("model")
                    or raw_entry.get("id")
                    or ""
                ).strip()
                if slug == model:
                    return route.account_id, dict(raw_entry)
        return "", {}

    def execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
    ) -> BackendResult:
        # The attempt's thread and the requests it reported live.
        usage_scope: dict[str, Any] = {}
        try:
            return self._execute(invocation, context, usage_scope)
        except CodexTurnDetached as exc:
            raise ExecutionRecoveryDeferred(str(exc)) from exc
        finally:
            self._report_rollout_usage(context, usage_scope)

    def _report_rollout_usage(self, context: BackendContext, usage_scope: Mapping[str, Any]) -> None:
        """Report the requests of the Execution that no live notification reported.

        A request that ends while one Worker hands the turn to the next, or a
        subagent's before Portal joins its thread, reaches no Worker; Codex
        keeps every request in the rollouts.  This runs after each attempt,
        also when it fails, is cancelled or hands over.
        """

        route = usage_scope.get("route")
        thread = usage_scope.get("thread")
        thread_id = str(thread() if callable(thread) else "")
        since = str(getattr(context, "execution_started_at", "") or "")
        if route is None or not thread_id or not since:
            return
        reported = set(usage_scope.get("reported") or ())
        try:
            rollout = self.account_registry.find_thread_rollout(route.account_id, thread_id)
            sources = [("", rollout, 0)] if rollout else []
            sources.extend(
                (f"{child}:", path, start)
                for child, path, start in codex_subagent_rollouts(route.codex_home, thread_id, since=since)
            )
            for prefix, path, start in sources:
                for event_id, delta, written_at in read_codex_request_usage(
                    path, since=since, history_start=start,
                ) or ():
                    if prefix + event_id not in reported:
                        reported.add(prefix + event_id)
                        context.emit("token_usage_delta", {
                            "event_id": prefix + event_id, "usage": delta, "occurred_at": written_at,
                        })
        except Exception:  # noqa: BLE001 - usage reporting never fails a turn
            LOGGER.warning("Codex rollout usage of thread %s could not be reported", thread_id, exc_info=True)

    def _execute(
        self,
        invocation: BackendInvocation,
        context: BackendContext,
        usage_scope: dict[str, Any],
    ) -> BackendResult:
        request = invocation.request
        if codex_goal_command(request.runtime_options) and bool(request.runtime_options.get("ephemeral", False)):
            raise ValueError("Codex native goals require a persistent thread")
        route, client, thread_id, system_context_bound_to_thread = self._resolve_client(invocation, context)
        session = CodexSession(
            invocation=invocation, context=context, route=route, client=client, thread_id=thread_id,
            system_context_bound_to_thread=system_context_bound_to_thread,
        )
        usage_scope.update(route=route, thread=lambda: session.thread_id)
        context.set_interrupt_target(
            thread_id,
            backend_route_id=route.account_id,
        )
        operation = request.operation
        handler = {
            "release": self._release,
            "create": self._create,
            "fork": self._fork,
            "compact": self._compact,
        }.get(operation)
        if handler is not None:
            return handler(session)
        if operation != "turn":
            raise ValueError(f"unsupported Codex backend operation: {operation}")
        return CodexTurnRun(self, session, usage_scope).run()

    def _operation_result(
        self,
        session: CodexSession,
        output: Mapping[str, Any] | None = None,
        **result: Any,
    ) -> BackendResult:
        """The result of an operation that runs no turn."""

        return BackendResult(
            output={
                "message": "",
                "provider": self.provider_id,
                "operation": session.request.operation,
                **(output or {}),
            },
            backend_route_id=session.route.account_id,
            **result,
        )

    def _release(self, session: CodexSession) -> BackendResult:
        session.client.unsubscribe_thread(session.thread_id)
        return self._operation_result(session)

    def _create(self, session: CodexSession) -> BackendResult:
        seed = session.request.metadata.get("fork_context_input") or {}
        if seed:
            self._import_session_history(session, seed)
        return self._operation_result(
            session,
            {"context_result": "preserved_import" if seed else "fresh"},
            backend_session_id=session.thread_id,
            available_harness_tool_names=session.available_harness_tool_names(),
        )

    def _import_session_history(self, session: CodexSession, seed: Mapping[str, Any]) -> None:
        """Give a new thread the history its creation preserves from another session."""

        request = session.request
        if not request.metadata.get("preserve_session_context"):
            raise ValueError("Session creation history requires an explicit preservation operation")
        from .agent_data_store import get_agent_data_store
        from .session_history_import import validate_codex_preservation
        store = get_agent_data_store()
        validate_codex_preservation(store.fork_context_segments(seed["segments"]))
        session.history_delivery(seed["segments"], store=store,
                                 agent_id=seed["source_agent_id"]).initialize(session.thread_id)
        # create has no turn for the projector to mark. Keep the
        # acknowledged window attached to this session for later forks.
        imported_session = store.ensure_provider_session(
            provider=self.provider_id, backend_route_id=session.route.account_id,
            backend_session_id=session.thread_id, agent_id=request.agent_id, actor_ref=request.actor,
        )
        store.record_context_received(seed["source_agent_id"], imported_session["provider_session_ref"],
            [item["segment_id"] for item in seed["segments"]], actor_ref=request.actor)

    def _fork(self, session: CodexSession) -> BackendResult:
        try:
            forked = session.client.fork_thread(
                self._fork_params(session.request, session.thread_id),
                cancellation_requested=session.provider_cancelled,
            )
        except CodexAppServerError as exc:
            if not isinstance(exc, CodexThreadNotFound):
                raise
            replacement = self._restore_fork_source(session)
            if replacement is None:
                raise
            forked = {"thread": {"id": replacement}}
        thread = (
            forked.get("thread")
            if isinstance(forked.get("thread"), dict)
            else {}
        )
        forked_id = str(thread.get("id") or "")
        if not forked_id:
            raise RuntimeError("Codex thread/fork returned no thread id")
        return self._operation_result(
            session,
            {"forked_from_session_id": session.thread_id},
            backend_session_id=forked_id,
        )

    @classmethod
    def _fork_params(cls, request: ExecutionRequest, thread_id: str) -> dict[str, Any]:
        options = request.runtime_options
        sandbox, sandbox_policy = cls._sandbox_options(options)
        if not sandbox:
            sandbox = thread_sandbox_from_policy(sandbox_policy)
        params = {
            "threadId": thread_id,
            "ephemeral": bool(options.get("ephemeral", False)),
            "excludeTurns": bool(options.get("exclude_turns", True)),
            "threadSource": str(options.get("thread_source") or "user"),
            "config": cls._runtime_thread_config(request),
        }
        for source, target in (
            ("cwd", "cwd"),
            ("approval_policy", "approvalPolicy"),
        ):
            value = options.get(source)
            if value not in (None, ""):
                params[target] = value
        if sandbox:
            params["sandbox"] = sandbox
        return params

    def _restore_fork_source(self, session: CodexSession) -> str | None:
        """Fork a source thread app-server lost from the history Portal kept of it.

        The new thread receives the source's history from the agent trace;
        ``None`` when Portal kept none.
        """

        from .agent_data_store import get_agent_data_store

        request = session.request
        store = get_agent_data_store()
        agent_id = str(request.metadata.get("resident_id") or request.agent_id)
        snapshot = store.provider_context_snapshot(
            provider=self.provider_id, backend_route_id=session.route.account_id,
            backend_session_id=session.thread_id, agent_id=agent_id,
        )
        if not snapshot or not snapshot.get("segments"):
            return None
        references = snapshot["segments"]
        if request.metadata.get("preserve_session_context"):
            from .session_history_import import validate_codex_preservation
            validate_codex_preservation(references)
        replacement = self._start_thread(
            session.client, request, session.context.is_cancelled, session.invocation.harness_tools,
        )
        delivery = session.history_delivery(references, agent_id=agent_id, store=store)
        delivery.initialize(replacement)
        session.context.emit("fork_source_restored", {
            "provider": self.provider_id, "source_thread_id": session.thread_id,
            "thread_id": replacement, "source": "agent_trace",
        })
        return replacement

    def _compact(self, session: CodexSession) -> BackendResult:
        request, context, route = session.request, session.context, session.route
        thread_id = session.thread_id
        self._write_execution_context(request)
        fork_history = request.metadata.get("fork_context_input") or {}
        if fork_history:
            if (
                str(request.metadata.get("initial_context_policy") or "")
                != "compact_before_first_turn"
            ):
                raise ValueError(
                    "Fork context may only accompany compact_before_first_turn"
                )

            session.history_delivery(fork_history["segments"]).initialize(thread_id)
        previous_compaction = self._latest_compaction(route, thread_id)
        try:
            usage = session.client.compact_thread(
                thread_id,
                config=self._runtime_thread_config(request),
                cancellation_requested=session.provider_cancelled,
                **self._compaction_wait_options(),
            )
        except (TimeoutError, CodexAppServerUnavailable) as exc:
            context.set_provider_recovery_state({"kind": "thread_writer_busy", "thread_id": thread_id})
            raise ExecutionRecoveryDeferred(
                "Codex compaction recovery wait deadline reached"
            ) from exc
        self._record_new_compaction(
            context=context,
            route=route,
            thread_id=thread_id,
            previous_fingerprint=(
                previous_compaction.fingerprint
                if previous_compaction is not None
                else ""
            ),
            wait_seconds=1.0,
            required=True,
        )
        return self._operation_result(
            session,
            backend_session_id=thread_id,
            usage=dict(usage) if isinstance(usage, dict) else {},
        )

    @staticmethod
    def _compaction_wait_options() -> dict[str, float]:
        return {
            "timeout_seconds": _bounded_wait_seconds(
                "PORTAL_CODEX_COMPACTION_TIMEOUT_SECONDS",
                DEFAULT_COMPACT_TOTAL_TIMEOUT_SECONDS,
            ),
            "no_progress_timeout_seconds": _bounded_wait_seconds(
                "PORTAL_CODEX_COMPACTION_NO_PROGRESS_TIMEOUT_SECONDS",
                DEFAULT_COMPACT_NO_PROGRESS_TIMEOUT_SECONDS,
            ),
        }

    def interrupt(
        self,
        *,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        # A lost Worker may not have learned the turn id yet; the thread's
        # turn in progress is then the one it started.
        find_active_turn = bool(runtime_options.get("interrupt_active_turn"))
        if not backend_session_id or not turn_id or not backend_route_id:
            if not (
                backend_session_id
                and backend_route_id
                and (isinstance(runtime_options.get("goal"), dict) or find_active_turn)
            ):
                return False
        route = self.account_registry.get(backend_route_id)
        client = self.client_factory(route.socket_path)
        goal_command = runtime_options.get("goal")
        paused = False
        try:
            if not turn_id and find_active_turn:
                turn_id = client.active_turn_id(backend_session_id)
            if turn_id:
                client.interrupt_turn(backend_session_id, turn_id)
        finally:
            if isinstance(goal_command, dict):
                try:
                    client.request(
                        "thread/goal/set",
                        {"threadId": backend_session_id, "status": "paused"},
                        timeout=30.0,
                    )
                    paused = True
                except Exception:
                    paused = False
        return bool(turn_id) or paused
