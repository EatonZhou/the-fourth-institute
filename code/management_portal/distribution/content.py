"""Registered content locations shared by release preview and export."""
from pathlib import Path

from .layout import physical


def _registered(path, *, exists):
    """A registered location is an area reference or an absolute path of this host."""
    if not path:
        return None
    resolved = physical(path)
    return resolved if resolved.is_absolute() and exists(resolved) else None


def registered_content_path(record):
    """Resolve owner metadata without guessing a checkout or following links."""
    path = record.get("source", {}).get("path")
    if record.get("resource_type") == "repo":
        path = record.get("attributes", {}).get("canonical_checkout") or path
        resolved = _registered(path, exists=Path.is_dir)
        if resolved is None:
            raise ValueError("Selected repository has no available canonical checkout")
        return resolved
    elif record.get("resource_type") == "article":
        path = record.get("attributes", {}).get("canonical_root") or path
        resolved = _registered(path, exists=Path.is_dir)
        if resolved is None:
            raise ValueError("Selected article has no available canonical root")
        return resolved
    elif record.get("resource_type") == "skill":
        attributes = record.get("attributes", {})
        path = attributes.get("content_root") or path
        resolved = _registered(path, exists=Path.exists)
        if resolved is None:
            raise ValueError("Selected Skill has no available registered content root")
        return resolved
    return physical(path) if path else None
