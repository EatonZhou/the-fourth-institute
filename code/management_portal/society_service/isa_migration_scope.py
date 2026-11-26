"""Explicitly deferred D066 ownership; never infer migration scope from ACL errors.

The source decision defers this existing Lineage without reading or changing it.
Keep this evidence until its authorized owner explicitly resumes the migration.
This is migration selection, not an instruction alias or a runtime permission.
"""

_LINEAGE_ID = "lineage_7983b047d221"


def is_deferred(lineage_id: str) -> bool:
    return lineage_id == _LINEAGE_ID


def deferred_report() -> list[dict[str, str]]:
    return [{
        "kind": "deferred_lineage",
        "lineage_id": _LINEAGE_ID,
        "trigger_path_id": "trigger_path_577a645d5189",
        "reason": "explicitly_deferred_without_access",
        "source_message_id": "msg_ed8c7e62384080bc8c4dceaa1f5f9b41",
    }]
