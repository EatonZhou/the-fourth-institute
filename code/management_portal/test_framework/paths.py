from __future__ import annotations

import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def instance_name() -> str:
    return re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        os.getenv("PORTAL_INSTANCE", "dev"),
    ) or "dev"


def user_state_root() -> Path:
    """Return the canonical per-user runtime state directory."""
    return Path.home() / ".local" / "state" / "tfi-portal"


def state_root() -> Path:
    return Path(
        os.getenv(
            "PORTAL_TEST_FRAMEWORK_STATE_ROOT",
            str(user_state_root() / "test-framework" / instance_name()),
        )
    ).expanduser()
