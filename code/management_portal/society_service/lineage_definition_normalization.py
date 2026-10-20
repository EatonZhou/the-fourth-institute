"""Normalize Lineage graph definitions and authored metadata.

Graph values are independent of persistence, permissions and runtime dispatch.
Callers supply the clock used by the Lineage operation.
"""
from __future__ import annotations


import secrets
from typing import Any, Callable
from dataclasses import dataclass
from functools import partial

from . import identity_contract, lineage_handoff_backend, lineage_program
from .lineage_values import _dedupe_strings, _integer, _item_id, _safe_actor, _text




@dataclass(frozen=True)
class DefinitionPorts:
    clock: Callable[[], str]
    resident_targets: Callable[[list[str]], dict[str, dict[str, str]]]
    position: Callable[[object, float], float]
    requested_lineage_project_id: Callable[..., str]
    clone_json: Callable[[dict[str, Any]], dict[str, Any]]
    schema_version: int
    lineage_folder_schema: str

NODE_STATES = {"idle", "queued", "running", "blocked", "done"}

LINEAGE_MEMBERSHIP_VALUES = {"formal", "personal"}

MAX_FRAGMENT_CHARS = 4000

MAX_FRAGMENT_IMAGES = 24

EDGE_TYPES = {"inheritance", "absorption", "handoff"}

EDGE_FAMILIES = {
    "inheritance": "context", "absorption": "context",
    "handoff": "workspace",
    "candidate_fork": "context",
}

# A candidate fork runs from a Resident to a Seed: the Resident whose work the
# candidate starts from, and that adopting the Seed forks.  It is the only
# relation whose target is a Seed, and a Seed has at most one.  Adopting turns
# it into the inheritance relation to the new Resident (decision_4adcc1ff2f2c).
CANDIDATE_FORK = "candidate_fork"
CANDIDATE_FORK_LABEL = "候选 Fork"

ABSORPTION_STATES = {"preparing", "queued", "failed"}

def _normalize_edge(
    raw: object,
    resident_ids: set[str],
    existing: dict[str, Any] | None = None,
    *,
    _clock: Callable[[], str],
) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    previous = existing if isinstance(existing, dict) else {}
    source = _text(item.get("source"), 80).lower()
    target = _text(item.get("target"), 80).lower()
    if source not in resident_ids or target not in resident_ids:
        raise ValueError("HATG 边引用了不存在的节点")
    if source == target:
        raise ValueError("HATG 不支持节点连接自身")
    edge_type = _text(item.get("type"), 40).lower() or "handoff"
    if edge_type not in EDGE_TYPES:
        raise ValueError("HATG 关系类型无效")
    absorption = item.get("absorption") if isinstance(item.get("absorption"), dict) else {}
    absorption_status = _text(absorption.get("status"), 40).lower() or "queued"
    if absorption_status not in ABSORPTION_STATES:
        absorption_status = "failed"
    now = _clock()
    handoff_fields = (
        lineage_handoff_backend.normalize_handoff_fields(
            item,
            previous if isinstance(previous, dict) else None,
            now=now,
        )
        if edge_type == "handoff"
        else {}
    )
    result = {
        "id": _item_id(item.get("id"), "edge"),
        "source": source,
        "target": target,
        "type": edge_type,
        "family": EDGE_FAMILIES[edge_type],
        "label": _text(item.get("label"), 120) or {
            "inheritance": "轨迹继承",
            "absorption": "节点吸收",
            "handoff": "工作交接",
        }[edge_type],
        "note": _text(item.get("note"), 4000),
        "absorption": {
            "status": absorption_status,
            "source_resident_id": _text(absorption.get("source_resident_id"), 80),
            "target_resident_id": _text(absorption.get("target_resident_id"), 80),
            "run_count": max(1, _integer(absorption.get("run_count"), 1)),
            "target_message_id": _text(absorption.get("target_message_id"), 180),
            "error": _text(absorption.get("error"), 2000),
            "started_at": _text(absorption.get("started_at"), 80) or now,
            "delivered_at": _text(absorption.get("delivered_at"), 80),
        } if edge_type == "absorption" else {},
        **handoff_fields,
    }
    return result

def is_candidate_fork(item: object) -> bool:
    return isinstance(item, dict) and _text(item.get("type"), 40).lower() == CANDIDATE_FORK


