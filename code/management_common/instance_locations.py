"""How registered directories are stored (protocol「路径的两条规则」).

A directory inside one of this instance's areas is stored as
``area:relative/path``; any other directory as its canonical absolute path.
Directories of another instance on this host are never registered. Reading
resolves an area reference against the current manifest, so moving an area
changes no stored value.
"""
from __future__ import annotations

from pathlib import Path

from .instance_manifest import AREAS, current_instance


def is_area_reference(value: object) -> bool:
    area, separator, _ = str(value or "").partition(":")
    return bool(separator) and area in AREAS


def stored_location(path: str | Path) -> str:
    """The storage form of a directory being registered."""
    from .host_instances import registered_reference
    return registered_reference(current_instance(), Path(path).expanduser().resolve(strict=False))


def stored_lookup_forms(paths) -> list[str]:
    """Stored forms to look registrations up by; paths that cannot be registered are skipped."""
    instance = current_instance()
    forms = []
    for path in paths:
        try:
            forms.append(instance.reference(Path(path).resolve(strict=False)))
        except ValueError:
            continue
    return list(dict.fromkeys(forms))


def located_path(value: str) -> str:
    """Materialize an absolute path with the registration contract's separators."""
    text = str(value or "")
    if is_area_reference(text):
        return current_instance().resolve(text).as_posix()
    return Path(text).as_posix()
