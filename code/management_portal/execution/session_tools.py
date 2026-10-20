"""The Harness tools a Resident's Provider session has to offer.

Some Providers' sessions keep the Portal tools they started with
(``AgentProvider.session_tools_fixed``): a Codex thread cannot add dynamic
tools on thread/resume or thread/fork.  A session that started before a
required tool existed never offers it, and only a new session does.  Bindings
record the tools their session started with; bindings older than that record
have none.
"""

from __future__ import annotations

from collections.abc import Mapping

from .provider_catalog import AGENT_PROVIDERS

# Every Resident turn offers hatg_instruction (harness_isa_tool.ISA_TOOL_NAME).
REQUIRED_SESSION_TOOLS = frozenset({"hatg_instruction"})


def session_lacks_required_tools(binding: Mapping[str, object] | None) -> bool:
    """Whether a bound Provider session can never offer a required tool."""

    if not isinstance(binding, Mapping):
        return False
    definition = AGENT_PROVIDERS.get(str(binding.get("provider") or ""))
    if definition is None or not definition.session_tools_fixed:
        return False
    if not str(binding.get("backend_session_id") or "").strip():
        return False
    names = binding.get("harness_tool_names") or ()
    if not isinstance(names, (list, tuple, set, frozenset)):
        names = ()
    return not REQUIRED_SESSION_TOOLS <= {str(name) for name in names}
