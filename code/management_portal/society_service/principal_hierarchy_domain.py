"""SocietyService owner of Project and personal principals and their navigation facts.

A Lineage's organisation level is set where it is created (CreateProject, the
personal Ask Me Anything); nothing reassigns it afterwards.  A user's personal
assistant is a Family set up on their personal Ask Me Anything.
"""
from __future__ import annotations

from management_common.principal import is_user_or_resident

import hashlib
from typing import Any

from . import personal_assistant, principal_hierarchy as contract
from . import lineage_aggregate


def ensure_project(project_id: str, actor: str) -> dict[str, Any]:
    from management_portal.shared_state import exclusive_file_lock
    from . import resident_domain_common, resident_registration_domain, resource_client

    if not is_user_or_resident(actor):
        raise PermissionError("Project management permission is required")
    # Registration prepares folders outside state scopes. This lock serializes
    # the whole ensure operation without entering a graph mutation scope.
    lock_name = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    with exclusive_file_lock(lineage_aggregate.DATA_ROOT / "locks" / "project-principals" / f"{lock_name}.lock"):
        for graph in lineage_aggregate.lineage_catalog_store().listing_records():
            if graph.get(contract.FIELD) == {"level": "project", "scope_id": project_id}:
                resident_registration_domain.confirm_resident_registration(graph["leader_resident_id"], actor)
                return {"resident_id": graph["leader_resident_id"], "lineage_id": graph["lineage_id"], "created": False}
        project = next((row for row in resource_client._query_all("project", actor=actor) if row["resource_id"] == project_id), None)
        if project is None:
            raise KeyError("Project not found")
        record, graph = resident_registration_domain.register_resident_and_lineage({
            "lineage_id": resident_domain_common.new_lineage_id(), "project_id": project_id,
            "title": str(project.get("title") or project_id),
            "workspace_paths": [str((project.get("attributes") or {}).get("path") or "")],
            "mode": "new_session",
        }, actor, project_principal=True)
        return {"resident_id": record["resident_id"], "lineage_id": record["lineage_id"], "created": True}


PERSONAL_AMA_TITLE = "Ask Me Anything"


def ensure_personal_ama(username: str, actor: str) -> dict[str, Any]:
    """The User's Ask Me Anything Lineage and leader, created on first call.

    Each User has one personal Lineage (decision_bd5217d08c09).  Only the User
    creates it: Portal calls this in the User's name when it creates the
    account, and an administrator's backfill does the same.
    """
    from management_common.principal import actor_principal
    from management_portal.shared_state import exclusive_file_lock
    from . import resident_domain_common, resident_registration_domain, resident_catalog

    owner = actor_principal(username)
    if owner.kind != "user":
        raise ValueError("Ask Me Anything belongs to a User")
    if actor_principal(actor).id.casefold() != owner.id.casefold():
        raise PermissionError("Ask Me Anything 只能以用户本人身份创建")
    lock_name = hashlib.sha256(owner.id.casefold().encode("utf-8")).hexdigest()
    with exclusive_file_lock(lineage_aggregate.DATA_ROOT / "locks" / "personal-ama" / f"{lock_name}.lock"):
        personal = [g for g in lineage_aggregate.lineage_catalog_store().listing_records() if g.get("lineage_membership") == "personal"]
        for graph in personal:
            if str(graph.get("created_by") or "").casefold() == owner.id.casefold():
                _finish_personal_ama(graph, owner.id)
                leader = resident_catalog.get_catalog().get(graph["leader_resident_id"])
                ama_id = (leader.get("family") or {}).get("personal_ama_id") or graph["leader_resident_id"]
                return {"resident_id": ama_id, "lineage_id": graph["lineage_id"], "created": False}
        record, _ = resident_registration_domain.register_resident_and_lineage({
            "lineage_id": resident_domain_common.new_lineage_id(), "title": PERSONAL_AMA_TITLE,
            "mode": "new_session",
        }, owner.id, personal_owner=owner.id)
        return {"resident_id": record["resident_id"], "lineage_id": record["lineage_id"], "created": True}


