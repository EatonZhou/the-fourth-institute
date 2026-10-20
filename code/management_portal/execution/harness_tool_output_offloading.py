from __future__ import annotations

from .harness_tool_semantics import inline_media_bytes

import re
from pathlib import Path
from typing import Any

from .config import execution_state_root
from .harness import HARNESS_TIMING_AFTER_TOOL_BATCH, AgentLoopToolBatch, AgentLoopToolBatchTransform, PORTAL_CONTROLLED_TOOL_RESULT_SOURCES, harness_feature_config, harness_feature_enabled, harness_timing_includes
from .harness_capabilities import PORTAL_TOOLS, requires
from .harness_artifact_recall import (
    READ_HARNESS_ARTIFACT_TOOL_NAME,
    SEARCH_HARNESS_ARTIFACT_TOOL_NAME,
    artifact_scope_for_invocation,
    attach_artifact_recall_tools,
)
from .harness_artifacts import HarnessArtifactReference, HarnessArtifactStore
from .harness_history import bounded_int, clean_tool_names
from .harness_tool_batches import (
    numeric_delta,
    replace_tool_result_text,
    tool_result_text,
    utf8_bytes,
)


TOOL_OUTPUT_OFFLOADING_FEATURE_ID = "tool_output_offloading"
DEFAULT_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES = 64 * 1024
MIN_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES = 4 * 1024
MAX_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES = 16 * 1024 * 1024
DEFAULT_TOOL_OUTPUT_PREVIEW_CHARS = 2_000
MIN_TOOL_OUTPUT_PREVIEW_CHARS = 256
MAX_TOOL_OUTPUT_PREVIEW_CHARS = 16_000
DEFAULT_MAX_OFFLOADS_PER_TURN = 8
MAX_OFFLOADS_PER_TURN = 100
MAX_AFTER_TOOL_BATCH_CAPTURE_BYTES = 16 * 1024 * 1024
_PLACEHOLDER_PREFIX = "[AgentHarness offloaded large tool output"
_ARTIFACT_HANDLE_SEARCH_RE = re.compile(r"ha1_[0-9a-f]{64}")


class ToolOutputOffloadingFeature:
    """Move oversized tool results to exact, locally retrievable artifacts."""

    feature_id = TOOL_OUTPUT_OFFLOADING_FEATURE_ID
    # The model recalls exact offloaded results through Harness tools.
    requires = requires({PORTAL_TOOLS})

    def __init__(
        self,
        artifact_store: HarnessArtifactStore | None = None,
        *,
        state_root: Path | str | None = None,
        namespace: str = "",
    ) -> None:
        root = Path(state_root or execution_state_root())
        if namespace:
            root = root / str(namespace)
        self.artifact_store = artifact_store or HarnessArtifactStore(
            root / "harness-artifacts"
        )

    def prepare_invocation(self, invocation: Any) -> Any:
        """Keep recall available after offloading is subsequently disabled."""

        scope = artifact_scope_for_invocation(invocation)
        if not scope:
            return invocation
        config = harness_feature_config(invocation, self.feature_id)
        after_tool_batch_enabled = (
            harness_feature_enabled(invocation, self.feature_id)
            and harness_timing_includes(
                config,
                HARNESS_TIMING_AFTER_TOOL_BATCH,
            )
        )
        if (
            not after_tool_batch_enabled
            and not self.artifact_store.has_scope(scope)
        ):
            return invocation
        return _with_recall_tool(invocation, self.artifact_store)

    @staticmethod
    def tool_output_capture_bytes(_config: dict[str, Any]) -> int:
        return MAX_AFTER_TOOL_BATCH_CAPTURE_BYTES




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
        scope = artifact_scope_for_invocation(invocation)
        if not scope:
            return AgentLoopToolBatchTransform(
                batch=batch,
                applied=False,
                reason="stable_artifact_scope_unavailable",
            )
        if not _has_recall_tool(invocation):
            return AgentLoopToolBatchTransform(
                batch=batch,
                applied=False,
                reason="artifact_recall_tool_name_conflict",
            )
        if not _recall_schema_available(
            invocation,
        ):
            return AgentLoopToolBatchTransform(
                batch=batch,
                applied=False,
                reason="artifact_recall_schema_unavailable",
            )
        configured_trigger = bounded_int(
            config.get("offload_trigger_bytes"),
            default=DEFAULT_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
            minimum=MIN_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
            maximum=MAX_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
        )
        configured_preview = bounded_int(
            config.get("preview_chars"),
            default=DEFAULT_TOOL_OUTPUT_PREVIEW_CHARS,
            minimum=MIN_TOOL_OUTPUT_PREVIEW_CHARS,
            maximum=MAX_TOOL_OUTPUT_PREVIEW_CHARS,
        )
        trigger_bytes = configured_trigger
        preview_chars = configured_preview
        max_outputs = bounded_int(
            config.get("max_outputs_per_turn"),
            default=DEFAULT_MAX_OFFLOADS_PER_TURN,
            minimum=1,
            maximum=MAX_OFFLOADS_PER_TURN,
        )
        excluded = set(clean_tool_names(config.get("exclude_tools"))) | {
            READ_HARNESS_ARTIFACT_TOOL_NAME,
            SEARCH_HARNESS_ARTIFACT_TOOL_NAME,
        }
        already_offloaded = int(state.get("offloaded_count") or 0)
        remaining = max(0, max_outputs - already_offloaded)
        transformed_items = list(batch.items)
        offloaded_count = 0
        output_bytes_offloaded = 0
        active_bytes_after = 0
        artifact_bytes = 0
        inline_media_skipped = 0
        failure_count = 0
        search_tool_name = (
            SEARCH_HARNESS_ARTIFACT_TOOL_NAME
            if any(
                tool.name == SEARCH_HARNESS_ARTIFACT_TOOL_NAME
                and bool(getattr(tool, "expose_schema", True))
                for tool in tuple(
                    getattr(invocation, "harness_tools", ()) or ()
                )
            )
            else ""
        )
        for index, item in enumerate(batch.items):
            if (
                remaining <= 0
                or item.source not in PORTAL_CONTROLLED_TOOL_RESULT_SOURCES
            ):
                continue
            if inline_media_bytes(item.result):
                inline_media_skipped += 1
                continue
            content = tool_result_text(item)
            content_bytes = utf8_bytes(content)
            if (
                not content
                or content_bytes < trigger_bytes
                or item.tool_name in excluded
            ):
                continue
            try:
                reference = self.artifact_store.put(
                    scope=scope,
                    value=content,
                )
            except Exception:
                failure_count += 1
                continue
            placeholder = _offload_text_placeholder(
                tool_name=item.tool_name,
                call_id=item.call_id,
                content=content,
                output_bytes=content_bytes,
                reference=reference,
                preview_chars=preview_chars,
                search_tool_name=search_tool_name,
            )
            reclaimed = numeric_delta(content, placeholder)
            if reclaimed <= 0:
                continue
            transformed_items[index] = replace_tool_result_text(
                item,
                placeholder,
            )
            offloaded_count += 1
            remaining -= 1
            output_bytes_offloaded += content_bytes
            active_bytes_after += utf8_bytes(placeholder)
            artifact_bytes += reference.content_bytes
        state["offloaded_count"] = already_offloaded + offloaded_count
        return AgentLoopToolBatchTransform(
            batch=AgentLoopToolBatch(items=tuple(transformed_items)),
            applied=offloaded_count > 0,
            reason=(
                "current_turn_tool_outputs_offloaded"
                if offloaded_count
                else (
                    "artifact_retention_unavailable"
                    if failure_count
                    else "no_current_turn_oversized_tool_outputs"
                )
            ),
            metrics={
                "configured_offload_trigger_bytes": configured_trigger,
                "effective_offload_trigger_bytes": trigger_bytes,
                "configured_preview_chars": configured_preview,
                "effective_preview_chars": preview_chars,
                "context_pressure_percent": (
                    observer.context_pressure_percent
                    if observer is not None
                    else 0
                ),
                "tool_output_count_offloaded": offloaded_count,
                "tool_output_bytes_offloaded": output_bytes_offloaded,
                "tool_output_active_bytes_after": active_bytes_after,
                "artifact_bytes_retained": artifact_bytes,
                "inline_media_tool_output_count_skipped": (
                    inline_media_skipped
                ),
                "artifact_failure_count": failure_count,
            },
        )






