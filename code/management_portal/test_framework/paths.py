from __future__ import annotations

import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def instance_name() -> str:
    return re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        os.getenv("PORTAL_DEPLOYMENT", "dev"),
    ) or "dev"


def state_root() -> Path:
    """Run records, targets and artifacts of the selected instance's test framework."""
    from management_common.instance_manifest import instance_runtime_root

    return instance_runtime_root() / "test-framework" / instance_name()


def slot_root() -> Path:
    """Concurrency slots of the selected instance's test executors."""
    from management_common.instance_manifest import instance_runtime_root

    return instance_runtime_root() / "test-slots"
