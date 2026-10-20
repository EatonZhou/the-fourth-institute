from __future__ import annotations

import json
from pathlib import Path

from management_common.product_environment import read_product_env


def execution_context() -> dict:
    """The execution a Resident's command-line call runs in, from the file its turn wrote.

    Only the execution ID travels: a Resident's calls are checked with its own
    permissions (decision_b820faf42218 §2).
    """
    value = read_product_env("PORTAL_EXECUTION_CONTEXT_FILE", "").strip()
    if not value:
        return {}
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Execution context file path must be absolute")
    with path.open(encoding="utf-8") as stream:
        content = stream.read(4097)
    if len(content) > 4096:
        raise ValueError("Execution context file is too large")
    envelope = json.loads(content)
    if not isinstance(envelope, dict) or set(envelope) != {"execution_id"}:
        raise ValueError("Invalid execution context file")
    return envelope
