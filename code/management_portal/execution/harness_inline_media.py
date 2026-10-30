from __future__ import annotations

import base64
import copy
import hashlib
import io
import logging
from dataclasses import dataclass, replace
from typing import Any

from .harness import AgentLoopToolBatch, AgentLoopToolBatchTransform, HarnessFeatureOutcome, capabilities_of
from .harness_capabilities import INPUT_IMAGE_DETAIL, PORTAL_TOOLS, requires
from .harness_history import bounded_int, serialized_bytes

LOGGER = logging.getLogger(__name__)

INLINE_MEDIA_HISTORY_FEATURE_ID = "inline_media_history"
DEFAULT_MAX_INLINE_MEDIA_BYTES = 512 * 1024
DEFAULT_TRIGGER_INLINE_MEDIA_BYTES = DEFAULT_MAX_INLINE_MEDIA_BYTES
DEFAULT_KEEP_RECENT_IMAGES = 1
# Runtime option read by an Adapter that declares ``input_image_detail``.
INPUT_IMAGE_DETAIL_RUNTIME_KEY = "input_image_detail"
DEFAULT_INPUT_IMAGE_DETAIL = "high"
DEFAULT_MAX_TOOL_IMAGE_EDGE = 1536
DEFAULT_TOOL_IMAGE_JPEG_QUALITY = 82


def normalize_inline_media_trigger_bytes(
    value: object,
    *,
    max_inline_media_bytes: int,
) -> int:
    return bounded_int(
        value,
        default=DEFAULT_TRIGGER_INLINE_MEDIA_BYTES,
        minimum=max_inline_media_bytes,
        maximum=32 * 1024 * 1024,
    )




@dataclass(frozen=True)
class _ToolResultInlineImage:
    result_index: int
    part_index: int
    image_url_field: str
    encoded_bytes: int
    image_url: str


def _compress_inline_image_url(
    image_url: str,
    *,
    max_encoded_bytes: int,
    max_edge: int,
    quality: int,
) -> tuple[str, int]:
    """Return a smaller model-facing data URL without touching the source."""

    if not image_url.startswith("data:image/") or "," not in image_url:
        return image_url, 0
    header, encoded = image_url.split(",", 1)
    if ";base64" not in header.casefold():
        return image_url, 0
    try:
        source = base64.b64decode(encoded, validate=True)
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(source)) as opened:
            normalized = ImageOps.exif_transpose(opened)
            if normalized.mode in {"RGBA", "LA"}:
                rgba = normalized.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                normalized = background
            else:
                normalized = normalized.convert("RGB")
            candidates: list[tuple[int, int]] = []
            for edge, candidate_quality in (
                (max_edge, quality),
                (max_edge, 68),
                (1280, 76),
                (1024, 72),
                (768, 68),
                (512, 64),
                (384, 60),
            ):
                candidate = (
                    min(max_edge, edge),
                    min(quality, candidate_quality),
                )
                if candidate not in candidates:
                    candidates.append(candidate)
            smallest = image_url
            smallest_size = len(image_url.encode("utf-8"))
            for edge, candidate_quality in candidates:
                rendered = normalized.copy()
                rendered.thumbnail(
                    (edge, edge),
                    Image.Resampling.LANCZOS,
                )
                output = io.BytesIO()
                rendered.save(
                    output,
                    format="JPEG",
                    quality=candidate_quality,
                    optimize=True,
                )
                candidate_url = (
                    "data:image/jpeg;base64,"
                    + base64.b64encode(output.getvalue()).decode("ascii")
                )
                candidate_size = len(candidate_url.encode("utf-8"))
                if candidate_size < smallest_size:
                    smallest = candidate_url
                    smallest_size = candidate_size
                if candidate_size <= max_encoded_bytes:
                    return candidate_url, max(
                        0,
                        len(image_url.encode("utf-8")) - candidate_size,
                    )
            if smallest != image_url:
                return smallest, max(
                    0,
                    len(image_url.encode("utf-8")) - smallest_size,
                )
    except Exception:
        LOGGER.warning("Inline image could not be recompressed; it stays as sent", exc_info=True)
        return image_url, 0
    return image_url, 0


