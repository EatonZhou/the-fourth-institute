"""Translate the common runtime envelope only at the native adapter boundary.

Resident supplies settings, filesystem authorization and canonical identity.
Native SDK names and permission switches never belong in that envelope.
"""
from dataclasses import replace
from management_common.portal_paths import path as portal_path
from pathlib import Path
import hashlib
import os
from collections.abc import Mapping
from typing import Any

from .provider_catalog import NATIVE_COMMANDS


class NativeOptions:
    # The slash commands this adapter's handler answers: Portal's own handler
    # (``resident_session_backend_domain``) unless ``handle_native_command``
    # answers them.  A Provider offers only what it declares, and every
    # command it offers must be one of these.
    slash_commands = frozenset({"status", "model", "permissions", "compact"})
    permission_labels = {}
    native_permission_modes = {}
    default_permission_preset = ""

    def capabilities(self, definition):
        # The Harness features this Backend can run follow from what its
        # Adapter can do; no Backend lists features by hand.
        from .harness_capabilities import available_feature_ids
        harness = definition.harness_capabilities()
        return {"schema": "tfi.runtime-capabilities.v1",
                "settings": list(definition.runtime_settings),
                "operations": sorted(definition.operations),
                "permission_presets": list(self.permission_labels), "permission_labels": dict(self.permission_labels),
                "default_permission_label": self.permission_labels.get(self.default_permission_preset, ""),
                "harness_capabilities": sorted(harness),
                "harness_features": available_feature_ids(harness)}

    def native_permission_mode(self, settings):
        preset = str(settings.get("permission_preset") or self.default_permission_preset)
        return self.native_permission_modes[preset]

    def validate_requested_fields(self, definition, updates):
        capabilities = self.capabilities(definition)
        fields = set(capabilities["settings"]) | {"prompt_injection", "allow_subagents", "agent_harness", "prompt_context", "backend_options", "backend_role", "backend_role_fallback"}
        from .resident_configuration import normalize_configuration_settings
        normalize_configuration_settings(updates, definition.provider)
        # The switch exists only where the model can start subagents; off is no change elsewhere.
        if updates.get("allow_subagents") is True and "subagents" not in capabilities["harness_capabilities"]:
            raise ValueError(f"{definition.label} does not let its model start subagents")
        if capabilities["permission_presets"]:
            fields.add("permission_preset")
        if "permission_preset" in updates and not updates["permission_preset"]:
            fields.update({"permissions", "approvals_reviewer", "approval_policy", "sandbox", "profile"})
        unsupported = set(updates) - fields
        if unsupported:
            raise ValueError(f"{definition.label} does not support settings: {', '.join(sorted(unsupported))}")
        preset = str(updates.get("permission_preset") or "")
        if preset and preset not in capabilities["permission_presets"]:
            raise ValueError(f"{definition.label} does not support permission preset {preset!r}")
        return fields

    def handle_native_command(self, content, settings, **context):
        return None

    def defaults(self, definition, record):
        # The settings modules load the Harness; a Worker imports them only when it runs a turn.
        from . import resident_runtime_settings
        settings = resident_runtime_settings.now_settings(
            model=str(record.get("model") or ""), reasoning_effort="", service_tier="", permissions="",
            approvals_reviewer="", approval_policy="", sandbox="danger-full-access", backend=definition.provider,
            permission_preset=self.default_permission_preset)
        settings.update(allow_subagents=definition.subagents_by_default)
        return settings

    def reconcile(self, definition, route, settings):
        catalog = definition.catalog(route)
        model = settings.get("model") or catalog["default_model"]
        if model not in catalog["models"]:
            model = catalog["default_model"]
        effort = settings.get("reasoning_effort", "")
        if effort not in catalog.get("efforts_by_model", {}).get(model, []):
            effort = ""
        return {**settings, "backend": definition.provider, "model": model, "reasoning_effort": effort}

    def validate_updates(self, definition, route, current, updates):
        fields = self.validate_requested_fields(definition, updates)
        existing = current if isinstance(current, dict) else {}
        model = str(updates.get("model", existing.get("model", "")))
        effort = str(updates.get("reasoning_effort", existing.get("reasoning_effort", "")))
        if effort and "reasoning_effort" not in updates:
            # A model switch keeps the current effort; a model without it needs
            # the caller's explicit choice (decision_8b2fa224616b).
            model, _ = definition.validate_settings(route, model)
            offered = definition.catalog(route).get("efforts_by_model", {}).get(model, [])
            if effort not in offered:
                raise ValueError(f"{model!r} does not offer reasoning effort {effort!r}; "
                                 f"set reasoning_effort with the model to one of {['', *offered]}")
        model, effort = definition.validate_settings(route, model, effort)
        result = {**updates, "model": model}
        if "reasoning_effort" in fields:
            result["reasoning_effort"] = effort
        return result

    def prepare(self, invocation):
        raw = invocation.request.runtime_options
        if "runtime_settings" not in raw:
            return invocation
        options = dict(raw)
        settings = options.pop("runtime_settings")
        options.pop("authorization_scope", None)
        from .provider_catalog import AGENT_PROVIDERS
        definition = AGENT_PROVIDERS[invocation.request.provider]
        route = invocation.session.backend_route_id or options.get("provider_profile") or definition.default_route
        model, effort = definition.validate_settings(route, settings.get("model", ""), settings.get("reasoning_effort", ""))
        options.update(model=model, effort=effort, provider_profile=route)
        options["config_dir"] = str(native_config_dir(
            definition.provider, options.pop("runtime_root", ""),
            invocation.request.session.binding_id or invocation.request.execution_id,
        ))
        options = self.native_options(options, settings)
        return replace(invocation, request=replace(invocation.request, runtime_options=options))

    def native_options(self, options, settings):
        return options


