"""Translate the Resident turn's writable directories to ACP workspace roots."""
from pathlib import Path

from management_common.host_instances import registered_reference
from management_common.instance_manifest import current_instance


def additional_directories(request) -> list[str]:
    metadata = getattr(request, "metadata", {}) or {}
    resident = metadata.get("resident_id")
    scope = metadata.get("filesystem_scope")
    if (not resident or not isinstance(scope, dict) or scope.get("principal_id") != resident
            or scope.get("cwd") != request.cwd or not isinstance(scope.get("paths"), list)):
        return []
    agent_id = getattr(request, "agent_id", None)
    if metadata.get("business_type") == "resident_session_operation":
        if request.operation not in {"create", "turn"} or agent_id not in (resident, f"resident:{resident}"):
            return []
    elif metadata.get("business_type") != "resident" or agent_id != resident:
        return []

    instance = current_instance()
    cwd = Path(request.cwd)
    roots = []
    # ACP gives a root and its descendants the same access. Skip an
    # additional root if it would expose a read-only binding inside it.
    restricted = [Path(row["path"]) for row in scope["paths"]
                  if isinstance(row, dict) and row.get("source") == "workspace_binding"
                  and row.get("access") != "write" and isinstance(row.get("path"), str)]
    for entry in scope["paths"]:
        if not isinstance(entry, dict) or entry.get("access") != "write":
            continue
        source, raw = entry.get("source"), entry.get("path")
        if source not in ("resident_folder", "workspace_binding") or not isinstance(raw, str) or not raw:
            continue
        path = Path(raw)
        if (not path.is_absolute() or path == path.parent or path == cwd or path in roots
                or any(child.is_relative_to(path) for child in restricted)):
            continue
        if source == "resident_folder" and (path.name != resident or path.parent.name != "residents"):
            continue
        try:
            # Reapply the Workspace registration boundary at use time. It
            # rejects symlink retargeting and overlap with a foreign instance.
            registered_reference(instance, raw)
            if (not path.is_dir() or instance.root.is_relative_to(path)
                    or instance.area("lineages").is_relative_to(path)
                    or instance.area("provider-state").is_relative_to(path)
                    or path.is_relative_to(instance.area("provider-state"))):
                continue
        except (OSError, ValueError, RuntimeError):
            continue
        roots.append(path)
    return [str(path) for path in roots]