def delete_personal_ama(username: str, actor: str) -> dict[str, Any]:
    """A deleted User's Ask Me Anything goes with the account.

    Nobody but its owner may open a personal Lineage, so once the account is
    gone it would stay unreachable.  Only an account that is already deleted
    loses it; Portal deletes it in its own name, past the owner-only rule.
    """
    from management_common.principal import actor_principal
    from management_portal.portal_access_control import get_portal_access_store
    from management_portal.shared_state import exclusive_file_lock
    from . import lineage_lifecycle

    owner = actor_principal(username)
    if owner.kind != "user":
        raise ValueError("Ask Me Anything belongs to a User")
    if get_portal_access_store().user(owner.id)["status"] != "deleted":
        raise PermissionError("只有已删除用户的 Ask Me Anything 随账号删除")
    lock_name = hashlib.sha256(owner.id.casefold().encode("utf-8")).hexdigest()
    deleted = []
    with exclusive_file_lock(lineage_aggregate.DATA_ROOT / "locks" / "personal-ama" / f"{lock_name}.lock"):
        for graph in lineage_aggregate.lineage_catalog_store().listing_records():
            if graph.get("lineage_membership") == "personal" and str(graph.get("created_by") or "").casefold() == owner.id.casefold():
                lineage_lifecycle.delete_lineage(graph["lineage_id"], "portal")
                deleted.append(graph["lineage_id"])
    return {"username": owner.id, "deleted_lineage_ids": deleted}


def delete_orphaned_personal_amas() -> dict[str, Any]:
    """Finish the AMA deletions that deleted accounts left behind.

    Deleting an account also deletes its AMA, but that step refuses while an
    AMA Resident still has work queued, and the account is deleted anyway.
    Run periodically, this deletes each such AMA once its work has ended.
    """
    from management_portal.portal_access_control import get_portal_access_store

    owners = {
        str(graph.get("created_by") or "").strip()
        for graph in lineage_aggregate.lineage_catalog_store().listing_records()
        if graph.get("lineage_membership") == "personal"
    }
    store = get_portal_access_store()
    deleted: list[str] = []
    failed: dict[str, str] = {}
    for owner in sorted(owner for owner in owners if owner):
        if store.user(owner)["status"] != "deleted":
            continue
        try:
            deleted.extend(delete_personal_ama(owner, "portal")["deleted_lineage_ids"])
        except Exception as exc:  # noqa: BLE001 - another owner's AMA must not wait on this one
            failed[owner] = str(exc) or type(exc).__name__
    return {"deleted_lineage_ids": deleted, "failed": failed}


def _finish_personal_ama(graph: dict[str, Any], owner_id: str) -> None:
    """Register what a creation stopped before: asking again completes it.

    The creation is finished once its leader is registered.  Until then the
    Lineage is published again from its committed state, as its creation
    would. A partial registration may have published descriptor metadata;
    retries use the canonical Lineage.
    """
    from . import lineage_state, resident_catalog, resident_registration_domain

    lineage_id = graph["lineage_id"]
    leader = str(graph.get("leader_resident_id") or "")
    if leader and resident_catalog.get_catalog().get(leader).get("registration_state") == "ready":
        resident_catalog.ensure_resident_agent(resident_catalog.get_catalog().get(leader), actor=owner_id)
        return
    lineage_state._publish_lineage_resource_projection(
        lineage_id, None, creation=lineage_aggregate.read_lineage_state(lineage_id),
    )
    if leader:
        resident_registration_domain.confirm_resident_registration(leader, owner_id)


