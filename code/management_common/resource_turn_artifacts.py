from __future__ import annotations
from management_common.format_tags import schema_matches

import difflib
import hashlib
import itertools
import json


MAX_DIFF_CHARS = 262144


def segment_id(execution_id: str, actor: str) -> str:
    identity = hashlib.sha256(f"resource-changes:{execution_id}:{actor}".encode()).hexdigest()[:24]
    return "segment_" + identity


def content_part(changes: list[dict], *, execution_id: str, actor: str) -> dict:
    """Create a stable review snapshot from exact writes, never the current tree."""
    diff = ""
    summaries = []
    truncated = False
    for change in changes:
        if (not schema_matches(change.get("schema"), "tfi.resource-content-change.v1")
                or change.get("execution_id") != execution_id or change.get("actor") != actor):
            raise ValueError("Resource change must belong to this execution and actor")
        before, after = change["before"], change["after"]
        summaries.append({key: change[key] for key in ("change_id", "resource_id", "address", "path", "created_at")})
        summaries[-1].update(before_version=before["version"], after_version=after["version"],
                             before_sha256=before["sha256"], after_sha256=after["sha256"])
        if truncated:
            continue
        # A write has its own label: later writes to the same resource stay separate.
        label = f"{change['address']}@{change['change_id']}"
        old_path, new_path = json.dumps("a/" + label), json.dumps("b/" + label)
        chunks = itertools.chain([f"diff --git {old_path} {new_path}\n"], difflib.unified_diff(
            before["text"].splitlines(keepends=True), after["text"].splitlines(keepends=True),
            fromfile=old_path, tofile=new_path,
        ))
        for chunk in chunks:
            if not chunk.endswith("\n"):
                chunk += "\n\\ No newline at end of file\n"
            remaining = max(0, MAX_DIFF_CHARS - len(diff))
            diff += chunk[:remaining]
            truncated = truncated or len(chunk) > remaining
            if truncated:
                break
    return {"type": "resource_changes", "execution_id": execution_id,
            "segment_id": segment_id(execution_id, actor), "changes": summaries,
            "diff": diff, "diff_truncated": truncated}
