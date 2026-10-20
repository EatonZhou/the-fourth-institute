"""What the Agent Harness may rely on in a Backend Adapter.

Each name states one ability of an Adapter.  ``AgentProvider`` declares which
of them a Backend has; a Harness feature declares which it needs.  The Harness
compares the two and never looks at the Backend's name.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# The Adapter offers Portal-executed tools (Harness tools) to the model and
# hands every result to the Harness before the model reads it.
PORTAL_TOOLS = "portal_tools"
# The Adapter reports the Provider's own tool calls of a running turn to the
# Harness, in the common tool-event shape (see ``native_tool_events``).
NATIVE_TOOL_EVENTS = "native_tool_events"
# The Provider takes more input while a turn is running.
ACTIVE_TURN_INPUT = "active_turn_input"
# The Provider compacts by itself at a share of the context window that the
# Adapter can set.
AUTO_COMPACT_THRESHOLD = "auto_compact_threshold"
# The Adapter reports the current main-agent context during a running turn.
CONTEXT_USAGE_EVENTS = "context_usage_events"
# After a Provider error the Adapter continues the turn in the same session,
# under the recovery policy the Harness attached.
ERROR_CONTINUATION = "error_continuation"
# Input images reach the Adapter as local files, which the Harness may replace
# by smaller copies before the Adapter reads them.
NATIVE_IMAGE_INPUT = "native_image_input"
# The Adapter can ask the Provider for a bounded detail level of input images.
INPUT_IMAGE_DETAIL = "input_image_detail"
# The reasoning effort can change from one turn to the next without
# restarting the Provider session.
TURN_REASONING_EFFORT = "turn_reasoning_effort"
# The Provider's model can start subagents, and the Adapter makes the
# Resident's setting "允许开启 sub agent" the Provider's control: when it is
# off the model cannot start one.  Whether a new Resident starts with it on
# is ``AgentProvider.subagents_by_default``.
SUBAGENTS = "subagents"
# The Adapter reports the subagents the model starts in one shape, whatever
# the Provider's own mechanism: each item of a subagent's own steps as
# ``subagent/event`` (its trace key, the call that started it and its
# ancestors), its running card as ``subagent/progress`` and its finished card
# as an ``item/completed`` ``subagentRun`` (``provider_subagent_runs``).  A
# card says ``background`` when the model's call returned before the run
# ended.  Only the main agent's own subagents have cards.
SUBAGENT_EVENTS = "subagent_events"
# The Adapter stops one running subagent while its parent turn goes on: an
# Execution input control with a single ``subagent_stop`` item naming the
# card's ``tool_use_id``.  The Chat offers Stop on a running card only then.
SUBAGENT_STOP = "subagent_stop"

HARNESS_CAPABILITIES = frozenset({
    PORTAL_TOOLS,
    NATIVE_TOOL_EVENTS,
    ACTIVE_TURN_INPUT,
    AUTO_COMPACT_THRESHOLD,
    CONTEXT_USAGE_EVENTS,
    ERROR_CONTINUATION,
    NATIVE_IMAGE_INPUT,
    INPUT_IMAGE_DETAIL,
    TURN_REASONING_EFFORT,
    SUBAGENTS,
    SUBAGENT_EVENTS,
    SUBAGENT_STOP,
})

# A feature's ``requires`` is a tuple of alternatives; each alternative is a
# set of capabilities that together let one part of the feature work.  The
# feature can be planned when at least one alternative is satisfied.
Requirement = tuple[frozenset[str], ...]


def requires(*alternatives: Iterable[str]) -> Requirement:
    return tuple(frozenset(alternative) for alternative in alternatives)


def provider_capabilities(provider_id: object, route: object = "") -> frozenset[str]:
    """The capabilities the Backend ``provider_id`` declares (none if unknown)."""

    from .provider_catalog import AGENT_PROVIDERS

    definition = AGENT_PROVIDERS.get(str(provider_id or "").strip())
    if definition is None:
        return frozenset()
    return definition.harness_capabilities(str(route or "").strip())


def _invocation_route(invocation: Any) -> str:
    session = getattr(invocation, "session", None)
    request = getattr(invocation, "request", None)
    options = getattr(request, "runtime_options", None)
    options = options if isinstance(options, dict) else {}
    return str(
        getattr(session, "backend_route_id", "")
        or options.get("provider_profile")
        or ""
    ).strip()


def invocation_capabilities(invocation: Any) -> frozenset[str]:
    """The capabilities of the Backend this invocation is addressed to."""

    request = getattr(invocation, "request", None)
    return provider_capabilities(
        getattr(request, "provider", ""),
        _invocation_route(invocation),
    )


def adapter_capabilities(provider: Any, invocation: Any = None) -> frozenset[str]:
    """The capabilities of the Adapter that will run the turn.

    An Adapter object may declare ``harness_capabilities`` itself; otherwise
    the declaration of its Backend in the Provider catalog applies.
    """

    declared = getattr(provider, "harness_capabilities", None)
    if declared is not None and not callable(declared):
        return frozenset(str(item) for item in declared)
    return provider_capabilities(
        getattr(provider, "provider_id", ""),
        _invocation_route(invocation) if invocation is not None else "",
    )


def unmet_requirement(requirement: Requirement, capabilities: Iterable[str]) -> str:
    """``""`` when a feature can be planned, else ``requires_<capability>``.

    The reason names the first capability missing from the feature's first
    alternative, which is the feature's main way of working.
    """

    if not requirement:
        return ""
    available = frozenset(capabilities)
    if any(alternative <= available for alternative in requirement):
        return ""
    missing = sorted(requirement[0] - available)
    return f"requires_{missing[0]}" if missing else ""


def feature_requirement(feature: Any) -> Requirement:
    """A feature's declared requirement; a feature that declares none runs anywhere."""

    raw = getattr(feature, "requires", ())
    if not isinstance(raw, (tuple, list)):
        return ()
    return tuple(
        frozenset(str(item) for item in alternative)
        for alternative in raw
        if isinstance(alternative, (frozenset, set, tuple, list))
    )


def default_feature_requirements() -> dict[str, Requirement]:
    """Requirements of the features ``default_agent_harness`` registers."""

    from .harness import HARNESS_FEATURES
    return {name: feature_requirement(definition.feature_class())
            for name, definition in HARNESS_FEATURES.items()}


def available_feature_ids(capabilities: Iterable[str]) -> list[str]:
    """The registered features a Backend with these capabilities can run."""

    available = frozenset(capabilities)
    return [
        feature_id
        for feature_id, requirement in default_feature_requirements().items()
        if not unmet_requirement(requirement, available)
    ]
