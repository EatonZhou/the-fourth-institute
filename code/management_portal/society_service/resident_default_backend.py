"""Resolve intent before first admission; a live session keeps its binding."""
from management_portal.execution.account_registrations import role_configuration
from management_portal.hatg.contracts import InstructionError


def selection_mode(record):
    settings = record.get("execution_settings") or {}
    if "account_selection" in settings:
        return settings["account_selection"]
    # Existing explicitly configured Residents stay fixed. Roles are the only
    # provenance that authorizes the site's automatic account policy.
    if settings.get("backend_role"):
        from management_portal.execution.account_registrations import resolve_role
        try:
            role, _ = resolve_role(settings["backend_role"])
        except InstructionError as error:
            if error.details.get("reason") == "backend_role.default_missing":
                return "fixed"  # A removed role cannot authorize changing accounts.
            raise
        return role["account_selection"]
    return "fixed"


def selection_priority(record):
    """The role owns the site's ordered backend policy, shared by admission and recovery."""
    from management_portal.execution.account_registrations import resolve_role
    try:
        role, _ = resolve_role((record.get("execution_settings") or {}).get("backend_role") or "default")
    except InstructionError as error:
        if error.details.get("reason") == "backend_role.default_missing":
            return []
        raise
    return list(role.get("backend_priority") or [])


def configuration(record, binding):
    if any(binding.get(key) for key in ("backend_route_id", "backend_session_id", "provider_session_ref")):
        return None
    settings = record.get("execution_settings") or {}
    configured = None
    if not record.get("backend_route_id") and record.get("backend") != "kimi":
        try:
            configured = role_configuration(settings.get("backend_role") or "default")
        except InstructionError as error:
            if error.details.get("reason") == "backend_role.default_missing":
                return None
            raise
    if configured is None:
        if selection_mode(record) != "auto":
            return None
        configured = {"backend": record["backend"], "backend_route_id": record.get("backend_route_id", ""),
                      "runtime_settings": {**settings, "account_selection": "auto"}}
    if configured["runtime_settings"].get("account_selection") == "auto":
        from management_portal.execution.account_selection import select, observations, priority_candidates, compatible
        from management_portal.execution.account_registrations import registrations
        backend = configured["backend"]
        priority = selection_priority(record)
        # Without an Agent order, direct completion needs no native account observation.
        if backend == "provider_api" and not priority:
            return configured
        runtime = configured["runtime_settings"]
        samples = observations()
        source = next((r for r in registrations(backend) if r["route_id"] == configured["backend_route_id"]), {})
        sample = next((a for a in samples if a["account_id"] == source.get("account_id")), {})
        model = runtime.get("model") or (sample.get("selection_catalog") or {}).get("default_model", "")
        if priority:
            from management_portal.execution.accounts import DefaultAccountUnavailable
            ordered = priority_candidates(samples, priority, source=source, model=model,
                                          effort=runtime.get("reasoning_effort", ""))
            if not ordered:
                raise DefaultAccountUnavailable("No backend in the configured order has verified available quota")
            chosen = ordered[0]
            if not model or not compatible(chosen, source):
                model = chosen["selection_catalog"]["default_model"]
                runtime.pop("reasoning_effort", None)
            configured.update(backend=chosen["provider"], backend_route_id=chosen["backend_route_id"])
            runtime.update(model=model, account_selection="auto")
            return configured
        if not model:
            from management_portal.execution.accounts import DefaultAccountUnavailable
            raise DefaultAccountUnavailable("Account model catalog is not ready")
        row = select(backend, anchor=source.get("account_id", ""), model=model,
                     effort=runtime.get("reasoning_effort", ""), accounts=samples)
        configured["backend_route_id"] = row["route_id"]
        runtime.update(model=model, account_selection="auto")
    return configured