def _with_recall_tool(
    invocation: Any,
    artifact_store: HarnessArtifactStore,
) -> Any:
    prepared, _added, _reason = attach_artifact_recall_tools(
        invocation,
        artifact_store,
        include_search=False,
    )
    return prepared


def _has_recall_tool(invocation: Any) -> bool:
    return any(
        tool.name == READ_HARNESS_ARTIFACT_TOOL_NAME
        for tool in tuple(getattr(invocation, "harness_tools", ()) or ())
    )


def _recall_schema_available(
    invocation: Any,
) -> bool:
    """Avoid returning a recall handle to a thread that cannot call it."""

    if bool(getattr(invocation, "is_new_backend_session", False)):
        return True
    available_names = frozenset(
        str(name or "").strip()
        for name in tuple(
            getattr(invocation, "available_harness_tool_names", ()) or ()
        )
        if str(name or "").strip()
    )
    if READ_HARNESS_ARTIFACT_TOOL_NAME in available_names:
        return True
    return False


def _offload_text_placeholder(
    *,
    tool_name: str,
    call_id: str,
    content: str,
    output_bytes: int,
    reference: HarnessArtifactReference,
    preview_chars: int,
    search_tool_name: str = "",
) -> str:
    preview = _bounded_preview(content, preview_chars)
    retrieval = (
        f"Search first with {search_tool_name}, then retrieve an exact slice "
        f"with {READ_HARNESS_ARTIFACT_TOOL_NAME}."
        if search_tool_name
        else (
            "Retrieve exact content with "
            f"{READ_HARNESS_ARTIFACT_TOOL_NAME}."
        )
    )
    metadata = (
        f"{_PLACEHOLDER_PREFIX} "
        f"(tool:{_safe_label(tool_name)}, "
        f"call_id:{_safe_label(call_id)}, "
        f"original_bytes:{output_bytes}, "
        f"sha256:{reference.sha256[:16]}, "
        f"handle:{reference.handle}). {retrieval}]"
    )
    return f"{metadata}\n{preview}" if preview else metadata


def _bounded_preview(content: str, limit: int) -> str:
    if not content or limit <= 0:
        return ""
    if len(content) <= limit:
        return content
    head = max(1, (limit * 3) // 4)
    tail = max(1, limit - head)
    omitted = len(content) - head - tail
    return (
        f"{content[:head]}\n"
        f"[... {omitted} characters offloaded ...]\n"
        f"{content[-tail:]}"
    )


def _safe_label(value: object) -> str:
    clean = "".join(
        character
        for character in str(value or "")[:128]
        if character.isalnum() or character in "._:/-"
    )
    return clean[:64] or "unknown"
