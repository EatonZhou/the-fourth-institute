"""Society-owned direct-Family review membership, with no directory fallback."""
from __future__ import annotations

import hashlib

from management_portal import permission_system
from . import family_familize as facts, lineage_aggregate, resident_access, resident_record_domain

KINDS = {"page": "解释页独立检查", "video": "讲解视频独立检查"}


def _role_text(kind):
    return (f"# {KINDS[kind]}\n\n你是本直属Family的独立{kind}业务审核者。\n"
            "读取委托固定包的正式地址、清单、SHA与实际来源；export导出固定成果后实际打开并操作交互，"
            "视频必须播放实际成片并核验画面、语音、字幕与时序，不能以构建成功或静态文件存在代替验收。\n"
            "保存可追溯的固定版本证据与具体问题，按正式送审入口返回passed或changes_requested；"
            "后者应写清可落实的修改条件，修订沿原审查关联重新核验。缺访问权或证据须如实blocked，"
            "不可自行签过、冒充试听或审自己的作者成果；页面检查不可替代视频检查。\n")


def instructions(record):
    family = record.get("family") or {}
    if family.get("role") != "member":
        return ""
    from .state_store import get_lineage_data_store
    store = get_lineage_data_store()
    for kind, title in KINDS.items():
        receipt = store.resident_creation_operations.get(record["lineage_id"], _key(family["family_id"], kind))
        if receipt.get("resident_id") == record.get("resident_id"):
            return _role_text(kind)
    return ""


def _permit(actor, instruction, **arguments):
    if not permission_system.allows(actor, instruction, arguments):
        raise PermissionError(f"{instruction} permission is required")


def _family(lineage, resident_id):
    blocks = facts.family_blocks(lineage)
    # A nested facade belongs to its own Family for this responsibility.
    return next((b for b in blocks if facts.role_of(b, "facade") == resident_id), None) or next(
        (b for b in blocks if resident_id in facts.member_ids(b)), None)


def _key(family_id, kind):
    return f"family-quality-reviewer:{family_id}:{kind}"


def _validate(lineage, block, reviewer, responsible, actor):
    if reviewer == responsible or reviewer not in facts.member_ids(block):
        raise ValueError("Reviewer must be an independent direct Family business member")
    record = resident_record_domain.find_resident(reviewer)
    if not record or record.get("deleted_at") or record.get("lineage_id") != lineage["lineage_id"]:
        raise ValueError("Reviewer is not a live member of the responsible Family")
    _permit(actor, "DeliverMessage", resident_id=reviewer)
    if not resident_access.can_manage_resident(record, actor, operation="read"):
        raise PermissionError("Reviewer access is required")


def ensure(resident_id, actor, *, kind, reviewer_id=""):
    """Mutating request entry; reading settings/preview never invokes this owner.

    Reuses existing CreateResident/Familize/UpdateFamily permissions, including
    personal-owner protection. Only the two fixed business roles are provisioned;
    a quality tier or message text does not invoke this mutation by itself.
    """
    if kind not in KINDS:
        raise ValueError("Review kind must be page or video")
    record = resident_record_domain.find_resident(resident_id)
    if not record or not resident_access.can_manage_resident(record, actor, operation="read"):
        raise PermissionError("Responsible Resident access is required")
    lineage_id = record["lineage_id"]
    store = lineage_aggregate.lineage_catalog_store().state_store
    # A session advisory mutex covers slow lifecycle work without an outer DDL
    # or resident transaction. Each lifecycle owner commits its own facts.
    key = int.from_bytes(hashlib.sha256(f"{store.database.schema}:quality-reviewers:{lineage_id}".encode()).digest()[:8], "big", signed=True)
    with store.database.advisory_lock(key):
        lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        block = _family(lineage, resident_id)
        if reviewer_id:
            if not block:
                raise ValueError("An explicit reviewer must already belong to the responsible Family")
            _validate(lineage, block, reviewer_id, resident_id, actor)
        if not block:
            _permit(actor, "Familize", lineage_id=lineage_id)
            _permit(actor, "CreateResident", lineage_id=lineage_id)
            _permit(actor, "UpdateFamily", lineage_id=lineage_id, family_id="")
            from .templates import create_family
            result = create_family(lineage_id, [resident_id], actor)
            lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
            block = facts.family_block(lineage, result["family_id"])
        family_id = block["id"]
        reviewers = {}
        for role in KINDS:
            receipt = store.resident_creation_operations.get(lineage_id, _key(family_id, role))
            rid = str(receipt.get("resident_id") or "")
            if not rid:
                candidates = [member for member in facts.member_ids(block)
                              if (resident_record_domain.find_resident(member) or {}).get("title") == KINDS[role]]
                if len(candidates) > 1:
                    raise ValueError(f"Ambiguous direct Family {role} reviewer responsibility")
                rid = candidates[0] if candidates else ""
            if rid:
                _validate(lineage, block, rid, resident_id, actor)
            reviewers[role] = rid
        if reviewer_id and reviewer_id != reviewers[kind]:
            raise ValueError("Explicit reviewer must match the Family's corresponding review responsibility")
        missing = [role for role, rid in reviewers.items() if not rid]
        if missing:
            _permit(actor, "CreateResident", lineage_id=lineage_id)
            _permit(actor, "UpdateFamily", lineage_id=lineage_id, family_id=family_id)
        from . import lineage_resident_nodes
        from .templates import familize
        for role in KINDS:
            if not reviewers[role]:
                result = lineage_resident_nodes.create_lineage_resident(lineage_id, {
                    "title": KINDS[role], "creation_key": _key(family_id, role),
                    "initial_instructions": _role_text(role),
                }, actor)
                reviewers[role] = result["resident"]["resident_id"]
            fresh = lineage_aggregate.read_lineage(lineage_id, actor=actor)
            current = facts.family_block(fresh, family_id)
            rid = reviewers[role]
            other = _family(fresh, rid)
            if other and other["id"] != family_id:
                raise ValueError("Registered reviewer belongs to another Family")
            if rid not in facts.member_ids(current):
                _permit(actor, "UpdateFamily", lineage_id=lineage_id, family_id=family_id)
                familize.join_new_resident(lineage_id, rid, actor, family_id)
            fresh = lineage_aggregate.read_lineage(lineage_id, actor=actor)
            _validate(fresh, facts.family_block(fresh, family_id), rid, resident_id, actor)
        return {"family_id": family_id, "lineage_id": lineage_id,
                "reviewer_id": reviewer_id or reviewers[kind], "reviewers": reviewers}
