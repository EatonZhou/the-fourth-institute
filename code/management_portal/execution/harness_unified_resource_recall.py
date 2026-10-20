from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from management_portal.resource_refs import extract_resource_addresses

from .harness import HarnessFeatureOutcome, HarnessTool, attach_harness_tools, harness_feature_config, harness_feature_enabled, has_harness_tool, stable_json_result
from .harness_capabilities import PORTAL_TOOLS, requires
from .harness_content_recall import read_text_slice, search_text
from .harness_history import approximate_value_tokens


UNIFIED_RESOURCE_RECALL_FEATURE_ID = "unified_resource_recall"
SEARCH_PORTAL_RESOURCE_TOOL_NAME = "search_portal_resource"
READ_PORTAL_RESOURCE_TOOL_NAME = "read_portal_resource"
DEFAULT_RESOURCE_RECALL_MAX_BYTES = 4 * 1024 * 1024
MIN_RESOURCE_RECALL_MAX_BYTES = 64 * 1024
MAX_RESOURCE_RECALL_MAX_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class _ResourceDocument:
    address: str
    content: str
    content_type: str
    sha256: str
    source_bytes: int
    metadata: dict[str, Any]


def _bounded_max_bytes(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_RESOURCE_RECALL_MAX_BYTES
    return max(
        MIN_RESOURCE_RECALL_MAX_BYTES,
        min(MAX_RESOURCE_RECALL_MAX_BYTES, parsed),
    )


def _text_content_type(content_type: str) -> bool:
    clean = str(content_type or "").partition(";")[0].strip().lower()
    return clean.startswith("text/") or clean in {
        "application/json",
        "application/ld+json",
        "application/toml",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
    }


def _default_resource_service() -> Any:
    from management_portal.resources import get_resource_service

    return get_resource_service()


class UnifiedResourceRecallFeature:
    """Search and slice explicitly referenced ResourceService content."""

    feature_id = UNIFIED_RESOURCE_RECALL_FEATURE_ID
    requires = requires({PORTAL_TOOLS})

    def __init__(
        self,
        resource_service_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.resource_service_factory = (
            resource_service_factory or _default_resource_service
        )

    @staticmethod
    def _addresses(invocation: Any) -> tuple[str, ...]:
        prompt = str(getattr(invocation, "user_prompt", "") or "")
        return tuple(extract_resource_addresses(prompt))

    def _tools(
        self,
        invocation: Any,
        *,
        max_bytes: int,
        enabled: bool = True,
    ) -> tuple[HarnessTool, ...]:
        request = getattr(invocation, "request", None)
        actor = str(getattr(request, "actor", "") or "").strip()
        allowed = frozenset(self._addresses(invocation))
        cache: dict[str, _ResourceDocument] = {}
        service: Any = None

        def load(address: object) -> tuple[_ResourceDocument, bool]:
            nonlocal service
            if not enabled:
                raise PermissionError(
                    "Unified Resource recall is disabled for this turn"
                )
            clean_address = str(address or "").strip()
            if clean_address not in allowed:
                raise PermissionError(
                    "Resource address was not explicitly referenced in this turn"
                )
            if clean_address in cache:
                return cache[clean_address], True
            if service is None:
                service = self.resource_service_factory()
            content, content_type, metadata = service.read_content(
                clean_address,
                actor=actor,
                max_bytes=max_bytes,
            )
            if not _text_content_type(str(content_type or "")):
                raise ValueError(
                    "Unified Resource recall only supports textual content"
                )
            raw_content = bytes(content)
            text = raw_content.decode("utf-8", errors="replace")
            document = _ResourceDocument(
                address=clean_address,
                content=text,
                content_type=str(content_type or "text/plain"),
                sha256=hashlib.sha256(raw_content).hexdigest(),
                source_bytes=len(raw_content),
                metadata=(
                    dict(metadata) if isinstance(metadata, dict) else {}
                ),
            )
            cache[clean_address] = document
            return document, False

        def envelope(
            document: _ResourceDocument,
            *,
            cache_hit: bool,
        ) -> dict[str, Any]:
            record = (
                document.metadata.get("record")
                if isinstance(document.metadata.get("record"), dict)
                else {}
            )
            detail = (
                document.metadata.get("detail")
                if isinstance(document.metadata.get("detail"), dict)
                else {}
            )
            return {
                "address": document.address,
                "resource_id": str(
                    document.metadata.get("resource_id")
                    or document.address.partition("/")[0]
                ),
                "title": str(
                    document.metadata.get("title")
                    or record.get("title")
                    or detail.get("title")
                    or ""
                ),
                "content_type": document.content_type,
                "sha256": document.sha256,
                "source_bytes": document.source_bytes,
                "cache_hit": cache_hit,
            }

        def search_resource(arguments: dict[str, Any]) -> str:
            document, cache_hit = load(arguments.get("address"))
            result = search_text(
                document.content,
                arguments.get("query"),
                max_hits=arguments.get("max_hits", 5),
                context_chars=arguments.get("context_chars", 600),
            )
            return json.dumps(
                {
                    **envelope(document, cache_hit=cache_hit),
                    **result,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

        def read_resource(arguments: dict[str, Any]) -> str:
            document, cache_hit = load(arguments.get("address"))
            result = read_text_slice(
                document.content,
                offset=arguments.get("offset", 0),
                limit=arguments.get("limit", 16_000),
            )
            return json.dumps(
                {
                    **envelope(document, cache_hit=cache_hit),
                    **result,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

        return (
            HarnessTool(
                name=SEARCH_PORTAL_RESOURCE_TOOL_NAME,
                description=(
                    "Search textual content behind a unified Portal resource "
                    "address explicitly listed in the current turn. Search "
                    "locally first and return small exact snippets with offsets; "
                    "the current actor is always re-authorized by ResourceService."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "address": {"type": "string"},
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 500,
                        },
                        "max_hits": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 5,
                        },
                        "context_chars": {
                            "type": "integer",
                            "minimum": 64,
                            "maximum": 4_000,
                            "default": 600,
                        },
                    },
                    "required": ["address", "query"],
                    "additionalProperties": False,
                },
                handler=search_resource,
                expose_schema=enabled,
                read_only=True,
                # The envelope says whether the cache answered this call.
                stable_result=stable_json_result("cache_hit"),
            ),
            HarnessTool(
                name=READ_PORTAL_RESOURCE_TOOL_NAME,
                description=(
                    "Read an exact character slice from textual content behind "
                    "a unified Portal resource address explicitly listed in the "
                    "current turn. Prefer offsets returned by "
                    "search_portal_resource instead of reading the whole file."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "address": {"type": "string"},
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 0,
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 32_000,
                            "default": 16_000,
                        },
                    },
                    "required": ["address"],
                    "additionalProperties": False,
                },
                handler=read_resource,
                expose_schema=enabled,
                read_only=True,
                # The envelope says whether the cache answered this call.
                stable_result=stable_json_result("cache_hit"),
            ),
        )

    def _prepare(
        self,
        invocation: Any,
        *,
        enabled: bool = True,
    ) -> tuple[Any, int, str]:
        config = harness_feature_config(invocation, self.feature_id)
        tools = self._tools(
            invocation,
            max_bytes=_bounded_max_bytes(config.get("max_resource_bytes")),
            enabled=enabled,
        )
        return attach_harness_tools(invocation, tools)

    def prepare_invocation(self, invocation: Any) -> Any:
        enabled = harness_feature_enabled(invocation, self.feature_id)
        if (
            not enabled
            and bool(getattr(invocation, "is_new_backend_session", False))
        ):
            return invocation
        prepared, _added, _reason = self._prepare(
            invocation,
            enabled=enabled,
        )
        return prepared

    def apply(
        self,
        *,
        provider: Any,
        invocation: Any,
        context: Any,
        config: dict[str, Any],
        observer: Any = None,
    ) -> HarnessFeatureOutcome:
        del context, observer
        addresses = self._addresses(invocation)
        max_bytes = _bounded_max_bytes(config.get("max_resource_bytes"))
        prepared, added, reason = self._prepare(invocation, enabled=True)
        tools = self._tools(invocation, max_bytes=max_bytes)
        available = all(
            has_harness_tool(prepared, tool.name) for tool in tools
        )
        schema_tokens = approximate_value_tokens([
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in tools
        ])
        return HarnessFeatureOutcome(
            invocation=prepared,
            status="applied" if available else "skipped",
            reason=(
                (
                    "unified_resource_recall_available"
                    if bool(
                        getattr(
                            invocation,
                            "is_new_backend_session",
                            False,
                        )
                    )
                    else (
                        "unified_resource_recall_handler_available_"
                        "existing_schema_unchanged"
                    )
                )
                if available
                else reason
            ),
            metrics={
                "explicit_resource_reference_count": len(addresses),
                "resource_recall_tool_count": len(tools),
                "resource_recall_tools_added": added,
                "resource_recall_schema_tokens_estimated": schema_tokens,
                "max_resource_bytes": max_bytes,
                "eager_resource_content_tokens": 0,
                "tool_schema_exposed_this_turn": bool(
                    getattr(invocation, "is_new_backend_session", False)
                ),
            },
        )
