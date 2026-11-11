"""Passive local-file capability assessment at the Execution boundary.

``available`` with scope ``policy`` means that the adapter's final options
permit native reading. It is NOT evidence that a path exists, is authorized,
is mounted in the native process, or has been read. None of the current
adapters exposes a native per-file probe: a file assessment stays unknown.
This module deliberately performs no filesystem or Provider I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Literal

from .backend_contracts import BackendInvocation


@dataclass(frozen=True)
class LocalFileReadAssessment:
    status: Literal["available", "blocked", "unknown"]
    reason: str
    scope: Literal["policy", "file"] = "policy"

    def to_payload(self) -> dict[str, str]:
        return {"status": self.status, "reason": self.reason, "scope": self.scope}


def assess_local_file_read(
    invocation: BackendInvocation, *, path: str = "",
) -> LocalFileReadAssessment:
    """Assess a *prepared* invocation, after final native/Harness options.

    Call inside Execution immediately before dispatch, and again if account,
    route, options or session changes. Raw Resident settings are insufficient.
    Passing an absolute path asks for file evidence, not a policy prediction.
    No caller-supplied receipt, Web ``os.access`` or native tool-event flag can
    promote a file to available. Consumers must not treat this as authorization.
    """
    from .provider_catalog import AGENT_PROVIDERS

    scope = "policy" if path == "" else "file"

    def result(status, reason):
        return LocalFileReadAssessment(status, reason, scope)

    if not isinstance(path, str) or (path and ("\x00" in path or not PurePosixPath(path).is_absolute())):
        return result("blocked", "absolute_path_required")
    request = invocation.request
    if request.service_type != "agent_session":
        return result("blocked", "service_has_no_native_file_tools")
    if request.operation != "turn":
        return result("blocked", "operation_is_not_turn")
    definition = AGENT_PROVIDERS.get(request.provider)
    if definition is None:
        return result("unknown", "provider_not_registered")
    if "runtime_settings" in request.runtime_options:
        return result("unknown", "runtime_options_not_prepared")
    route = request.runtime_options.get("provider_profile")
    if route and invocation.session.backend_route_id and route != invocation.session.backend_route_id:
        return result("blocked", "provider_route_mismatch")
    # The concrete adapter owns native policy semantics, not the business
    # caller, subscription name, or observability of native tool events.
    # Policy hooks are static/class methods: do not construct a client or an
    # account runtime simply to inspect a declaration.
    assess = getattr(definition.backend_class(), "local_file_read_policy", None) if definition.adapter else None
    if assess is None:
        return result("unknown" if definition.local_file_read else "blocked",
                      "adapter_policy_unavailable" if definition.local_file_read else "local_file_read_not_supported")
    try:
        policy = assess(invocation)
    except (TypeError, ValueError):
        return result("unknown", "invalid_native_options")
    if policy.status == "available" and not definition.local_file_read:
        return result("unknown", "local_file_read_not_declared")
    if path and policy.status == "available":
        return result("unknown", "native_file_probe_required")
    return replace(policy, scope=scope)
