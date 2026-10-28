"""An Agent Harness configuration as a Resident's settings store it: each feature, its timing and parameters.

SocietyService normalizes the persisted configuration with it; a
Resident's runtime settings (``resident_runtime_settings``) carry it as
``agent_harness``.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from .harness import DEFAULT_ENABLED_AGENT_HARNESS_FEATURE_IDS, HARNESS_FEATURES, HARNESS_TIMING_AFTER_TOOL_BATCH, HARNESS_TIMING_BEFORE_TURN
from .harness_early_compaction import (
    EARLY_CONTEXT_COMPACTION_FEATURE_ID,
)
from .harness_artifact_search import (
    ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID,
)
from .harness_history import bounded_int
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
from .harness_provider_error_continue import PROVIDER_ERROR_CONTINUE_FEATURE_ID
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

# Reads a feature's configuration and the parameters normalized before this one.
_ParameterNormalizer = Callable[[dict[str, Any], dict[str, Any]], Any]


def _bounded(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> tuple[str, _ParameterNormalizer]:
    return name, lambda configured, _normalized: bounded_int(
        configured.get(name),
        default=default,
        minimum=minimum,
        maximum=maximum,
    )


def _inline_media_trigger_bytes(
    configured: dict[str, Any],
    normalized: dict[str, Any],
) -> int:
    return normalize_inline_media_trigger_bytes(
        configured.get("trigger_inline_media_bytes"),
        max_inline_media_bytes=normalized["max_inline_media_bytes"],
    )


@dataclass(frozen=True)
class _FeatureSettings:
    """How one feature's stored configuration is normalized."""

    # The timing the feature always runs at; None: the feature has no timing.
    timing: str | None
    parameters: tuple[tuple[str, _ParameterNormalizer], ...] = ()


