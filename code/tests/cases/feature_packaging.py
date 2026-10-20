"""Probes of a Portal package that leaves features out (decision_15aab18c0ab3).

A package without a feature has neither the feature's modules
(``PortalFeature.modules``) nor the resource type declarations whose provider
is one of them, so the feature's unit is not deployed.  A probe that blocks
the modules in a fresh interpreter calls ``drop_resource_types`` with the same
patterns before anything reads the resource type registry.
"""
from __future__ import annotations

import atexit
import fnmatch
import json
import os
import shutil
import tempfile
from pathlib import Path

RESOURCE_TYPES = Path(__file__).resolve().parents[2] / "config" / "resource-types"


def drop_resource_types(patterns) -> None:
    """Point the registry at a copy of ``config/resource-types`` without the types a left-out module provides."""
    copy = Path(tempfile.mkdtemp(prefix="portal-resource-types-"))
    atexit.register(shutil.rmtree, copy, True)
    for source in RESOURCE_TYPES.glob("*.json"):
        provider = str(json.loads(source.read_text()).get("provider") or "").partition(":")[0]
        if not (provider and any(fnmatch.fnmatchcase(provider, pattern) for pattern in patterns)):
            shutil.copy(source, copy / source.name)
    os.environ["PORTAL_RESOURCE_TYPES_DIR"] = str(copy)