def native_config_dir(provider: str, runtime_root: str, session_key: str) -> Path:
    """The configuration directory of a native session that no account owns.

    A Resident's requests name its runtime directory; other callers' sessions
    get one Portal keeps per binding.
    """

    if runtime_root:
        return Path(runtime_root) / provider / "config"
    root = Path(os.getenv("PORTAL_AGENT_CONFIG_ROOT", str(portal_path("staging_root") / "shared" / "agent-config")))
    return root / hashlib.sha256(session_key.encode()).hexdigest() / provider / "config"


def claude_route_config_dir(route: str) -> Path | None:
    """A Claude subscription route's configuration directory.

    The default route reaches DeepSeek's API and has none; its sessions use
    ``native_config_dir``.
    """

    from management_portal import claude_provider_catalog as catalog
    from .provider_catalog import AGENT_PROVIDERS

    if route == AGENT_PROVIDERS["claude"].default_route:
        return None
    profile = catalog.profile(route)
    return catalog.config_directory(profile) if catalog.is_native(profile) else None


class ClaudeOptions(NativeOptions):
    permission_labels = {"default": "Restricted", "full-access": "Full Access"}
    native_permission_modes = {"default": "dontAsk", "full-access": "bypassPermissions"}
    default_permission_preset = "full-access"

    def native_options(self, options, settings):
        config_dir = claude_route_config_dir(options["provider_profile"])
        if config_dir is not None:
            options["config_dir"] = str(config_dir)
        options["permission_mode"] = self.native_permission_mode(settings)
        return options