# Normalized settings carry each of these features, in this order, after the
# configured ones.
_FEATURE_SETTINGS: dict[str, _FeatureSettings] = {
    INLINE_MEDIA_HISTORY_FEATURE_ID: _FeatureSettings(
        HARNESS_TIMING_AFTER_TOOL_BATCH,
        (
            _bounded(
                "max_inline_media_bytes",
                default=DEFAULT_MAX_INLINE_MEDIA_BYTES,
                minimum=64 * 1024,
                maximum=16 * 1024 * 1024,
            ),
            ("trigger_inline_media_bytes", _inline_media_trigger_bytes),
            _bounded(
                "keep_recent_images",
                default=DEFAULT_KEEP_RECENT_IMAGES,
                minimum=1,
                maximum=20,
            ),
        ),
    ),
    TOOL_CALL_GRANULARITY_FEATURE_ID: _FeatureSettings(HARNESS_TIMING_BEFORE_TURN),
    NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID: _FeatureSettings(
        HARNESS_TIMING_BEFORE_TURN,
        (
            _bounded(
                "checkpoint_interval",
                default=DEFAULT_NATIVE_CHECKPOINT_INTERVAL,
                minimum=20,
                maximum=500,
            ),
            _bounded(
                "max_checkpoints",
                default=DEFAULT_NATIVE_MAX_CHECKPOINTS,
                minimum=1,
                maximum=8,
            ),
            _bounded(
                "duplicate_read_threshold",
                default=DEFAULT_DUPLICATE_READ_THRESHOLD,
                minimum=2,
                maximum=100,
            ),
            _bounded(
                "large_output_bytes",
                default=DEFAULT_NATIVE_LARGE_OUTPUT_BYTES,
                minimum=16 * 1024,
                maximum=16 * 1024 * 1024,
            ),
        ),
    ),
    TOOL_OUTPUT_OFFLOADING_FEATURE_ID: _FeatureSettings(
        HARNESS_TIMING_AFTER_TOOL_BATCH,
        (
            _bounded(
                "offload_trigger_bytes",
                default=DEFAULT_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
                minimum=MIN_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
                maximum=MAX_TOOL_OUTPUT_OFFLOAD_TRIGGER_BYTES,
            ),
            _bounded(
                "preview_chars",
                default=DEFAULT_TOOL_OUTPUT_PREVIEW_CHARS,
                minimum=MIN_TOOL_OUTPUT_PREVIEW_CHARS,
                maximum=MAX_TOOL_OUTPUT_PREVIEW_CHARS,
            ),
            _bounded(
                "max_outputs_per_turn",
                default=DEFAULT_MAX_OFFLOADS_PER_TURN,
                minimum=1,
                maximum=MAX_OFFLOADS_PER_TURN,
            ),
        ),
    ),
    ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID: _FeatureSettings(HARNESS_TIMING_BEFORE_TURN),
    HASH_AWARE_READ_DEDUP_FEATURE_ID: _FeatureSettings(
        HARNESS_TIMING_AFTER_TOOL_BATCH,
        (
            _bounded(
                "min_output_tokens",
                default=DEFAULT_MIN_DUPLICATE_OUTPUT_TOKENS,
                minimum=MIN_DUPLICATE_OUTPUT_TOKENS,
                maximum=MAX_DUPLICATE_OUTPUT_TOKENS,
            ),
        ),
    ),
    UNIFIED_RESOURCE_RECALL_FEATURE_ID: _FeatureSettings(
        HARNESS_TIMING_BEFORE_TURN,
        (
            _bounded(
                "max_resource_bytes",
                default=DEFAULT_RESOURCE_RECALL_MAX_BYTES,
                minimum=MIN_RESOURCE_RECALL_MAX_BYTES,
                maximum=MAX_RESOURCE_RECALL_MAX_BYTES,
            ),
        ),
    ),
    EARLY_CONTEXT_COMPACTION_FEATURE_ID: _FeatureSettings(timing=None),
    PROVIDER_ERROR_CONTINUE_FEATURE_ID: _FeatureSettings(HARNESS_TIMING_BEFORE_TURN),
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
    features = _configured_features(data.get("features"))
    for feature_id, settings in _FEATURE_SETTINGS.items():
        features[feature_id] = _normalized_feature(
            feature_id,
            features.get(feature_id, {}),
            settings,
        )
    mode = str(data.get("mode") or "auto").strip().casefold()
    if mode not in {"auto", "manual"}:
        raise ValueError(f"unsupported AgentHarness mode: {mode}")
    return {
        "mode": mode,
        "features": features,
    }


def _configured_features(raw_features: object) -> dict[str, dict[str, Any]]:
    """The configured features, validated, each with its ``enabled`` resolved."""

    if raw_features is not None and not isinstance(raw_features, dict):
        raise ValueError("AgentHarness features must be an object")
    configured: dict[str, dict[str, Any]] = {}
    for raw_feature_id, raw_feature_config in (raw_features or {}).items():
        feature_id = _clean_harness_feature_id(raw_feature_id)
        if feature_id not in _HARNESS_FEATURE_PARAMETER_FIELDS:
            raise ValueError(f"unsupported AgentHarness feature: {raw_feature_id}")
        if not isinstance(raw_feature_config, dict):
            raise ValueError(f"AgentHarness feature {feature_id} must be an object")
        unknown = set(raw_feature_config) - {"enabled", "timing"} - _HARNESS_FEATURE_PARAMETER_FIELDS[feature_id]
        if unknown:
            raise ValueError(f"AgentHarness feature {feature_id} contains unsupported fields: " + ", ".join(sorted(unknown)))
        feature_config = deepcopy(raw_feature_config)
        feature_config["enabled"] = _feature_enabled(feature_id, feature_config)
        configured[feature_id] = feature_config
    return configured


def _normalized_feature(
    feature_id: str,
    configured: dict[str, Any],
    settings: _FeatureSettings,
) -> dict[str, Any]:
    normalized = dict(configured)
    if settings.timing is None:
        normalized.pop("timing", None)
    normalized["enabled"] = _feature_enabled(feature_id, configured)
    for name, normalize in settings.parameters:
        normalized[name] = normalize(configured, normalized)
    if settings.timing is not None:
        normalized["timing"] = settings.timing
    return normalized


def _feature_enabled(feature_id: str, feature_config: dict[str, Any]) -> bool:
    return boolean_setting(
        feature_config.get("enabled"),
        default=feature_id in DEFAULT_ENABLED_AGENT_HARNESS_FEATURE_IDS,
    )


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
