from __future__ import annotations

from dataclasses import replace
from typing import Any

from .harness import HarnessFeatureOutcome


TOOL_CALL_GRANULARITY_FEATURE_ID = "tool_call_granularity"
TOOL_CALL_GRANULARITY_POLICY_VERSION = 2
TOOL_CALL_GRANULARITY_POLICY_MARKER = (
    "<agent-harness-tool-granularity-policy>"
)
TOOL_CALL_GRANULARITY_POLICY = """\
<agent-harness-tool-granularity-policy>
When gathering independent read-only evidence, batch or parallelize available
search, read, and check tools instead of making many small sequential calls.
Only combine calls when their total model-facing output is expected to stay
bounded. For image inspection, use a resized preview unless exact source
resolution is necessary. Use the available tools' output limits and avoid
returning multiple full-resolution images or large binary results together.
Avoid rereading unchanged content. Keep writes, destructive actions, and steps
whose inputs depend on earlier results sequential. Use only tools that are
actually available.
</agent-harness-tool-granularity-policy>"""


class ToolCallGranularityFeature:
    """Steer the provider loop toward larger safe read-only tool batches."""

    feature_id = TOOL_CALL_GRANULARITY_FEATURE_ID

    def apply(
        self,
        *,
        provider: Any,
        invocation: Any,
        context: Any,
        config: dict[str, Any],
        observer: Any = None,
    ) -> HarnessFeatureOutcome:
        del provider, context, config, observer
        prompt = str(getattr(invocation, "user_prompt", "") or "")
        if TOOL_CALL_GRANULARITY_POLICY_MARKER in prompt:
            return HarnessFeatureOutcome(
                invocation=invocation,
                status="skipped",
                reason="policy_already_attached",
            )
        updated_prompt = f"{TOOL_CALL_GRANULARITY_POLICY}\n\n{prompt}"
        return HarnessFeatureOutcome(
            invocation=replace(invocation, user_prompt=updated_prompt),
            status="applied",
            reason="tool_granularity_policy_attached",
            metrics={
                "policy_version": TOOL_CALL_GRANULARITY_POLICY_VERSION,
                "policy_chars": len(TOOL_CALL_GRANULARITY_POLICY),
                "prompt_chars_before": len(prompt),
                "prompt_chars_after": len(updated_prompt),
            },
        )