def _normalize_candidate_forks(
    raw_edges: list[dict[str, Any]],
    program: dict[str, Any],
    resident_ids: set[str],
) -> list[dict[str, Any]]:
    """The candidate forks of a definition, one per Seed at most.

    A Seed written with ``config.source_resident_id`` (a Function Block
    structure, an older definition) gets that Resident's candidate fork in
    place of any other; the field itself is not kept.  A candidate fork whose
    Seed or Resident no longer exists is dropped with it.
    """

    seeds = {
        str(item.get("id") or ""): item
        for item in program.get("components") or []
        if isinstance(item, dict) and item.get("kind") == "seed"
    }
    by_seed: dict[str, dict[str, Any]] = {}
    for item in raw_edges:
        target = _text(item.get("target"), 80).lower()
        if target in seeds:
            # A later one replaces an earlier one: that is how a source is changed.
            by_seed[target] = item
    for seed_id, seed in seeds.items():
        config = seed.get("config") if isinstance(seed.get("config"), dict) else {}
        declared = str(config.pop("source_resident_id", "") or "")
        if declared and (by_seed.get(seed_id) or {}).get("source") != declared:
            by_seed[seed_id] = {"source": declared, "target": seed_id}
    result: list[dict[str, Any]] = []
    for seed_id, item in by_seed.items():
        source = _text(item.get("source"), 80).lower()
        if source not in resident_ids:
            continue
        result.append({
            "id": _item_id(item.get("id"), "edge_candidate"),
            "source": source,
            "target": seed_id,
            "type": CANDIDATE_FORK,
            "family": EDGE_FAMILIES[CANDIDATE_FORK],
            "label": CANDIDATE_FORK_LABEL,
        })
    return result


