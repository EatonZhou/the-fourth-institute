"""Registered content locations shared by release preview and export."""
from pathlib import Path


def registered_content_path(record):
    """Resolve owner metadata without guessing a checkout or following links."""
    path = record.get("source", {}).get("path")
    if record.get("resource_type") == "repo":
        path = record.get("attributes", {}).get("canonical_checkout") or path
        if not path or not Path(path).is_absolute() or not Path(path).is_dir():
            raise ValueError("Selected repository has no available canonical checkout")
    elif record.get("resource_type") == "article":
        path = record.get("attributes", {}).get("canonical_root") or path
        if not path or not Path(path).is_absolute() or not Path(path).is_dir():
            raise ValueError("Selected article has no available canonical root")
    elif record.get("resource_type") == "skill":
        attributes = record.get("attributes", {})
        path = attributes.get("content_root") or path
        if not path or not Path(path).is_absolute() or not Path(path).exists():
            raise ValueError("Selected Skill has no available registered content root")
    return Path(path) if path else None
