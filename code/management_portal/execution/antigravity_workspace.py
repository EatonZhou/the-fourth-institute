"""Translate the Resident turn's directories to ACP workspace roots.

Full Access gives the widest roots Portal can name: every instance area except
provider-state, and the host user's home. A restricted tier gives the turn's
writable roots. ACP gives a root and its descendants the same access, so a
read-only binding inside a writable root becomes writable; the writable root is
kept rather than dropped.
"""
from pathlib import Path

from management_common.host_instances import registered_reference
from management_common.instance_manifest import AREAS, current_instance


def _resident_scope(request):
    metadata = getattr(request, "metadata", {}) or {}
    resident = metadata.get("resident_id")
    scope = metadata.get("filesystem_scope")
    if (not resident or not isinstance(scope, dict) or scope.get("principal_id") != resident
            or scope.get("cwd") != request.cwd or not isinstance(scope.get("paths"), list)):
        return None, None
    agent_id = getattr(request, "agent_id", None)
    if metadata.get("business_type") == "resident_session_operation":
        if request.operation not in {"create", "turn"} or agent_id not in (resident, f"resident:{resident}"):
            return None, None
    elif metadata.get("business_type") != "resident" or agent_id != resident:
        return None, None
    return resident, scope


def additional_directories(request, *, full_access=False) -> list[str]:
    resident, scope = _resident_scope(request)
    if scope is None:
        return []
    instance = current_instance()
    return _full_access_roots(instance, Path(request.cwd)) if full_access else _writable_roots(instance, resident, scope, Path(request.cwd))


def _full_access_roots(instance, cwd):
    private = instance.area("provider-state")
    candidates = [instance.area(name) for name in AREAS if name != "provider-state"] + [Path.home()]
    roots = []
    for path in candidates:
        try:
            if (not path.is_absolute() or path == path.parent or not path.is_dir() or path in roots
                    or private.is_relative_to(path) or path.is_relative_to(private)):
                continue
        except OSError:
            continue
        roots.append(path)
    # A root inside another root adds nothing.
    return [str(path) for path in roots if not any(path != other and path.is_relative_to(other) for other in roots)]


def _writable_roots(instance, resident, scope, cwd):
    roots = []
    for entry in scope["paths"]:
        if not isinstance(entry, dict) or entry.get("access") != "write":
            continue
        source, raw = entry.get("source"), entry.get("path")
        if source not in ("resident_folder", "workspace_binding") or not isinstance(raw, str) or not raw:
            continue
        path = Path(raw)
        if not path.is_absolute() or path == path.parent or path == cwd or path in roots:
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
