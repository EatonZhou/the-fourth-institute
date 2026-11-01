"""Instance directories: a projection of the single validated manifest."""
from __future__ import annotations

from pathlib import Path
from .instance_manifest import AREAS, FIXED_DIRECTORIES, load

KEYS = (*AREAS, *FIXED_DIRECTORIES)


def resolve_directory(value: str) -> Path:
    instance = load()
    if isinstance(value, str) and value.partition(':')[0] in AREAS:
        return instance.resolve(value)
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('Directory must be absolute or an area reference')
    return instance.resolve(str(Path(value).resolve()))


def path(key: str) -> Path:
    instance = load()
    if key in AREAS:
        return instance.area(key)
    if key in FIXED_DIRECTORIES:
        return instance.directory(key)
    raise KeyError(key)


def paths() -> dict[str, Path]:
    instance = load()
    return {**{key: instance.area(key) for key in AREAS},
            **{key: instance.directory(key) for key in FIXED_DIRECTORIES}}


def path_values() -> dict[str, str]:
    return {key: str(value) for key, value in paths().items()}
