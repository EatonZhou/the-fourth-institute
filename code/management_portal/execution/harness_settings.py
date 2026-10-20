"""An Agent Harness configuration as a Resident's settings store it: each feature, its timing and parameters.

SocietyService normalizes the persisted configuration with it; a
Resident's runtime settings (``resident_runtime_settings``) carry it as
``agent_harness``.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .harness import DEFAULT_ENABLED_AGENT_HARNESS_FEATURE_IDS, HARNESS_FEATURES, HARNESS_TIMING_AFTER_TOOL_BATCH, HARNESS_TIMING_BEFORE_TURN
from .harness_early_compaction import (
    EARLY_CONTEXT_COMPACTION_FEATURE_ID,
)
from .harness_artifact_search import (
    ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID,
)
from .harness_inline_media import (
    DEFAULT_KEEP_RECENT_IMAGES,
    DEFAULT_MAX_INLINE_MEDIA_BYTES,
    INLINE_MEDIA_HISTORY_FEATURE_ID,
    normalize_inline_media_trigger_bytes,
)
from .harness_native_loop import (
    DEFAULT_DUPLICATE_READ_THRESHOLD,
    DEFAULT_NATIVE_CHECKPOINT_INTERVAL,
    DEFAULT_NATIVE_LARGE_OUTPUT_BYTES,
    DEFAULT_NATIVE_MAX_CHECKPOINTS,
    NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID,
)
from .harness_read_dedup import (
    DEFAULT_MIN_DUPLICATE_OUTPUT_TOKENS,
    HASH_AWARE_READ_DEDUP_FEATURE_ID,
    MAX_DUPLICATE_OUTPUT_TOKENS,
    MIN_DUPLICATE_OUTPUT_TOKENS,
)
from .harness_tool_granularity import (
    TOOL_CALL_GRANULARITY_FEATURE_ID,
)
from .harness_tool_output_offloading import (
    DEFAULT_MAX_OFFLOADS_PER_TURN,
    DEFAULT_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
    DEFAULT_TOOL_OUTPUT_PREVIEW_CHARS,
    MAX_OFFLOADS_PER_TURN,
    MAX_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
    MAX_TOOL_OUTPUT_PREVIEW_CHARS,
    MIN_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
    MIN_TOOL_OUTPUT_PREVIEW_CHARS,
    TOOL_OUTPUT_OFFLOADING_FEATURE_ID,
)
from .harness_unified_resource_recall import (
    DEFAULT_RESOURCE_RECALL_MAX_BYTES,
    MAX_RESOURCE_RECALL_MAX_BYTES,
    MIN_RESOURCE_RECALL_MAX_BYTES,
    UNIFIED_RESOURCE_RECALL_FEATURE_ID,
)


_HARNESS_FEATURE_PARAMETER_FIELDS = {
    name: definition.parameters for name, definition in HARNESS_FEATURES.items()
}


def normalize_agent_harness_settings(raw: object) -> dict[str, Any]:
    """Normalize the user-controlled, optional AgentHarness plugin settings."""

    if raw is not None and not isinstance(raw, dict):
        raise ValueError("AgentHarness settings must be an object")
    data = raw or {}
    unknown_fields = set(data) - {"features", "mode"}
    if unknown_fields:
        raise ValueError(
            "AgentHarness settings contain unsupported fields: "
            + ", ".join(sorted(unknown_fields))
        )
    raw_features = data.get("features")
    if raw_features is not None and not isinstance(raw_features, dict):
        raise ValueError("AgentHarness features must be an object")
    features = raw_features or {}

    def feature_enabled(
        feature_id: str,
        feature_config: dict[str, Any],
    ) -> bool:
        return boolean_setting(
            feature_config.get("enabled"),
            default=(
                feature_id
                in DEFAULT_ENABLED_AGENT_HARNESS_FEATURE_IDS
            ),
        )

    normalized_features: dict[str, dict[str, Any]] = {}
    for raw_feature_id, raw_feature_config in features.items():
        feature_id = _clean_harness_feature_id(raw_feature_id)
        if feature_id not in _HARNESS_FEATURE_PARAMETER_FIELDS:
            raise ValueError(f"unsupported AgentHarness feature: {raw_feature_id}")
        if not isinstance(raw_feature_config, dict):
            raise ValueError(f"AgentHarness feature {feature_id} must be an object")
        unknown = set(raw_feature_config) - {"enabled", "timing"} - _HARNESS_FEATURE_PARAMETER_FIELDS[feature_id]
        if unknown:
            raise ValueError(f"AgentHarness feature {feature_id} contains unsupported fields: " + ", ".join(sorted(unknown)))
        feature_config = deepcopy(raw_feature_config)
        feature_config["enabled"] = feature_enabled(
            feature_id,
            feature_config,
        )
        normalized_features[feature_id] = feature_config
    inline_media = normalized_features.get(
        INLINE_MEDIA_HISTORY_FEATURE_ID,
        {},
    )
    max_inline_media_bytes = _bounded_integer(
        inline_media.get("max_inline_media_bytes"),
        default=DEFAULT_MAX_INLINE_MEDIA_BYTES,
        minimum=64 * 1024,
        maximum=16 * 1024 * 1024,
    )
    normalized_features[INLINE_MEDIA_HISTORY_FEATURE_ID] = {
        **inline_media,
        "enabled": feature_enabled(
            INLINE_MEDIA_HISTORY_FEATURE_ID,
            inline_media,
        ),
        "max_inline_media_bytes": max_inline_media_bytes,
        "trigger_inline_media_bytes": normalize_inline_media_trigger_bytes(
            inline_media.get("trigger_inline_media_bytes"),
            max_inline_media_bytes=max_inline_media_bytes,
        ),
        "keep_recent_images": _bounded_integer(
            inline_media.get("keep_recent_images"),
            default=DEFAULT_KEEP_RECENT_IMAGES,
            minimum=1,
            maximum=20,
        ),
        "timing": HARNESS_TIMING_AFTER_TOOL_BATCH,
    }
    tool_granularity = normalized_features.get(
        TOOL_CALL_GRANULARITY_FEATURE_ID,
        {},
    )
    normalized_features[TOOL_CALL_GRANULARITY_FEATURE_ID] = {
        **tool_granularity,
        "enabled": feature_enabled(
            TOOL_CALL_GRANULARITY_FEATURE_ID,
            tool_granularity,
        ),
        "timing": HARNESS_TIMING_BEFORE_TURN,
    }
    native_loop = normalized_features.get(
        NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID,
        {},
    )
    normalized_features[NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID] = {
        **native_loop,
        "enabled": feature_enabled(
            NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID,
            native_loop,
        ),
        "checkpoint_interval": _bounded_integer(
            native_loop.get("checkpoint_interval"),
            default=DEFAULT_NATIVE_CHECKPOINT_INTERVAL,
            minimum=20,
            maximum=500,
        ),
        "max_checkpoints": _bounded_integer(
            native_loop.get("max_checkpoints"),
            default=DEFAULT_NATIVE_MAX_CHECKPOINTS,
            minimum=1,
            maximum=8,
        ),
        "duplicate_read_threshold": _bounded_integer(
            native_loop.get("duplicate_read_threshold"),
            default=DEFAULT_DUPLICATE_READ_THRESHOLD,
            minimum=2,
            maximum=100,
        ),
        "large_output_bytes": _bounded_integer(
            native_loop.get("large_output_bytes"),
            default=DEFAULT_NATIVE_LARGE_OUTPUT_BYTES,
            minimum=16 * 1024,
            maximum=16 * 1024 * 1024,
        ),
        "timing": HARNESS_TIMING_BEFORE_TURN,
    }
    tool_output_offloading = normalized_features.get(
        TOOL_OUTPUT_OFFLOADING_FEATURE_ID,
        {},
    )
    normalized_features[TOOL_OUTPUT_OFFLOADING_FEATURE_ID] = {
        **tool_output_offloading,
        "enabled": feature_enabled(
            TOOL_OUTPUT_OFFLOADING_FEATURE_ID,
            tool_output_offloading,
        ),
        "offload_trigger_bytes": _bounded_integer(
            tool_output_offloading.get("offload_trigger_bytes"),
            default=DEFAULT_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
            minimum=MIN_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
            maximum=MAX_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
        ),
        "preview_chars": _bounded_integer(
            tool_output_offloading.get("preview_chars"),
            default=DEFAULT_TOOL_OUTPUT_PREVIEW_CHARS,
            minimum=MIN_TOOL_OUTPUT_PREVIEW_CHARS,
            maximum=MAX_TOOL_OUTPUT_PREVIEW_CHARS,
        ),
        "max_outputs_per_turn": _bounded_integer(
            tool_output_offloading.get("max_outputs_per_turn"),
            default=DEFAULT_MAX_OFFLOADS_PER_TURN,
            minimum=1,
            maximum=MAX_OFFLOADS_PER_TURN,
        ),
        "timing": HARNESS_TIMING_AFTER_TOOL_BATCH,
    }
    artifact_search = normalized_features.get(
        ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID,
        {},
    )
    normalized_features[ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID] = {
        **artifact_search,
        "enabled": feature_enabled(
            ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID,
            artifact_search,
        ),
        "timing": HARNESS_TIMING_BEFORE_TURN,
    }
    read_dedup = normalized_features.get(
        HASH_AWARE_READ_DEDUP_FEATURE_ID,
        {},
    )
    normalized_features[HASH_AWARE_READ_DEDUP_FEATURE_ID] = {
        **read_dedup,
        "enabled": feature_enabled(
            HASH_AWARE_READ_DEDUP_FEATURE_ID,
            read_dedup,
        ),
        "min_output_tokens": _bounded_integer(
            read_dedup.get("min_output_tokens"),
            default=DEFAULT_MIN_DUPLICATE_OUTPUT_TOKENS,
            minimum=MIN_DUPLICATE_OUTPUT_TOKENS,
            maximum=MAX_DUPLICATE_OUTPUT_TOKENS,
        ),
        "timing": HARNESS_TIMING_AFTER_TOOL_BATCH,
    }
    resource_recall = normalized_features.get(
        UNIFIED_RESOURCE_RECALL_FEATURE_ID,
        {},
    )
    normalized_features[UNIFIED_RESOURCE_RECALL_FEATURE_ID] = {
        **resource_recall,
        "enabled": feature_enabled(
            UNIFIED_RESOURCE_RECALL_FEATURE_ID,
            resource_recall,
        ),
        "max_resource_bytes": _bounded_integer(
            resource_recall.get("max_resource_bytes"),
            default=DEFAULT_RESOURCE_RECALL_MAX_BYTES,
            minimum=MIN_RESOURCE_RECALL_MAX_BYTES,
            maximum=MAX_RESOURCE_RECALL_MAX_BYTES,
        ),
        "timing": HARNESS_TIMING_BEFORE_TURN,
    }
    early_compaction = normalized_features.get(
        EARLY_CONTEXT_COMPACTION_FEATURE_ID,
        {},
    )
    normalized_features[EARLY_CONTEXT_COMPACTION_FEATURE_ID] = {
        **{key: value for key, value in early_compaction.items() if key != "timing"},
        "enabled": feature_enabled(
            EARLY_CONTEXT_COMPACTION_FEATURE_ID,
            early_compaction,
        ),
    }
    provider_error_continue = normalized_features.get("provider_error_continue", {})
    normalized_features["provider_error_continue"] = {
        **provider_error_continue,
        "enabled": feature_enabled("provider_error_continue", provider_error_continue),
        "timing": HARNESS_TIMING_BEFORE_TURN,
    }
    mode = str(data.get("mode") or "auto").strip().casefold()
    if mode not in {"auto", "manual"}:
        raise ValueError(f"unsupported AgentHarness mode: {mode}")
    return {
        "mode": mode,
        "features": normalized_features,
    }


def boolean_setting(value: object, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _clean_harness_feature_id(value: object) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 80:
        return ""
    return text if all(ch.isalnum() or ch in "._-" for ch in text) else ""


def _bounded_integer(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(int(minimum), min(int(maximum), number))
