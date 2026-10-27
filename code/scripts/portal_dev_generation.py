#!/usr/bin/env python3
"""Read the build IDs recorded for the Dev instance's components."""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_common.instance_paths import instance_root

DEV_INSTANCE_ROOT = instance_root() / 'dev'
DEV_GENERATION_ENV = DEV_INSTANCE_ROOT / "source-generation.env"
DEV_COMPONENT_GENERATIONS = DEV_INSTANCE_ROOT / "component-generations.json"
COMPONENTS = (
    "web",
    "resource_service",
    "execution_service",
    "execution_worker",
    "society_service",
)


def read_build_id(path: Path = DEV_GENERATION_ENV) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() == "PORTAL_BUILD_ID":
            return value.strip()
    return ""


def read_component_build_ids(
    path: Path = DEV_COMPONENT_GENERATIONS,
) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    values = payload.get("components") if isinstance(payload, dict) else {}
    if not isinstance(values, dict):
        return {}
    return {
        str(component): str(build_id)
        for component, build_id in values.items()
        if str(component) in COMPONENTS and str(build_id)
    }
