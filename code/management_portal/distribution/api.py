"""The sole browser-facing release service. Session/CSRF belong to HTTP."""
from .store import ReleaseStore


def dispatch(operation, payload, actor):
    if not actor:
        raise PermissionError("Login required")
    if not isinstance(payload, dict):
        raise ValueError("Release arguments must be an object")
    store = ReleaseStore()
    if operation == 'targets':
        from management_common.distribution_targets import target_catalog, resolve_targets
        return {**target_catalog(), 'resolution': resolve_targets(payload.get('snapshot') or {}, payload.get('features') or [])}
    if operation == "configs.list":
        return {"configs": store.list_configs()}
    if operation == "configs.get":
        return {"config": store.get_config(payload["config_id"])}
    if operation == "configs.save":
        # Old open pages may still send this retired field; it has no save semantics.
        payload = {key: value for key, value in payload.items() if key != "expected_revision"}
        return {"config": store.save_config(actor=actor, **payload)}
    if operation == "runs.list":
        return {"runs": store.list_runs(payload.get("config_id"))}
    if operation == "runs.get":
        return {"run": store.get_run(payload["run_id"])}
    if operation == "runs.start":
        from .service import start_run
        return {"run": start_run(payload, actor)}
    if operation == "candidates":
        from .candidates import candidate
        return candidate(payload["operation"], payload.get("arguments") or {})
    raise ValueError("Unknown release operation")
