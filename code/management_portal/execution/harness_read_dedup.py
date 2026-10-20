from __future__ import annotations

from typing import Any

from .harness import AgentLoopToolBatch, AgentLoopToolBatchTransform, PORTAL_CONTROLLED_TOOL_RESULT_SOURCES
from .harness_capabilities import PORTAL_TOOLS, requires
from .harness_history import approximate_value_tokens, bounded_int, canonical_json, value_sha256
from .harness_tool_batches import (
    numeric_delta,
    replace_tool_result_text,
    tool_result_text,
    utf8_bytes,
)


from .harness_tool_semantics import DEFAULT_READ_TOOL_NAMES, declared_tool_reads, tool_call_reads


HASH_AWARE_READ_DEDUP_FEATURE_ID = "hash_aware_read_dedup"
DEFAULT_MIN_DUPLICATE_OUTPUT_TOKENS = 256
MIN_DUPLICATE_OUTPUT_TOKENS = 32
MAX_DUPLICATE_OUTPUT_TOKENS = 20_000


class HashAwareReadDedupFeature:
    feature_id = HASH_AWARE_READ_DEDUP_FEATURE_ID
    # Replace repeated read results before returning a tool batch to the model.
    requires = requires({PORTAL_TOOLS})



    def after_tool_batch(
        self,
        *,
        batch: AgentLoopToolBatch,
        provider: Any,
        invocation: Any,
        context: Any,
        config: dict[str, Any],
        state: dict[str, Any],
        observer: Any = None,
    ) -> AgentLoopToolBatchTransform:
        del provider, context
        tool_names = _configured_read_tool_names(config)
        # Portal's own tools say themselves which calls only read.
        harness_tools = {
            str(getattr(tool, "name", "") or ""): tool
            for tool in tuple(getattr(invocation, "harness_tools", ()) or ())
        }
        configured_minimum_tokens = bounded_int(
            config.get("min_output_tokens"),
            default=DEFAULT_MIN_DUPLICATE_OUTPUT_TOKENS,
            minimum=MIN_DUPLICATE_OUTPUT_TOKENS,
            maximum=MAX_DUPLICATE_OUTPUT_TOKENS,
        )
        minimum_tokens = configured_minimum_tokens
        seen = state.setdefault("seen", {})
        transformed_items = list(batch.items)
        duplicate_count = 0
        duplicate_bytes = 0
        tokens_reclaimed = 0
        eligible_count = 0
        for index, item in enumerate(batch.items):
            if item.source not in PORTAL_CONTROLLED_TOOL_RESULT_SOURCES:
                continue
            content = tool_result_text(item)
            tool = harness_tools.get(item.tool_name)
            if (
                not content
                or approximate_value_tokens(content) < minimum_tokens
                or not (
                    declared_tool_reads(tool, item.arguments)
                    or tool_call_reads(item.tool_name, item.arguments, tool_names)
                )
            ):
                continue
            eligible_count += 1
            stable_result = getattr(tool, "stable_result", None)
            digest = value_sha256(stable_result(content) if callable(stable_result) else content)
            key = "\0".join((
                item.tool_name.casefold(),
                canonical_json(item.arguments),
                digest,
            ))
            prior = seen.get(key)
            if not isinstance(prior, dict):
                seen[key] = {
                    "call_id": item.call_id,
                    "sha256": digest,
                    "output_bytes": utf8_bytes(content),
                }
                continue
            placeholder = (
                "[AgentHarness removed a duplicate current-turn read-only "
                "tool result; identical content was already returned at "
                f"call_id:{prior.get('call_id') or '(unknown)'}; "
                f"sha256:{digest}; original_bytes:{utf8_bytes(content)}.]"
            )
            transformed_items[index] = replace_tool_result_text(
                item,
                placeholder,
            )
            duplicate_count += 1
            duplicate_bytes += numeric_delta(content, placeholder)
            tokens_reclaimed += max(
                0,
                approximate_value_tokens(content)
                - approximate_value_tokens(placeholder),
            )
        return AgentLoopToolBatchTransform(
            batch=AgentLoopToolBatch(items=tuple(transformed_items)),
            applied=duplicate_count > 0,
            reason=(
                "duplicate_current_turn_read_results_removed"
                if duplicate_count
                else "no_duplicate_current_turn_read_results"
            ),
            metrics={
                "configured_min_output_tokens": configured_minimum_tokens,
                "effective_min_output_tokens": minimum_tokens,
                "context_pressure_percent": (
                    observer.context_pressure_percent
                    if observer is not None
                    else 0
                ),
                "eligible_read_result_count": eligible_count,
                "duplicate_output_count_removed": duplicate_count,
                "duplicate_output_bytes_removed": duplicate_bytes,
                "estimated_tokens_reclaimed": tokens_reclaimed,
            },
        )




def _configured_read_tool_names(
    config: dict[str, Any],
) -> frozenset[str]:
    configured_tools = config.get("read_tool_names")
    return (
        frozenset(
            str(value).strip().casefold()
            for value in configured_tools
            if str(value or "").strip()
        )
        if isinstance(configured_tools, (list, tuple, set))
        else DEFAULT_READ_TOOL_NAMES
    )
