"""Committed authorization changes, for tests that check nothing was written.

Authorization has no policy version; the audit log records every committed
change, so its length stands in where tests compared versions.
"""
from __future__ import annotations


def committed_changes(engine) -> int:
    with engine.store.transaction() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM auth_audit").fetchone()[0])
