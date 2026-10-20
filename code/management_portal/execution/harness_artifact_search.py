from __future__ import annotations

from dataclasses import replace
from typing import Any

from .harness import (
    HarnessFeatureOutcome,
    attach_harness_tools,
    harness_feature_enabled,
    has_harness_tool,
)
from .harness_capabilities import PORTAL_TOOLS, requires
from .harness_artifact_recall import (
    READ_HARNESS_ARTIFACT_TOOL_NAME,
    SEARCH_HARNESS_ARTIFACT_TOOL_NAME,
    artifact_recall_tools,
    artifact_scope_for_invocation,
    attach_artifact_recall_tools,
)
from .harness_artifacts import HarnessArtifactStore
from .harness_history import approximate_value_tokens


ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID = "artifact_search_and_slice"


class ArtifactSearchAndSliceFeature:
    """Search exact offloaded output locally, then recall only useful slices."""

    feature_id = ARTIFACT_SEARCH_AND_SLICE_FEATURE_ID
    requires = requires({PORTAL_TOOLS})

    def __init__(self, artifact_store: HarnessArtifactStore) -> None:
        self.artifact_store = artifact_store

    def prepare_invocation(self, invocation: Any) -> Any:
        enabled = harness_feature_enabled(invocation, self.feature_id)
        if (
            not enabled
            and bool(getattr(invocation, "is_new_backend_session", False))
        ):
            return invocation
        if not enabled:
            scope = artifact_scope_for_invocation(invocation)
            if not scope:
                return invocation

            def disabled(_arguments: dict[str, Any]) -> str:
                raise PermissionError(
                    "Artifact search and slice is disabled for this turn"
                )

            hidden_tools = tuple(
                replace(tool, handler=disabled, expose_schema=False)
                for tool in artifact_recall_tools(
                    self.artifact_store,
                    scope=scope,
                    include_search=True,
                )
            )
            prepared, _added, _reason = attach_harness_tools(
                invocation,
                hidden_tools,
            )
            return prepared
        prepared, _added, _reason = attach_artifact_recall_tools(
            invocation,
            self.artifact_store,
            include_search=True,
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
        del context, config, observer
        scope = artifact_scope_for_invocation(invocation)
        if not scope:
            return HarnessFeatureOutcome(
                invocation=invocation,
                status="skipped",
                reason="stable_artifact_scope_unavailable",
            )
        prepared, added, reason = attach_artifact_recall_tools(
            invocation,
            self.artifact_store,
            include_search=True,
        )
        available = all(
            has_harness_tool(prepared, tool_name)
            for tool_name in (
                READ_HARNESS_ARTIFACT_TOOL_NAME,
                SEARCH_HARNESS_ARTIFACT_TOOL_NAME,
            )
        )
        tools = artifact_recall_tools(
            self.artifact_store,
            scope=scope,
            include_search=True,
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
                "artifact_search_and_slice_available"
                if available
                else reason
            ),
            metrics={
                "artifact_recall_tool_count": len(tools),
                "artifact_recall_tools_added": added,
                "artifact_recall_schema_tokens_estimated": schema_tokens,
                "artifact_scope_has_content": self.artifact_store.has_scope(
                    scope
                ),
            },
        )
