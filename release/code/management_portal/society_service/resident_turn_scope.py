"""The paths a Resident turn works in and the authorization context it is told about."""
from __future__ import annotations

from management_portal import permission_system


def context_prompt(scope: dict, job: dict, resident_folder: str) -> str:
    rows = [f"本轮执行者：{scope['principal_id']}；消息来源：{scope['source_principal_id']}。"]
    rows.append("工作目录参考：")
    rows.extend(f"- {path}" for path in dict.fromkeys(item["path"] for item in scope["paths"]))
    rows.append(permission_system.prompt_rule())
    return "<portal-authorization-context>\n" + "\n".join(rows) + "\n</portal-authorization-context>"


def filesystem_scope(record: dict, job: dict, *, cwd: str, resident_folder: str, bindings: list, projects: dict | None = None) -> dict:
    """Where the turn works.  The turn acts with the Resident's own permissions; the message's
    source is only reported to it (decision_b820faf42218 §2)."""
    resident_id = str(record.get("resident_id") or "")
    paths = [{"path": resident_folder, "access": "write", "source": "resident_folder"}, {"path": cwd, "access": "write", "source": "resident_workspace"}]
    paths.extend({"path": str(item["path"]), "access": item.get("access", "write"), "source": "workspace_binding"} for item in bindings if item.get("path"))
    return {"cwd": cwd, "paths": paths, "read_scope": "host", "approval_policy": "never", "sandbox_policy": {"type": "dangerFullAccess"},
            "principal_id": resident_id, "source_principal_id": str(job.get("actor") or "")}
