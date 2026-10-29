"""Freeze Resident-selected tools and enforce revocation at their call boundary.

The durable Execution request owns desired configuration; the existing recovery
checkpoint owns the assembly. Provider-native catalogs we cannot observe remain
unknown. Neither discovery nor missing historical registration replaces sessions.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from typing import Any

from .harness import capabilities_of


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def current_selection(resident_id: str, execution_id: str) -> dict:
    """Read authoritative desired state as the executing Resident, not the model."""
    from .harness_isa_tool import run_instruction
    receipt = run_instruction(resident_id, {
        "instruction": "GetResidentSettings", "arguments": {"resident_id": resident_id},
    }, execution_id=execution_id)
    if receipt.get("status") != "committed":
        raise RuntimeError("Cannot confirm current Resident capability selection")
    return dict((receipt.get("result", {}).get("settings") or {}).get("capability_config") or {})


def _enabled(config: dict, tool: Any) -> bool:
    connection = str(getattr(tool, "connection_ref", "") or "")
    if connection:
        for item in config.get("mcp", []):
            if item["ref"] == connection:
                return item.get("enabled", True) and getattr(tool, "local_name", "") in item.get("tools", [])
        # Removing a selected connection revokes access immediately.
        return False
    ref = str(getattr(tool, "capability_ref", "") or f"portal:{tool.name}")
    for item in config.get("tools", []):
        if item["ref"] == ref:
            return item.get("enabled", True)
    return True  # No configuration on a legacy Resident preserves its built-ins.


def _guard(tool: Any, resident_id: str, execution_id: str, selected: bool, context: Any):
    def handler(arguments: dict) -> str:
        if context.is_cancelled():
            raise InterruptedError("Resident tool call cancelled")
        if not selected or not _enabled(current_selection(resident_id, execution_id), tool):
            raise PermissionError(f"Resident capability disabled: {tool.capability_ref or tool.name}")
        reference = tool.capability_ref or f"portal:{tool.name}"
        context.emit("resident_capability_call", {"ref": reference, "delivery": "observed_call", "result": "running"})
        try:
            result = tool.handler(arguments)
        except Exception as exc:
            context.emit("resident_capability_call", {"ref": reference, "result": "failed", "error_type": type(exc).__name__})
            raise
        # Returned JSON may itself contain a business rejection or unknown result.
        context.emit("resident_capability_call", {"ref": reference, "result": "returned"})
        return result
    return replace(tool, handler=handler, expose_schema=bool(selected and tool.expose_schema))


def _contract_changes(saved: list[dict], current: list[dict]) -> list[dict]:
    """Tools added, removed or redefined since the snapshot, by alias."""
    def comparable(tool: dict) -> dict:
        return {key: value for key, value in tool.items() if key != "application"}
    before = {tool["alias"]: comparable(tool) for tool in saved}
    after = {tool["alias"]: comparable(tool) for tool in current}
    return [{"ref": (after.get(alias) or before[alias])["ref"], "alias": alias,
             "change": "added" if alias not in before else "removed" if alias not in after else "changed"}
            for alias in sorted(before.keys() | after.keys()) if before.get(alias) != after.get(alias)]


def prepare(invocation: Any, context: Any) -> Any:
    request = invocation.request
    resident_id = str((request.metadata or {}).get("resident_id") or "")
    if request.operation not in {"turn", "generate", "create", "fork"} or not resident_id.startswith("resident_"):
        return invocation
    options = dict(request.runtime_options or {})
    config = dict((options.get("runtime_settings") or {}).get("capability_config") or {})
    saved = (invocation.recovery_checkpoint or {}).get("capability_assembly")
    from .provider_catalog import AGENT_PROVIDERS
    provider = AGENT_PROVIDERS.get(request.provider)
    fixed_session = bool(provider and provider.session_tools_fixed and invocation.session.backend_session_id)
    tools = list(invocation.harness_tools)
    observations: list[dict] = []
    selected_connections = [item for item in config.get("mcp", [])
                            if item.get("enabled", True) and item.get("tools")]
    if selected_connections:
        if "portal_tools" not in capabilities_of(invocation):
            if any(item.get("required") for item in selected_connections):
                raise ValueError("required MCP tools unsupported by this Provider")
            observations.extend({"ref": item["ref"], "observation": "unknown", "reason": "provider_tools_unsupported"}
                                for item in selected_connections)
        else:
            from .resident_mcp import materialize_mcp_tools
            attached, observed = materialize_mcp_tools(selected_connections, caller=resident_id,
                                                      execution_id=request.execution_id,
                                                      is_cancelled=context.is_cancelled,
                                                      expected_dependencies=(saved.get("mcp_dependencies", {})
                                                                             if saved is not None else None))
            tools.extend(attached)
            observations.extend(observed)
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)):
        raise ValueError("Resident tool alias collision")
    refs = {tool.capability_ref or f"portal:{tool.name}" for tool in tools}
    for item in config.get("tools", []):
        if item.get("enabled", True) and item["ref"] not in refs:
            if item.get("required"):
                raise ValueError(f"required Resident capability missing: {item['ref']}")
            observations.append({"ref": item["ref"], "observation": "missing"})
    declarations = []
    guarded = []
    for tool in tools:
        enabled = _enabled(config, tool)
        selection = next((item for item in config.get("tools", [])
                          if item["ref"] == (tool.capability_ref or f"portal:{tool.name}")), None)
        if tool.connection_ref:
            selection = next((item for item in config.get("mcp", []) if item["ref"] == tool.connection_ref), None)
        pending = fixed_session and tool.name not in invocation.available_harness_tool_names
        if pending and enabled and selection and selection.get("required"):
            raise ValueError(f"Required capability pending explicit new session: {tool.capability_ref or tool.name}")
        application = ("disabled" if not enabled else "pending_session" if pending and selection
                       else "unknown" if pending else "this_turn")
        schema = {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        declarations.append({"ref": tool.capability_ref or f"portal:{tool.name}", "alias": tool.name,
                             "contract_digest": digest(schema), "selected": enabled,
                             "expose_schema": bool(enabled and tool.expose_schema),
                             "observation": "found", "delivery": "not_sent", "application": application})
        guarded.append(_guard(tool, resident_id, request.execution_id, enabled, context))
    dependencies = {}
    for observation in observations:
        if observation.get("connection_digest") and observation.get("tool_digest"):
            dependency = dependencies.setdefault(observation["ref"], {
                "connection_digest": observation["connection_digest"], "tools": {}})
            dependency["tools"][observation["local_name"]] = observation["tool_digest"]
    snapshot = {"schema": "tfi.resident-assembly.v1", "resident_id": resident_id,
                "execution_id": request.execution_id, "provider": request.provider,
                "backend_session_id": str(invocation.session.backend_session_id or ""),
                "backend_route_id": str(invocation.session.backend_route_id or ""),
                "settings_revision": (options.get("resident_assembly") or {}).get("settings_revision", ""),
                "adapter": str(getattr(provider, "adapter", "") or "model_api"),
                "runtime_package": str(getattr(provider, "runtime_package", "") or ""),
                "selection_digest": digest(config), "tools": declarations,
                "observations": observations, "mcp_dependencies": dependencies,
                "provider_managed": "unknown"}
    if saved is not None:
        # Only a changed Resident configuration fails recovery. A release that
        # restarts the Worker mid-turn may change Portal tool definitions (the
        # HATG tool lists every instruction); the turn continues on them and
        # the frozen snapshot stays the record of what was accepted.
        if (saved.get("execution_id") != request.execution_id or
                saved.get("selection_digest") != snapshot["selection_digest"] or
                saved.get("mcp_dependencies", {}) != dependencies):
            raise ValueError("Resident capability configuration changed during execution recovery")
        changes = _contract_changes(saved.get("tools", []), declarations)
        if changes:
            context.emit("resident_capability_contract_changed", {"reason": "execution_recovery", "changes": changes})
        snapshot = deepcopy(saved)
    # Unlike best-effort telemetry, this checkpoint must survive acceptance.
    context.record_capability_assembly(deepcopy(snapshot))
    options["resident_assembly"] = {**dict(options.get("resident_assembly") or {}), **snapshot}
    context.emit("resident_capability_assembly", snapshot)
    return replace(invocation, request=replace(request, runtime_options=options),
                   harness_tools=tuple(guarded))


def require_offered_tools(invocation: Any, available_names: Any) -> None:
    """Reject known adapter omissions before it starts a model turn."""
    config = ((invocation.request.runtime_options or {}).get("runtime_settings") or {}).get("capability_config") or {}
    offered = [tool for tool in invocation.harness_tools if tool.name in available_names]
    references = {tool.capability_ref or f"portal:{tool.name}" for tool in offered}
    missing = [item["ref"] for item in config.get("tools", [])
               if item.get("enabled", True) and item.get("required") and item["ref"] not in references]
    for item in config.get("mcp", []):
        if item.get("enabled", True) and item.get("required"):
            names = {tool.local_name for tool in offered if tool.connection_ref == item["ref"]}
            missing.extend(f"{item['ref']}#{name}" for name in item.get("tools", []) if name not in names)
    if missing:
        raise ValueError(f"Required Resident capability could not be offered: {', '.join(sorted(missing))}")


def observe_delivery(invocation: Any, context: Any, *, completed: bool,
                     available_names: Any = None) -> None:
    """Keep adapter transport evidence separate from configuration and business results."""
    assembly = invocation.request.runtime_options.get("resident_assembly") or {}
    if assembly.get("schema") != "tfi.resident-assembly.v1":
        return
    from .provider_catalog import AGENT_PROVIDERS
    provider = AGENT_PROVIDERS.get(invocation.request.provider)
    fixed = bool(provider and provider.session_tools_fixed and invocation.session.backend_session_id)
    for tool in assembly.get("tools", []):
        if not tool["expose_schema"]:
            continue
        # Use the adapter's final catalog, not the requested catalog. A normally
        # completed turn may have continued without optional Portal tools.
        context.emit("resident_capability_delivery", {
            "ref": tool["ref"], "alias": tool["alias"],
            "delivery": "sent" if (completed and not fixed and invocation.request.operation == "turn"
                                     and tool["alias"] in (available_names or ())
                                     and "portal_tools" in capabilities_of(invocation)) else "unknown",
            "acceptance": "unknown", "evidence": ("adapter_tool_catalog" if completed and available_names is not None
                                                     else "adapter_returned" if completed else "adapter_failed"),
            "backend_session_id": str(invocation.session.backend_session_id or ""),
        })