def _bound_input_image_detail(
    *,
    provider: Any,
    invocation: Any,
    config: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Bind a resized detail mode before the Adapter serializes image attachments."""

    if INPUT_IMAGE_DETAIL not in capabilities_of(invocation, provider):
        return invocation, {}
    request = getattr(invocation, "request", None)
    input_media = tuple(getattr(request, "input_media", ()) or ())
    image_count = sum(
        1
        for item in input_media
        if str(getattr(item, "media_type", "") or "") == "image"
    )
    if not image_count:
        return invocation, {}
    runtime_options = dict(getattr(request, "runtime_options", {}) or {})
    if INPUT_IMAGE_DETAIL_RUNTIME_KEY in runtime_options:
        return invocation, {}
    detail = str(
        config.get(
            "input_image_detail",
            DEFAULT_INPUT_IMAGE_DETAIL,
        )
        or ""
    ).strip().casefold()
    if detail not in {"auto", "high", "original"}:
        detail = DEFAULT_INPUT_IMAGE_DETAIL
    runtime_options[INPUT_IMAGE_DETAIL_RUNTIME_KEY] = detail
    updated_request = replace(request, runtime_options=runtime_options)
    return replace(invocation, request=updated_request), {
        "input_image_count": image_count,
        "input_image_detail": detail,
    }




class InlineMediaHistoryFeature:
    feature_id = INLINE_MEDIA_HISTORY_FEATURE_ID
    # Tool-result image transformations and native image detail.
    requires = requires(
        {PORTAL_TOOLS},
        {INPUT_IMAGE_DETAIL},
    )
    # Native input-image detail is configured before the turn.
    requires_before_turn_setup_for_after_tool_batch = True

    def apply(
        self,
        *,
        provider: Any,
        invocation: Any,
        context: Any,
        config: dict[str, Any],
        observer: Any = None,
    ) -> HarnessFeatureOutcome:
        current_invocation, input_metrics = _bound_input_image_detail(
            provider=provider,
            invocation=invocation,
            config=config,
        )
        return HarnessFeatureOutcome(
            invocation=current_invocation,
            status="applied" if input_metrics else "skipped",
            reason="input_image_detail_bounded" if input_metrics else "input_image_detail_unavailable",
            metrics=input_metrics,
        )

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
        del provider, invocation, context, state, observer
        max_bytes = bounded_int(
            config.get("max_inline_media_bytes"),
            default=DEFAULT_MAX_INLINE_MEDIA_BYTES,
            minimum=64 * 1024,
            maximum=16 * 1024 * 1024,
        )
        trigger_bytes = normalize_inline_media_trigger_bytes(
            config.get("trigger_inline_media_bytes"),
            max_inline_media_bytes=max_bytes,
        )
        keep_recent = bounded_int(
            config.get("keep_recent_images"),
            default=DEFAULT_KEEP_RECENT_IMAGES,
            minimum=1,
            maximum=20,
        )
        max_edge = bounded_int(
            config.get("max_tool_image_edge"),
            default=DEFAULT_MAX_TOOL_IMAGE_EDGE,
            minimum=384,
            maximum=4096,
        )
        quality = bounded_int(
            config.get("tool_image_jpeg_quality"),
            default=DEFAULT_TOOL_IMAGE_JPEG_QUALITY,
            minimum=60,
            maximum=95,
        )
        images_before = _tool_result_inline_images(batch)
        total_bytes_before = sum(
            image.encoded_bytes for image in images_before
        )
        before_bytes = serialized_bytes(tuple(
            item.result for item in batch.items
        ))
        transformed_items = list(batch.items)
        results = [copy.deepcopy(item.result) for item in batch.items]
        compressed_count = 0
        compressed_bytes_avoided = 0
        for image in images_before:
            if image.encoded_bytes <= max_bytes:
                continue
            compressed, avoided = _compress_inline_image_url(
                image.image_url,
                max_encoded_bytes=max_bytes,
                max_edge=max_edge,
                quality=quality,
            )
            if not avoided:
                continue
            results[image.result_index]["contentItems"][
                image.part_index
            ][image.image_url_field] = compressed
            compressed_count += 1
            compressed_bytes_avoided += avoided
        for index, result in enumerate(results):
            if result != batch.items[index].result:
                transformed_items[index] = replace(
                    batch.items[index],
                    result=result,
                )
        current_batch = AgentLoopToolBatch(items=tuple(transformed_items))
        images = _tool_result_inline_images(current_batch)
        total_bytes = sum(image.encoded_bytes for image in images)
        base_metrics = {
            "tool_result_json_bytes_before": before_bytes,
            "inline_image_count_before": len(images_before),
            "inline_media_bytes_before": total_bytes_before,
            "inline_media_bytes_after_compression": total_bytes,
            "inline_images_compressed": compressed_count,
            "inline_media_compression_bytes_avoided": (
                compressed_bytes_avoided
            ),
            "max_inline_media_bytes": max_bytes,
            "trigger_inline_media_bytes": trigger_bytes,
            "keep_recent_images": keep_recent,
            "max_tool_image_edge": max_edge,
            "tool_image_jpeg_quality": quality,
        }
        if total_bytes <= trigger_bytes:
            return AgentLoopToolBatchTransform(
                batch=current_batch,
                applied=bool(compressed_count),
                reason=(
                    "current_tool_batch_inline_media_compressed"
                    if compressed_count
                    else "below_inline_media_trigger"
                ),
                metrics={
                    **base_metrics,
                    "tool_result_json_bytes_after": serialized_bytes(
                        tuple(item.result for item in current_batch.items)
                    ),
                    "inline_image_count_after": len(images),
                    "inline_media_bytes_after": total_bytes,
                },
            )

        remove = images[:max(0, len(images) - keep_recent)]
        if not remove:
            return AgentLoopToolBatchTransform(
                batch=current_batch,
                applied=bool(compressed_count),
                reason=(
                    "current_tool_batch_inline_media_compressed_over_budget"
                    if compressed_count
                    else "recent_inline_media_exceeds_budget"
                ),
                metrics=base_metrics,
            )

        transformed_items = list(current_batch.items)
        results = [copy.deepcopy(item.result) for item in current_batch.items]
        for image in remove:
            content_items = results[image.result_index]["contentItems"]
            content_items[image.part_index] = {
                "type": "inputText",
                "text": _image_placeholder_for(
                    image.image_url,
                    image.encoded_bytes,
                ),
            }
        for index, result in enumerate(results):
            if result != current_batch.items[index].result:
                transformed_items[index] = replace(
                    current_batch.items[index],
                    result=result,
                )
        transformed = AgentLoopToolBatch(items=tuple(transformed_items))
        after_bytes = serialized_bytes(tuple(
            item.result for item in transformed.items
        ))
        removed_bytes = sum(image.encoded_bytes for image in remove)
        return AgentLoopToolBatchTransform(
            batch=transformed,
            applied=True,
            reason="current_tool_batch_inline_media_replaced",
            metrics={
                **base_metrics,
                "tool_result_json_bytes_after": after_bytes,
                "tool_result_json_bytes_reclaimed": max(
                    0,
                    before_bytes - after_bytes,
                ),
                "inline_image_count_after": len(images) - len(remove),
                "inline_image_count_removed": len(remove),
                "inline_media_bytes_after": total_bytes - removed_bytes,
                "inline_media_bytes_removed": removed_bytes,
            },
        )






def _tool_result_inline_images(
    batch: AgentLoopToolBatch,
) -> list[_ToolResultInlineImage]:
    images: list[_ToolResultInlineImage] = []
    for result_index, item in enumerate(batch.items):
        result = item.result
        if result.get("success") is not True:
            continue
        parts = result.get("contentItems")
        if not isinstance(parts, list):
            continue
        for part_index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").replace("_", "").lower()
            if part_type not in {"inputimage", "image"}:
                continue
            image_url_field = (
                "imageUrl" if "imageUrl" in part else "image_url"
            )
            image_url = str(part.get(image_url_field) or "")
            if not image_url.startswith("data:"):
                continue
            images.append(_ToolResultInlineImage(
                result_index=result_index,
                part_index=part_index,
                image_url_field=image_url_field,
                encoded_bytes=len(image_url.encode("utf-8")),
                image_url=image_url,
            ))
    return images






def _image_placeholder_for(image_url: str, encoded_bytes: int) -> str:
    digest = hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:16]
    return (
        "[AgentHarness omitted a historical inline image "
        f"(sha256:{digest}, encoded_bytes:{encoded_bytes}).]"
    )
