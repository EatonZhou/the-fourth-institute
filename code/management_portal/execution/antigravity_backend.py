"""Public imports retained for existing callers and account usage readers."""
from management_portal import antigravity_accounts as accounts
from .antigravity_cli_backend import AntigravityCliBackend as AntigravityBackend
from .antigravity_history import _pb_fields, _step_usage
from .native_options import NativeOptions


class AntigravityOptions(NativeOptions):
    permission_labels = {"default": "Portal tools only", "full-access": "Full Access"}
    default_permission_preset = "full-access"

    def handle_native_command(self, content, settings, *, account_route, can_update_runtime, actor, updated_at, **context):
        from .slash_commands import _result, model_selection_settings

        parts = str(content or "").strip().split(maxsplit=1)
        action = parts[0].lower() if parts else ""
        if action == "/swarm":
            return _result(settings, "当前 Portal 使用的 Antigravity ACP 未提供原生 Swarm。可在 Antigravity App 或 CLI 中使用 `/swarm`。",
                           title="Antigravity Swarm")
        if action != "/orchestrator":
            return None
        observed = accounts.catalog(account_route())
        labels = observed.get("model_labels", {})
        variants = {}
        for model in observed.get("models", ()):
            label = labels.get(model, "")
            name = label.strip().casefold()
            if model == "gemini-pro-orchestrator" or name == "orchestrator" or name.startswith("orchestrator ("):
                variants[model] = label or model
        title = "Antigravity Orchestrator"
        if not variants:
            return _result(settings, "当前账号的 ACP 模型列表未提供 Orchestrator。", title=title)
        selected = parts[1] if len(parts) > 1 else ""
        if not selected and len(variants) == 1:
            selected = next(iter(variants))
        if selected not in variants:
            options = "\n".join(f"- {label} · `/orchestrator {model}`" for model, label in variants.items())
            return _result(settings, "请选择该账号提供的 Orchestrator 模型。\n\n" + options, title=title)
        if not can_update_runtime:
            return _result(settings, "当前用户不能修改这个会话的模型。", title=title)
        updated = model_selection_settings(settings, {"model": selected}, actor=actor, updated_at=updated_at)
        return _result(updated, f"已选择 `{variants[selected]}`。下一轮使用该模型。", title=title, updates=updated)

    def native_options(self, options, settings):
        preset = settings.get("permission_preset")
        sandbox = settings.get("sandbox") or ""
        custom_permissions = any(settings.get(field) for field in (
            "permissions", "approval_policy", "approvals_reviewer", "profile",
        ))
        if not preset and not custom_permissions and sandbox in {"", "danger-full-access"}:
            # The settings store can express the Provider default as an empty
            # string. Unmapped custom permission settings still fail closed.
            preset = self.default_permission_preset
        return {**options, "native_tools_enabled": preset == "full-access"}
