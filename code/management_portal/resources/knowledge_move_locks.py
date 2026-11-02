"""Stable Knowledge identity lock shared by edits, deletion and relocation."""
from contextlib import contextmanager

from management_common.instance_manifest import current_instance
from .locking import resource_mutation_lock


@contextmanager
def knowledge_identity_lock(resource_id):
    # A Project/path may change while an editor waits. Identity does not.
    root = current_instance().root / "runtime" / "knowledge-moves"
    with resource_mutation_lock(root, resource_id):
        yield