class CodexOptions(NativeOptions):
    @staticmethod
    def runtime_options(
        overrides: Mapping[str, object],
        *,
        default_sandbox: str = "",
        allow_subagents: bool | None = None,
        agent_harness: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Translate normalized Portal settings to the Codex Backend contract."""

        permissions = str(overrides.get("permissions") or "").strip()
        sandbox = str(overrides.get("sandbox") or "").strip()
        if not permissions and not sandbox:
            sandbox = str(default_sandbox or "").strip()
        approval_policy = str(overrides.get("approval_policy") or "").strip()
        if not approval_policy and (
            sandbox == "danger-full-access"
            or permissions == ":danger-full-access"
        ):
            approval_policy = "never"

        options: dict[str, Any] = {
            key: value
            for key, value in {
                "model": str(overrides.get("model") or "").strip(),
                "effort": str(overrides.get("reasoning_effort") or "").strip(),
                "service_tier": str(overrides.get("service_tier") or "").strip(),
                # Omit collaboration mode when the caller did not select one.
                # Codex treats an explicit "default" mode as a configured native
                # collaboration mode, which requires an explicit model.  Absence
                # means to use the backend's normal model and mode defaults.
                "collaboration_mode": str(
                    overrides.get("collaboration_mode") or ""
                ).strip(),
                "permissions": permissions,
                "sandbox": sandbox,
                "approval_policy": approval_policy,
                "approvals_reviewer": str(
                    overrides.get("approvals_reviewer") or ""
                ).strip(),
            }.items()
            if value
        }
        if allow_subagents is not None:
            options["allow_subagents"] = bool(allow_subagents)
        if agent_harness is not None:
            options["agent_harness"] = dict(agent_harness)
        return options

    # Codex's own handler (``codex_runtime_settings.handle_codex_native_command``) answers every one.
    slash_commands = frozenset(NATIVE_COMMANDS)
    permission_labels = {"default": "Ask", "auto-review": "Auto-review", "full-access": "Full Access"}
    default_permission_preset = "full-access"

    def handle_native_command(self, content, settings, **context):
        text = str(content or "").strip()
        parts = text[1:].split() if text.startswith("/") else []
        command = parts[0].casefold() if parts else ""
        if command not in self.slash_commands:
            return {"handled": False}
        from . import codex_runtime_settings
        # Status, permissions and other local controls work before any AI is
        # connected. Only the model picker/validator reads an account catalog.
        account_route = context.pop("account_route")
        context["account_id"] = account_route() if command == "model" else ""
        return codex_runtime_settings.handle_codex_native_command(content, settings, **context)

    def defaults(self, definition, record):
        from . import resident_runtime_settings
        names = {"model": "MODEL", "reasoning_effort": "EFFORT", "service_tier": "SERVICE_TIER",
                 "permissions": "PERMISSION_PROFILE", "approvals_reviewer": "APPROVALS_REVIEWER"}
        settings = resident_runtime_settings.now_settings(**{key: os.getenv("RESIDENT_AGENT_CODEX_" + suffix, "").strip() for key, suffix in names.items()},
            approval_policy="", sandbox="danger-full-access", backend=definition.provider)
        settings.update(allow_subagents=definition.subagents_by_default)
        return settings

    def reconcile(self, definition, route, settings):
        from . import codex_runtime_settings
        return codex_runtime_settings.resolve_codex_runtime_configuration(settings, account_id=route)

    def validate_updates(self, definition, route, current, updates):
        from . import codex_runtime_settings
        self.validate_requested_fields(definition, updates)
        return codex_runtime_settings.validate_codex_runtime_updates(current, updates, account_id=route)

    def prepare(self, invocation):
        from . import codex_runtime_settings
        raw = dict(invocation.request.runtime_options)
        if "runtime_settings" not in raw:
            return invocation
        settings = raw.pop("runtime_settings")
        raw.pop("authorization_scope", None)
        environment = dict(raw.pop("resident_environment", {}))
        from .provider_catalog import AGENT_PROVIDERS
        options = {} if settings.get("inherit_session_runtime") else self.runtime_options(
            codex_runtime_settings.codex_cli_overrides(settings), default_sandbox="danger-full-access",
            allow_subagents=raw.get("allow_subagents", AGENT_PROVIDERS["codex"].subagents_by_default),
            agent_harness=raw.get("agent_harness"),
        )
        options.update(raw)
        if environment:
            overrides = options.setdefault("config_overrides", {})
            overrides["shell_environment_policy.set"] = {**overrides.get("shell_environment_policy.set", {}), **environment}
        return replace(invocation, request=replace(invocation.request, runtime_options=options))


KIMI_OWNER_TOOLS = ["Read", "Write", "Edit", "Grep", "Glob", "Bash", "TaskList", "TaskOutput", "TaskStop",
    "CronCreate", "CronList", "CronDelete", "ReadMediaFile", "TodoList", "Skill", "WebSearch", "Agent",
    "AgentSwarm", "FetchURL", "EnterPlanMode", "ExitPlanMode", "CreateGoal", "GetGoal", "SetGoalBudget", "UpdateGoal", "mcp__*"]
KIMI_READER_TOOLS = ["Read", "ReadMediaFile", "Glob", "Grep", "WebSearch", "FetchURL"]
# The tools with which Kimi Code's model starts subagents.
KIMI_SUBAGENT_TOOLS = frozenset({"Agent", "AgentSwarm"})


class KimiOptions(NativeOptions):
    permission_labels = {"default": "Manual", "full-access": "Full Access"}
    native_permission_modes = {"default": "manual", "full-access": "yolo"}
    default_permission_preset = "full-access"

    def defaults(self, definition, record):
        return super().defaults(definition, {**record, "model": record.get("model") or definition.catalog(
            record.get("backend_route_id") or "")["default_model"]})

    def prepare(self, invocation):
        options = dict(invocation.request.runtime_options)
        if "runtime_settings" not in options:
            return invocation
        settings = options.pop("runtime_settings")
        options.pop("authorization_scope", None)
        owner = bool(options.get("is_owner"))
        tools = KIMI_OWNER_TOOLS if owner else KIMI_READER_TOOLS
        from .provider_catalog import AGENT_PROVIDERS
        if not AGENT_PROVIDERS["kimi"].allows_subagents(options):
            tools = [tool for tool in tools if tool not in KIMI_SUBAGENT_TOOLS]
        route = invocation.session.backend_route_id or options.get("provider_profile") or ""
        model, _ = AGENT_PROVIDERS["kimi"].validate_settings(route, settings.get("model", ""), "")
        options.update(model=model, provider_profile=route, permission_mode=self.native_permission_mode(settings),
                       tools=list(tools))
        return replace(invocation, request=replace(invocation.request, runtime_options=options))


def prepare_invocation(invocation):
    from . import provider_catalog
    definition = provider_catalog.AGENT_PROVIDERS[invocation.request.provider]
    return definition.runtime_adapter().prepare(invocation)
