from __future__ import annotations

import secrets


def new_test_run_id() -> str:
    """Return the canonical identifier for a newly created Test Run."""
    return f"testrun_{secrets.token_hex(8)}"
