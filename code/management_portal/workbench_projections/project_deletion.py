"""Enlist installed Workbench owner rows in the authorized Project deletion."""
from __future__ import annotations


def prepare():
    # Schema preparation precedes the coordinator transaction. Deletion uses
    # that transaction, the original actor and the owner's current rows.
    from .store import Store
    return Store().delete_project
