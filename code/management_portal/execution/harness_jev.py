from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from management_portal.jev_provider import JevProvider, shared_jev_provider

from .harness import (
    HarnessFeatureOutcome,
    HarnessTool,
    attach_harness_tools,
    harness_feature_enabled,
    has_harness_tool,
)
from .harness_capabilities import PORTAL_TOOLS, requires
from .provider_catalog import AGENT_PROVIDERS


JEV_DECISION_FEATURE_ID = "jev_decision_tool"
JEV_DECIDE_TOOL_NAME = "jev_decide"


class JevDecisionToolFeature:
    """Expose Jev as a typed decision tool without pretending it is chat."""

    feature_id = JEV_DECISION_FEATURE_ID
    requires = requires({PORTAL_TOOLS})

    def __init__(self, provider: JevProvider = shared_jev_provider) -> None:
        self.provider = provider

    def _available(self) -> bool:
        try:
            item = self.provider.configuration()
            return Path(self.provider.registry.secret_path(item)).expanduser().is_file()
        except (OSError, ValueError):
            return False

    def _tool(self, *, expose_schema: bool) -> HarnessTool:
        models = list(self.provider.enabled_models())

        def decide(arguments: dict[str, Any]) -> str:
            result = self.provider.decide(
                state=arguments.get("state"),
                questions=arguments.get("questions"),
                model=str(arguments.get("model") or ""),
            )
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

        question_schema = {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["choice", "score", "noul"]},
                "instructions": {},
                "criteria": {},
            },
            "required": ["type", "instructions"],
            "additionalProperties": False,
        }
        properties: dict[str, Any] = {
            "state": {
                "description": "Text or structured JSON state to evaluate.",
            },
            "questions": {
                "type": "object",
                "description": (
                    "Questions evaluated together. choice criteria is an option map; "
                    "score criteria is an ordered 2-10 item array; noul criteria is optional."
                ),
                "additionalProperties": question_schema,
                "minProperties": 1,
                "maxProperties": 128,
            },
        }
        if models:
            properties["model"] = {"type": "string", "enum": models}
        return HarnessTool(
            name=JEV_DECIDE_TOOL_NAME,
            description=(
                "Ask TypeSafe Jev to make fast structured decisions about a state. "
                "Use Choice, Score, or Noul questions; Jev does not generate prose. "
                "State and questions are sent to the external TypeSafe API."
            ),
            input_schema={
                "type": "object",
                "properties": properties,
                "required": ["state", "questions"],
                "additionalProperties": False,
            },
            handler=decide,
            expose_schema=expose_schema,
        )

    def _prepare(self, invocation: Any, *, expose_schema: bool) -> tuple[Any, int, str]:
        if not self._available():
            return invocation, 0, "jev_provider_unavailable"
        return attach_harness_tools(
            invocation,
            (self._tool(expose_schema=expose_schema),),
        )

    def prepare_invocation(self, invocation: Any) -> Any:
        if not harness_feature_enabled(invocation, self.feature_id):
            return invocation
        if bool(getattr(invocation, "is_new_backend_session", False)):
            return invocation
        prepared, _added, _reason = self._prepare(invocation, expose_schema=False)
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
        definition = AGENT_PROVIDERS.get(invocation.request.provider)
        expose = bool(getattr(invocation, "is_new_backend_session", False)) or bool(
            definition and not definition.session_tools_fixed
        )
        prepared, added, reason = self._prepare(invocation, expose_schema=expose)
        available = has_harness_tool(prepared, JEV_DECIDE_TOOL_NAME)
        return HarnessFeatureOutcome(
            invocation=prepared,
            status="applied" if available else "skipped",
            reason="jev_decision_tool_available" if available else reason,
            metrics={
                "jev_tool_added": added,
                "tool_schema_exposed_this_turn": expose,
            },
        )