def _reconnect_removed_inheritance_nodes(
    previous: dict[str, Any],
    nodes_raw: list[object],
    edges_raw: list[object],
) -> list[object]:
    """Reconnect inheritance paths whose intermediate HATG nodes were removed."""
    previous_nodes = {
        str(item.get("id") or ""): item
        for item in previous.get("nodes") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    current_ids = {
        _text(item.get("id"), 80).lower()
        for item in nodes_raw
        if isinstance(item, dict) and _text(item.get("id"), 80)
    }
    removed_ids = set(previous_nodes) - current_ids
    if not removed_ids:
        return list(edges_raw)

    previous_edges = [
        item for item in previous.get("edges") or []
        if isinstance(item, dict) and str(item.get("type") or "") == "inheritance"
    ]
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in previous_edges:
        outgoing.setdefault(str(edge.get("source") or ""), []).append(edge)
    result = [
        item
        for item in edges_raw
        if not isinstance(item, dict)
        or (
            str(item.get("source") or "") in current_ids
            and str(item.get("target") or "") in current_ids
        )
    ]
    relations = {
        (
            str(item.get("source") or ""),
            str(item.get("target") or ""),
            str(item.get("type") or ""),
        )
        for item in result
        if isinstance(item, dict)
    }

    def add_reconnected(source_id: str, target_id: str, path: list[dict[str, Any]], skipped: list[str]) -> None:
        relation = (source_id, target_id, "inheritance")
        if source_id == target_id or relation in relations:
            return
        target_title = str(previous_nodes.get(target_id, {}).get("title") or target_id)
        result.append({
            "id": f"edge_reconnected_{secrets.token_hex(6)}",
            "source": source_id,
            "target": target_id,
            "type": "inheritance",
            "label": f"继承链重连 · {target_title}",
        })
        relations.add(relation)

    def walk(source_id: str, current_id: str, path: list[dict[str, Any]], skipped: list[str], seen: set[str]) -> None:
        if current_id in seen:
            return
        next_seen = {*seen, current_id}
        for edge in outgoing.get(current_id, []):
            target_id = str(edge.get("target") or "")
            next_path = [*path, edge]
            if target_id in removed_ids:
                walk(source_id, target_id, next_path, [*skipped, target_id], next_seen)
            elif target_id in current_ids:
                add_reconnected(source_id, target_id, next_path, skipped)

    for source_id in current_ids:
        for edge in outgoing.get(source_id, []):
            target_id = str(edge.get("target") or "")
            if target_id in removed_ids:
                walk(source_id, target_id, [edge], [target_id], {source_id})
    return result

def _normalize_fragment_attachment(raw: object) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    content_type = _text(
        item.get("content_type") or item.get("mime"),
        120,
    ).lower()
    kind = _text(item.get("kind"), 24).lower() or (
        "image" if not content_type or content_type.startswith("image/") else "file"
    )
    if kind != "image" or (content_type and not content_type.startswith("image/")):
        raise ValueError("碎片附件仅支持图片")
    file_id = _text(item.get("file_id"), 800)
    path = _text(item.get("path") or item.get("url"), 2400)
    if not file_id and not path:
        raise ValueError("碎片图片缺少已上传的文件引用")
    attachment_id = _text(item.get("id"), 800) or file_id or path
    return {
        "id": attachment_id,
        "file_id": file_id,
        "kind": "image",
        "name": _text(item.get("name") or item.get("filename"), 240) or "pasted image",
        "path": path,
        "preview_url": _text(item.get("preview_url"), 2400),
        "content_type": content_type or "image/png",
        "size": max(0, _integer(item.get("size") or item.get("size_bytes"), 0)),
    }

def _normalize_fragment(
    raw: object,
    existing: dict[str, Any] | None,
    actor: str,
    _clock: Callable[[], str],
) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    previous = existing if isinstance(existing, dict) else {}
    content = _text(item.get("content"), MAX_FRAGMENT_CHARS)
    if not content:
        raise ValueError("碎片内容不能为空")
    attachments_raw = (
        item.get("attachments")
        if isinstance(item.get("attachments"), list)
        else previous.get("attachments") or []
    )
    if len(attachments_raw) > MAX_FRAGMENT_IMAGES:
        raise ValueError(f"每个碎片最多保存 {MAX_FRAGMENT_IMAGES} 张图片")
    attachments = [
        _normalize_fragment_attachment(attachment)
        for attachment in attachments_raw
    ]
    attachment_ids = [attachment["id"] for attachment in attachments]
    if len(set(attachment_ids)) != len(attachment_ids):
        raise ValueError("同一碎片中的图片引用不能重复")
    now = _clock()
    actor_id = _safe_actor(actor)
    return {
        "id": _item_id(item.get("id"), "fragment"),
        "content": content,
        "attachments": attachments,
        "created_by": _text(previous.get("created_by") or item.get("created_by"), 120) or actor_id,
        "created_at": _text(previous.get("created_at") or item.get("created_at"), 80) or now,
        "updated_by": actor_id,
        "updated_at": now,
    }

DEFAULT_LEADER_CHARTER = (
    "持续审视 Lineage 的职责边界、交接质量、验收回路和未决风险。"
    "根据 owner 的明确目标，通过 HATG Instruction Set 幂等修改和操作本 Lineage，"
    "并负责编排节点、交接与执行；必须保留明确要求的人工确认边界。"
)



def _normalize_node(
    raw: object,
    index: int,
    existing: dict[str, Any] | None = None,
    *,
    resident_references: dict[str, dict[str, Any]] | None = None,
    ports: DefinitionPorts,
) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    previous = existing if isinstance(existing, dict) else {}
    resident_id = _text(item.get("id") or previous.get("id"), 80).lower()
    if not identity_contract.is_valid_id(resident_id, "resident"):
        raise ValueError("每个 HATG 节点必须绑定规范的 Resident ID")
    target = (
        (resident_references or {}).get(resident_id)
        or ports.resident_targets([resident_id]).get(resident_id)
        or {}
    )
    resolved_resident_id = _text(target.get("resident_id"), 80).lower()
    if resolved_resident_id != resident_id:
        raise ValueError(f"Resident 不存在或未注册：{resident_id}")
    now = ports.clock()
    status = _text(item.get("status"), 24).lower() or "idle"
    if status not in NODE_STATES:
        status = "idle"
    executor_raw = (
        item.get("operator_executor")
        if isinstance(item.get("operator_executor"), dict)
        else previous.get("operator_executor")
        if isinstance(previous.get("operator_executor"), dict)
        else {}
    )
    operator_executor: dict[str, str] = {}
    if executor_raw:
        operator_kind = _text(executor_raw.get("kind"), 32).lower()
        component_id = _text(
            executor_raw.get("component_id"),
            110,
        ).lower()
        if (
            operator_kind not in lineage_program.PACKET_OPERATOR_KINDS
            or not component_id.startswith(f"{operator_kind}_")
        ):
            raise ValueError("Packet Operator Resident 执行器绑定无效")
        operator_executor = {
            "component_id": component_id,
            "kind": operator_kind,
        }
    result = {
        "id": resident_id,
        "title": _text(item.get("title"), 120) or resident_id,
        "status": status,
        "x": ports.position(item.get("x"), 50),
        "y": ports.position(item.get("y"), 50),
        "backend": _text(item.get("backend"), 40) or "codex",
        "last_execution": item.get("last_execution") if isinstance(item.get("last_execution"), dict) else {},
        "workspace": {},
        "origin_seed_id": _text(item.get("origin_seed_id") or previous.get("origin_seed_id"), 80).lower(),
    }
    if operator_executor:
        result["operator_executor"] = operator_executor
    return result

def _normalize_lineage_payload(
    payload: dict[str, Any],
    previous: dict[str, Any],
    actor: str,
    *,
    resident_references: dict[str, dict[str, Any]] | None = None,
    reconnect_removed_inheritance: bool = True,
    ports: DefinitionPorts,
) -> dict[str, Any]:
    normalize_node = partial(_normalize_node, ports=ports)
    normalize_edge = partial(_normalize_edge, _clock=ports.clock)
    normalize_fragment = partial(_normalize_fragment, _clock=ports.clock)
    nodes_raw = payload.get("nodes") if isinstance(payload.get("nodes"), list) else previous.get("nodes") or []
    previous_nodes = {str(item.get("id") or ""): item for item in previous.get("nodes") or [] if isinstance(item, dict)}
    # A merged definition is validated while its Lineage row is locked.
    # Resolve its members together: a catalog read also projects Workspaces
    # and Family roles, and repeating it per node lengthens that transaction.
    node_ids = list(dict.fromkeys(
        _text(item.get("id"), 80).lower() if isinstance(item, dict) else ""
        for item in nodes_raw
    ))
    if any(not identity_contract.is_valid_id(rid, "resident") for rid in node_ids):
        raise ValueError("每个 HATG 节点必须绑定规范的 Resident ID")
    prepared = {rid: ref for rid, ref in (resident_references or {}).items() if isinstance(ref, dict)}
    requested = [rid for rid in node_ids if rid not in prepared]
    identity_map = {**(ports.resident_targets(requested) if requested else {}), **prepared}
    for rid in node_ids:
        if not identity_map.get(rid):
            raise ValueError(f"Resident 不存在或未注册：{rid}")
    nodes = [
        normalize_node(
            item,
            index,
            previous_nodes.get(str(item.get("id") or ""))
            if isinstance(item, dict)
            else None,
            resident_references=identity_map,
        )
        for index, item in enumerate(nodes_raw)
    ]
    for node in nodes:
        identity = identity_map.get(str(node.get("id") or "")) or {}
        resolved_resident_id = identity.get("resident_id")
        if resolved_resident_id:
            node["id"] = resolved_resident_id
    resident_ids = [node["id"] for node in nodes]
    if len(set(resident_ids)) != len(resident_ids):
        raise ValueError("HATG 节点 id 不能重复")

    edges_raw = payload.get("edges") if isinstance(payload.get("edges"), list) else previous.get("edges") or []
    candidate_forks_raw = [item for item in edges_raw if is_candidate_fork(item)]
    edges_raw = [item for item in edges_raw if not is_candidate_fork(item)]
    if reconnect_removed_inheritance:
        edges_raw = _reconnect_removed_inheritance_nodes(previous, nodes_raw, edges_raw)
    previous_edges = {str(item.get("id") or ""): item for item in previous.get("edges") or [] if isinstance(item, dict)}
    edges = [
        normalize_edge(
            item,
            set(resident_ids),
            previous_edges.get(str(item.get("id") or "")) if isinstance(item, dict) else None,
        )
        for item in edges_raw
    ]
    edge_ids = [edge["id"] for edge in edges]
    if len(set(edge_ids)) != len(edge_ids):
        raise ValueError("HATG 边 id 不能重复")
    relations = [(edge["source"], edge["target"], edge["type"]) for edge in edges]
    if len(set(relations)) != len(relations):
        raise ValueError("相同类型的节点关系不能重复")
    program_raw = (
        payload.get("program")
        if isinstance(payload.get("program"), dict)
        else previous.get("program")
        if isinstance(previous.get("program"), dict)
        else lineage_program.empty_program()
    )
    program = lineage_program.normalize_program(
        program_raw,
        lineage_nodes=nodes,
        previous=(
            previous.get("program")
            if isinstance(previous.get("program"), dict)
            else None
        ),
    )
    candidate_forks = _normalize_candidate_forks(
        candidate_forks_raw, program, set(resident_ids),
    )
    if len({edge["id"] for edge in candidate_forks} | set(edge_ids)) != len(candidate_forks) + len(edge_ids):
        raise ValueError("HATG 边 id 不能重复")
    edges.extend(candidate_forks)

    from .function_block_domain import normalize_blocks
    blocks = normalize_blocks({"nodes": nodes, "program": program,
                               "function_blocks": payload.get("function_blocks", previous.get("function_blocks", []))},
                              actor, previous=previous)
    from .effect_failure_notifications import initialize_recipients
    initialize_recipients(program, previous.get("program") or {}, actor)

    fragments_raw = payload.get("fragments") if isinstance(payload.get("fragments"), list) else previous.get("fragments") or []
    previous_fragments = {
        str(item.get("id") or ""): item
        for item in previous.get("fragments") or []
        if isinstance(item, dict)
    }
    fragments = [
        normalize_fragment(
            item,
            previous_fragments.get(str(item.get("id") or "")) if isinstance(item, dict) else None,
            actor,
        )
        for item in fragments_raw
    ]
    fragment_ids = [fragment["id"] for fragment in fragments]
    if len(set(fragment_ids)) != len(fragment_ids):
        raise ValueError("碎片 id 不能重复")

    previous_leader_resident_id = _text(previous.get("leader_resident_id"), 80).lower()
    leader_resident_id = _text(payload.get("leader_resident_id") if "leader_resident_id" in payload else previous_leader_resident_id, 80).lower()
    if leader_resident_id and leader_resident_id not in set(resident_ids):
        raise ValueError("Leader 必须是当前 HATG 中的节点")
    now = ports.clock()
    membership = str(
        payload.get("lineage_membership")
        if "lineage_membership" in payload
        else previous.get("lineage_membership")
    ).strip().lower()
    if membership not in LINEAGE_MEMBERSHIP_VALUES:
        raise ValueError("Lineage lineage_membership must be formal or personal")
    project_id = ports.requested_lineage_project_id(payload, previous, actor)
    hierarchy_fields = {}
    if "principal_hierarchy" in previous:
        from .principal_hierarchy import scope_after_project_change
        hierarchy_fields["principal_hierarchy"] = scope_after_project_change(previous["principal_hierarchy"], project_id)
    from . import lineage_automation_identity
    run_as = lineage_automation_identity.normalize(payload.get("run_as_principal", previous.get("run_as_principal")))
    return {
        "schema": ports.lineage_folder_schema,
        **({"run_as_principal": run_as} if run_as is not None else {}),
        "schema_version": ports.schema_version,
        **hierarchy_fields,
        "lineage_id": str(previous.get("lineage_id") or ""),
        "lineage_membership": membership,
        "project_id": project_id,
        "title": _text(payload.get("title") if "title" in payload else previous.get("title"), 140) or "未命名 Lineage",
        "description": _text(payload.get("description") if "description" in payload else previous.get("description"), 3000),
        "leader_resident_id": leader_resident_id,
        "nodes": nodes,
        "edges": edges,
        "program": program,
        "function_blocks": blocks,
        "fragments": fragments,
        "revision": int(previous.get("revision") or 0) + 1,
        "layout_revision": max(
            0,
            _integer(previous.get("layout_revision")),
        ),
        "runtime_revision": max(
            0,
            _integer(previous.get("runtime_revision")),
        ),
        "created_at": _text(previous.get("created_at"), 80) or now,
        "updated_at": now,
        "graph_updated_at": now,
        "layout_updated_at": _text(
            previous.get("layout_updated_at"),
            80,
        ),
        "runtime_updated_at": _text(
            previous.get("runtime_updated_at"),
            80,
        ),
        "created_by": _text(previous.get("created_by"), 120) or _safe_actor(actor),
    }
