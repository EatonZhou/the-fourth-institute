"""Authorized semantic references at the projection boundary."""
from __future__ import annotations

from datetime import datetime
import re

from management_portal.resources import get_resource_service
from .store import ProjectionError
from management_common.clock import utc_now_iso


def observe(refs, actor):
    if not isinstance(refs, list) or len(refs) > 128 or any(not isinstance(ref, str) or not ref or len(ref) > 2048 for ref in refs):
        raise ProjectionError("projection.references", "请提供最多 128 个有效对象地址")
    service = get_resource_service()
    observed = []
    for ref in dict.fromkeys(refs):
        address = re.sub(r"#L[1-9][0-9]*(?:-L[1-9][0-9]*)?$", "", ref)
        resolved = service.resolve(address, actor=actor)
        if resolved.get("exists") is False:
            raise ProjectionError("projection.reference_missing", "引用对象已不存在", 404)
        observed.append({"ref": ref, "root_id": resolved["root_id"], "observed_at": utc_now_iso()})
    return {"references": observed, "observed_at": utc_now_iso()}


def semantic_object(value, actor):
    if not isinstance(value, dict) or set(value) != {"ref", "kind", "status", "source", "observed_at"}:
        raise ProjectionError("projection.object", "对象需要 ref、kind、status、source、observed_at")
    if value["status"] not in {"current", "proposed", "simulated", "verified"}:
        raise ProjectionError("projection.object", "对象状态无效")
    if not isinstance(value["kind"], str) or not 0 < len(value["kind"]) <= 120:
        raise ProjectionError("projection.object", "对象种类无效")
    if not isinstance(value["source"], str) or len(value["source"]) > 2048:
        raise ProjectionError("projection.object", "依据须为地址或空字符串")
    if value["status"] == "verified" and not value["source"]:
        raise ProjectionError("projection.evidence_missing", "已验证结果必须提供可读取的依据")
    try:
        stamp = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ProjectionError("projection.object", "观察时间需要带时区的 ISO 时间") from None
    observe([value["ref"], *([value["source"]] if value["source"] else [])], actor)
    return dict(value)