def navigation(actor: str) -> dict[str, Any]:
    from . import resource_client
    from .lineage_access_policy import changeable_ids

    if not is_user_or_resident(actor):
        return {"schema": contract.SCHEMA, "projects": [], "user_residents": [], "levels": list(contract.LEVELS)}
    definitions = lineage_aggregate.lineage_catalog_store().listing_records()
    owners = {row["lineage_id"]: row.get("created_by", "") if row.get("lineage_membership") == "personal" else ""
              for row in definitions}
    readable = changeable_ids(actor, owners, "GetLineage", "lineage_id")
    definitions = [row for row in definitions if row["lineage_id"] in readable]
    projects = []
    for project in resource_client._query_all("project", actor=actor):
        identifier = project["resource_id"]
        project_graphs = [row for row in definitions if row.get("project_id") == identifier and (row.get(contract.FIELD) or {}).get("level") == "project"]
        graph = project_graphs[0] if project_graphs else {}
        children = []
        for row in definitions:
            if row.get("project_id") != identifier or row is graph or row.get("lineage_membership") == "personal" or (row.get(contract.FIELD) or {}).get("level") in {"project", "user"}:
                continue
            children.append({"lineage_id": row["lineage_id"], "title": row["title"], "resident_id": row.get("leader_resident_id", "")})
        accessible = bool(graph)
        projects.append({"project_id": identifier, "title": project.get("title") or identifier,
                         "accessible": accessible,
                         "can_initialize": not graph,
                         "resident_id": graph.get("leader_resident_id", "") if accessible else "",
                         "lineage_id": graph.get("lineage_id", "") if accessible else "", "lineages": children})
    # Lineages the actor can read in a Project it cannot read.  The Paper
    # Lineage's owner could not find it in the sidebar because the
    # whole Project was skipped; the Project is shown by its ID, locked, with
    # only those Lineages.
    listed = {project["project_id"] for project in projects}
    unlisted: dict[str, list[dict[str, Any]]] = {}
    for row in definitions:
        project_id = str(row.get("project_id") or "")
        if (
            not project_id or project_id in listed or row.get("lineage_membership") == "personal"
            or (row.get(contract.FIELD) or {}).get("level") in {"project", "user"}
        ):
            continue
        unlisted.setdefault(project_id, []).append(
            {"lineage_id": row["lineage_id"], "title": row["title"], "resident_id": row.get("leader_resident_id", "")}
        )
    for project_id, children in sorted(unlisted.items()):
        projects.append({"project_id": project_id, "title": project_id, "accessible": False, "project_readable": False,
                         "can_initialize": False, "resident_id": "", "lineage_id": "", "lineages": children})
    return {"schema": contract.SCHEMA, "projects": projects, "user_residents": [],
            "levels": list(contract.LEVELS)}


def ensure_personal_assistant(username: str, ama_id: str, actor: str) -> dict[str, Any]:
    """Resume one user's Family setup without touching their AMA session or history."""
    from management_common.principal import actor_principal
    from management_portal.shared_state import exclusive_file_lock
    from . import lineage_state, lineage_graph_edits, resident_catalog, lineage_graph_state
    from .templates.familize import familize

    owner = actor_principal(username)
    if owner.kind != "user" or actor_principal(actor) != owner:
        raise PermissionError("个人助手只能以用户本人身份初始化")
    lock = hashlib.sha256(owner.id.casefold().encode()).hexdigest()
    with exclusive_file_lock(lineage_aggregate.DATA_ROOT / "locks" / "personal-assistant" / f"{lock}.lock"):
        ama = resident_catalog.get_catalog().get(ama_id)
        lineage_id = ama["lineage_id"]
        graph = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        if (graph.get("lineage_membership") != "personal"
                or str(graph.get("created_by") or "").casefold() != owner.id.casefold()
                or ama.get("deleted_at")):
            raise ValueError("个人 AMA 入口必须指向本人的私人 Lineage 中的有效成员")
        blocks = [b for b in graph.get("function_blocks", []) if (b.get("family") or {}).get("personal_ama_id")]
        if blocks and blocks[0]["family"]["personal_ama_id"] != ama_id:
            raise ValueError("个人助手已绑定另一 AMA；请明确调整成员，初始化不替换绑定")
        if graph["title"] == "Ask Me Anything":
            def rename(body):
                if body["title"] == "Ask Me Anything":
                    body["title"] = personal_assistant.TITLE
                return {}
            graph, _ = lineage_graph_edits.commit_lineage_edit(lineage_id, actor, rename, reason="个人助手默认名称")
        if not blocks:
            result = familize(lineage_id, [ama_id], actor, title=graph["title"], personal_ama_id=ama_id)
            graph = lineage_aggregate.read_lineage(lineage_id, actor=actor)
            block = next(b for b in graph["function_blocks"] if b["id"] == result["family_id"])
        else:
            block = blocks[0]
        family = block["family"]
        # Publication can fail after the graph committed. Retry it,
        # keeping the original roles and migration boundary.
        lineage_state._publish_lineage_resource_projection(lineage_id, None, actor=actor)
        key = personal_assistant.preferences_key(family["facade"])
        lineage_graph_state.update_cell(lineage_id, key, {"preferences": [], "my_teams": []}, expected_value=None)
        return {"resident_id": family["facade"], "lineage_id": lineage_id, "family_id": family["resource_id"]}
