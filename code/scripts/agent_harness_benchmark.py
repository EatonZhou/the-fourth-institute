#!/usr/bin/env python3
"""Run deterministic, serial effectiveness checks for AgentHarness plugins.

The benchmark deliberately makes no model calls.  It measures mechanisms the
Portal owns (prompt/schema/tool batch transforms, exact artifact recall, native
threshold configuration) without conflating those
facts with stochastic model-quality claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from management_portal.execution.backend_contracts import BackendContext, BackendInvocation  # noqa: E402
from management_portal.execution.codex_backend import CodexBackend  # noqa: E402
from management_portal.execution.requests import (  # noqa: E402
    build_agent_session_request,
)
from management_portal.execution.harness_artifact_search import (  # noqa: E402
    READ_HARNESS_ARTIFACT_TOOL_NAME,
    SEARCH_HARNESS_ARTIFACT_TOOL_NAME,
    ArtifactSearchAndSliceFeature,
)
from management_portal.execution.harness import AgentLoopToolBatch, AgentLoopToolResult
from management_portal.execution.harness_capabilities import provider_capabilities
from management_portal.execution.harness_tool_batches import tool_result_text
from management_portal.execution.harness_artifact_recall import artifact_scope_for_invocation
from management_portal.execution.harness_artifacts import (  # noqa: E402
    HarnessArtifactStore,
)
from management_portal.execution.harness_early_compaction import (  # noqa: E402
    EarlyContextCompactionFeature,
)
from management_portal.execution.harness_history import (  # noqa: E402
    approximate_value_tokens,
)
from management_portal.execution.harness_read_dedup import (  # noqa: E402
    HashAwareReadDedupFeature,
)
from management_portal.execution.harness_tool_granularity import (  # noqa: E402
    TOOL_CALL_GRANULARITY_POLICY_MARKER,
    ToolCallGranularityFeature,
)
from management_portal.execution.harness_tool_output_offloading import (  # noqa: E402
    ToolOutputOffloadingFeature,
)
from management_portal.execution.harness_unified_resource_recall import (  # noqa: E402
    READ_PORTAL_RESOURCE_TOOL_NAME,
    SEARCH_PORTAL_RESOURCE_TOOL_NAME,
    UnifiedResourceRecallFeature,
)
from management_portal.execution.session_manager import RuntimeSession  # noqa: E402


def _context() -> BackendContext:
    return BackendContext(
        emit_event=lambda *_args: None,
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args, **_kwargs: None,
    )


def _invocation(
    *,
    runtime_options: dict[str, Any] | None = None,
    cwd: Path = REPO_ROOT,
    new_session: bool = False,
    prompt: str = "Inspect the Harness implementation and report the result.",
) -> BackendInvocation:
    backend_session_id = "" if new_session else (
        "019f0000-0000-7000-8000-000000000001"
    )
    binding_id = "benchmark:agent-harness"
    route_id = "" if new_session else "benchmark"
    request = build_agent_session_request(
        actor="harness-benchmark",
        prompt=prompt,
        provider="codex",
        operation="turn",
        correlation_id="benchmark:agent-harness",
        cwd=str(cwd),
        runtime_options=dict(runtime_options or {}),
        binding_id=binding_id,
        backend_route_id=route_id,
    )
    return BackendInvocation(
        request=request,
        session=RuntimeSession(
            mode="persistent",
            binding_id=binding_id,
            backend_session_id=backend_session_id,
            backend_route_id=route_id,
            is_new_backend_session=new_session,
        ),
        user_prompt=request.prompt,
        system_context="",
        attach_system_context=False,
        is_new_backend_session=new_session,
        harness_capabilities=provider_capabilities("codex"),
    )




def _tool_granularity() -> dict[str, Any]:
    feature = ToolCallGranularityFeature()
    invocation = _invocation()
    outcome = feature.apply(
        provider=SimpleNamespace(provider_id="codex"),
        invocation=invocation,
        context=_context(),
        config={},
    )
    repeated = feature.apply(
        provider=SimpleNamespace(provider_id="codex"),
        invocation=outcome.invocation,
        context=_context(),
        config={},
    )
    tokens_before = approximate_value_tokens(invocation.user_prompt)
    tokens_after = approximate_value_tokens(outcome.invocation.user_prompt)
    return {
        "feature_id": "tool_call_granularity",
        "status": outcome.status,
        "policy_attached_once": (
            outcome.invocation.user_prompt.count(
                TOOL_CALL_GRANULARITY_POLICY_MARKER
            ) == 1
        ),
        "idempotent_second_apply": repeated.status == "skipped",
        "prompt_tokens_added": max(0, tokens_after - tokens_before),
        "quality_scope": (
            "Mechanism verified; model adoption requires multi-turn "
            "production telemetry."
        ),
    }


class _CommandClient:
    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []

    def execute_command(self, params: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        self.commands.append(dict(params))
        return {"exitCode": 0, "stdout": "benchmark-result", "stderr": ""}




class _CatalogRegistry:
    def __init__(self, route: Any) -> None:
        self.route = route

    def routes(self) -> list[Any]:
        return [self.route]

    def default_for_new_session(self) -> Any:
        return self.route


def _early_compaction() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="portal-harness-model-") as raw:
        root = Path(raw)
        (root / "models_cache.json").write_text(
            json.dumps({
                "models": [{
                    "slug": "benchmark-model",
                    "context_window": 272_000,
                    "effective_context_window_percent": 95,
                }],
            }),
            encoding="utf-8",
        )
        route = SimpleNamespace(
            account_id="benchmark",
            codex_home=root,
        )
        backend = CodexBackend(
            account_registry=_CatalogRegistry(route),
        )
        invocation = _invocation(runtime_options={"model": "benchmark-model"})
        outcome = EarlyContextCompactionFeature().apply(
            provider=backend,
            invocation=invocation,
            context=_context(),
            config={},
        )
        configured_limit = outcome.invocation.request.runtime_options[
            "config_overrides"
        ]["model_auto_compact_token_limit"]
        return {
            "feature_id": "early_context_compaction",
            "status": outcome.status,
            **dict(outcome.metrics),
            "configured_limit_verified": configured_limit == 180_880,
        }


def _batch(outputs):
    return AgentLoopToolBatch(items=tuple(
        AgentLoopToolResult(call_id=call, tool_name=name, arguments=args,
                            result={"success": True, "contentItems": [{"type": "inputText", "text": content}]})
        for call, name, content, args in outputs
    ))


def _read_dedup() -> dict[str, Any]:
    repeated = "stable-read-result\n" + "R" * 32_000
    transformed = HashAwareReadDedupFeature().after_tool_batch(
        batch=_batch([(f"read-{i}", "read_file", repeated, {"path": "same.py"}) for i in range(3)]),
        provider=None, invocation=_invocation(), context=_context(), config={}, state={},
    )
    return {
        "feature_id": "hash_aware_read_dedup",
        "status": "applied" if transformed.applied else "skipped",
        **transformed.metrics,
        "first_exact_output_preserved": tool_result_text(transformed.batch.items[0]) == repeated,
    }


def _offload(store, original):
    invocation = _invocation(new_session=True, runtime_options={"agent_harness": {"mode": "manual", "features": {"tool_output_offloading": {"enabled": True, "timing": "after_tool_batch"}}}})
    feature = ToolOutputOffloadingFeature(store)
    invocation = feature.prepare_invocation(invocation)
    transformed = feature.after_tool_batch(
        batch=_batch([("large", "exec_command", original, {"cmd": "benchmark"})]),
        provider=None, invocation=invocation, context=_context(), config={}, state={},
    )
    import re
    match = re.search(r"ha1_[0-9a-f]{64}", tool_result_text(transformed.batch.items[0]))
    if match is None:
        raise AssertionError(f"Offloading failed: {transformed.reason}")
    return transformed, match.group(), artifact_scope_for_invocation(invocation)


def _tool_output_offloading() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="portal-harness-artifact-") as raw:
        store = HarnessArtifactStore(Path(raw) / "artifacts")
        original = "BEGIN\n" + "large-output-line\n" * 20_000 + "END"
        transformed, handle, scope = _offload(store, original)
        chunks: list[str] = []
        offset = 0
        while True:
            result = store.read(
                handle,
                scope=scope,
                offset=offset,
                limit=32_000,
            )
            chunks.append(str(result["content"]))
            if result["eof"]:
                break
            offset = int(result["next_offset"])
        recalled = "".join(chunks)
        return {
            "feature_id": "tool_output_offloading",
            "status": "applied" if transformed.applied else "skipped",
            **dict(transformed.metrics),
            "exact_recall_verified": recalled == original,
            "artifact_sha256_verified": (
                hashlib.sha256(recalled.encode("utf-8")).hexdigest()
                == store.read(handle, scope=scope, limit=1)["sha256"]
            ),
            "recall_chunk_count": len(chunks),
        }


def _artifact_search_and_slice() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="portal-harness-search-") as raw:
        store = HarnessArtifactStore(Path(raw) / "artifacts")
        needle = (
            "UNIQUE_FAILURE_CODE=DB-9472 root cause is a stale scheduler "
            "lease; renew the owning claim before retry."
        )
        original = (
            "ordinary build output without actionable errors\n" * 24_000
            + needle
            + "\nordinary trailing output\n" * 24_000
        )
        transformed, handle, scope = _offload(store, original)
        feature = ArtifactSearchAndSliceFeature(store)
        outcome = feature.apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=_invocation(new_session=True),
            context=_context(),
            config={"enabled": True},
        )
        tools = {tool.name: tool for tool in outcome.invocation.harness_tools}
        search_raw = tools[SEARCH_HARNESS_ARTIFACT_TOOL_NAME].handler({
            "handle": handle,
            "query": "DB-9472 stale scheduler lease",
            "max_hits": 3,
            "context_chars": 360,
        })
        search_result = json.loads(search_raw)
        exact_offset = original.index(needle)
        read_raw = tools[READ_HARNESS_ARTIFACT_TOOL_NAME].handler({
            "handle": handle,
            "offset": exact_offset,
            "limit": len(needle),
        })
        read_result = json.loads(read_raw)
        full_tokens = approximate_value_tokens(original)
        returned_tokens = approximate_value_tokens(search_raw) + (
            approximate_value_tokens(read_raw)
        )
        schema_tokens = int(
            outcome.metrics.get("artifact_recall_schema_tokens_estimated")
            or 0
        )
        net_tokens = returned_tokens + schema_tokens
        avoided = max(0, full_tokens - net_tokens)
        return {
            "feature_id": "artifact_search_and_slice",
            "status": outcome.status,
            **dict(outcome.metrics),
            "comparison_baseline": "read_entire_offloaded_tool_output",
            "source_chars": len(original),
            "full_content_tokens_estimated": full_tokens,
            "search_and_exact_slice_tokens_estimated": returned_tokens,
            "net_tokens_including_schema_estimated": net_tokens,
            "tokens_avoided_estimated": avoided,
            "net_token_reduction_percent": round(
                (avoided / full_tokens) * 100,
                2,
            ) if full_tokens else 0,
            "search_hit_verified": (
                search_result.get("hit_count") == 1
                and needle in search_result["hits"][0]["content"]
            ),
            "exact_slice_verified": read_result.get("content") == needle,
            "source_read_calls_replaced": (
                (len(original) + 31_999) // 32_000
            ),
            "harness_tool_calls": 2,
        }


class _BenchmarkResourceService:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[tuple[str, str, int]] = []

    def read_content(
        self,
        address: str,
        *,
        actor: str,
        max_bytes: int,
    ) -> tuple[bytes, str, dict[str, Any]]:
        self.calls.append((address, actor, max_bytes))
        return (
            self.content,
            "text/markdown; charset=utf-8",
            {"resource_id": address, "title": "Benchmark decision"},
        )


def _unified_resource_recall() -> dict[str, Any]:
    address = "decision_123456789abc"
    needle = (
        "PORTAL_INVARIANT: ExecutionService transports caller-composed "
        "prompts without interpreting business intent."
    )
    content_text = (
        "ordinary architecture decision paragraph\n" * 40_000
        + needle
        + "\nordinary decision appendix\n" * 40_000
    )
    service = _BenchmarkResourceService(content_text.encode())
    prompt = (
        "请核对引用资源中的执行层约束。\n"
        "<resource_references>\n"
        f'<resource address="{address}" id="{address}" '
        'type="decision" status="resolved">\n'
        "title: Benchmark decision\n"
        "</resource>\n"
        "</resource_references>"
    )
    feature = UnifiedResourceRecallFeature(lambda: service)
    outcome = feature.apply(
        provider=SimpleNamespace(provider_id="codex"),
        invocation=_invocation(new_session=True, prompt=prompt),
        context=_context(),
        config={"enabled": True},
    )
    tools = {tool.name: tool for tool in outcome.invocation.harness_tools}
    search_raw = tools[SEARCH_PORTAL_RESOURCE_TOOL_NAME].handler({
        "address": address,
        "query": "ExecutionService caller-composed prompts business intent",
        "max_hits": 3,
        "context_chars": 360,
    })
    search_result = json.loads(search_raw)
    exact_offset = content_text.index(needle)
    read_raw = tools[READ_PORTAL_RESOURCE_TOOL_NAME].handler({
        "address": address,
        "offset": exact_offset,
        "limit": len(needle),
    })
    read_result = json.loads(read_raw)
    denied = False
    try:
        tools[SEARCH_PORTAL_RESOURCE_TOOL_NAME].handler({
            "address": "decision_aaaaaaaaaaaa",
            "query": "private",
        })
    except PermissionError:
        denied = True
    full_tokens = approximate_value_tokens(content_text)
    returned_tokens = approximate_value_tokens(search_raw) + (
        approximate_value_tokens(read_raw)
    )
    schema_tokens = int(
        outcome.metrics.get("resource_recall_schema_tokens_estimated") or 0
    )
    net_tokens = returned_tokens + schema_tokens
    avoided = max(0, full_tokens - net_tokens)
    return {
        "feature_id": "unified_resource_recall",
        "status": outcome.status,
        **dict(outcome.metrics),
        "comparison_baseline": "inject_or_read_entire_resource_content",
        "source_chars": len(content_text),
        "full_content_tokens_estimated": full_tokens,
        "search_and_exact_slice_tokens_estimated": returned_tokens,
        "net_tokens_including_schema_estimated": net_tokens,
        "tokens_avoided_estimated": avoided,
        "net_token_reduction_percent": round(
            (avoided / full_tokens) * 100,
            2,
        ) if full_tokens else 0,
        "search_hit_verified": (
            search_result.get("hit_count") == 1
            and needle in search_result["hits"][0]["content"]
        ),
        "exact_slice_verified": read_result.get("content") == needle,
        "resource_service_read_count": len(service.calls),
        "turn_cache_verified": (
            len(service.calls) == 1 and read_result.get("cache_hit") is True
        ),
        "actor_authorization_verified": (
            bool(service.calls)
            and service.calls[0][1] == "harness-benchmark"
        ),
        "unreferenced_resource_denied": denied,
        "source_content_eagerly_injected": needle in prompt,
        "harness_tool_calls": 2,
    }






def run(repo_root: Path) -> dict[str, Any]:
    checks: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
        ("tool_call_granularity", _tool_granularity),
        ("unified_resource_recall", _unified_resource_recall),
        ("early_context_compaction", _early_compaction),
        ("hash_aware_read_dedup", _read_dedup),
        ("tool_output_offloading", _tool_output_offloading),
        ("artifact_search_and_slice", _artifact_search_and_slice),
    )
    results: list[dict[str, Any]] = []
    for order, (expected_id, check) in enumerate(checks, start=1):
        started = time.perf_counter_ns()
        result = check()
        if result.get("feature_id") != expected_id:
            raise RuntimeError(
                f"Harness benchmark returned wrong feature: {expected_id}"
            )
        results.append({
            "order": order,
            **result,
            "local_wall_ms": round(
                (time.perf_counter_ns() - started) / 1_000_000,
                3,
            ),
        })
    return {
        "schema": "tfi.agent-harness-benchmark.v1",
        "execution_mode": "serial_local_no_model_calls",
        "excluded_feature_ids": ["inline_media_history"],
        "measurement_model_calls": 0,
        "repo_root": str(repo_root),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(REPO_ROOT))
    args = parser.parse_args()
    payload = run(Path(args.repo).expanduser().resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
