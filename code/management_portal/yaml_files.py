"""Parsed YAML configuration files, parsed again only when their content changes.

Several request paths read the same small registry files (the LLM API registry,
the Codex account registry) many times per request; parsing them with the
pure-Python YAML loader dominated batch Resident state reads. Reading the text
stays cheap, so the cache compares content rather than trusting timestamps.
"""
from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

import yaml

_CACHE: dict[str, tuple[str, Any]] = {}
_LOCK = threading.Lock()


def load_yaml_file(path: Path | str) -> Any:
    """Return the parsed document of a YAML file, as a copy the caller may change.

    Raises the same OSError, UnicodeError and yaml.YAMLError as
    ``yaml.safe_load(path.read_text(encoding="utf-8"))``.
    """
    target = Path(path)
    content = target.read_text(encoding="utf-8")
    key = str(target)
    with _LOCK:
        cached = _CACHE.get(key)
    if cached is not None and cached[0] == content:
        return copy.deepcopy(cached[1])
    payload = yaml.safe_load(content)
    with _LOCK:
        _CACHE[key] = (content, payload)
    return copy.deepcopy(payload)
