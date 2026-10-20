"""Filter, Selector and Reducer: prompts for a Resident executor and result parsers.

An operator either follows its rules or asks an executor Resident to decide;
the executor answers with one ``<filter-result>``, ``<selector-result>`` or
``<reducer-result>`` JSON block (decision_4adcc1ff2f2c “Packet、算子与循环”).
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import lineage_primitives
from .lineage_values import _text

RULE_REDUCE_STRATEGIES = {"join", "first", "highest_priority"}
_SELECTOR_RESULT_RE = re.compile(
    r"<selector-result>\s*(\{.*\})\s*</selector-result>",
    re.IGNORECASE | re.DOTALL,
)
_FILTER_RESULT_RE = re.compile(
    r"<filter-result>\s*(\{.*\})\s*</filter-result>",
    re.IGNORECASE | re.DOTALL,
)
_REDUCER_RESULT_RE = re.compile(
    r"<reducer-result>\s*(\{.*\})\s*</reducer-result>",
    re.IGNORECASE | re.DOTALL,
)


def reducer_event_prompt(
    *,
    lineage_id: str,
    reducer_node: dict[str, Any],
    target_node: dict[str, Any],
    event: lineage_primitives.LineageEvent | dict[str, Any],
    packets: list[dict[str, Any]],
    source_titles: dict[str, str] | None = None,
    target_nodes: list[dict[str, Any]] | None = None,
    enveloped_packet_ids: set[str] | None = None,
) -> str:
    normalized_event = lineage_primitives.normalize_event(event)
    reducer = (
        reducer_node.get("config")
        if isinstance(reducer_node.get("config"), dict)
        else {}
    )
    titles = source_titles or {}
    envelope_ids = enveloped_packet_ids or set()
    sections: list[str] = []
    for packet in packets:
        resident_id = str(packet.get("from_resident_id") or "")
        title = titles.get(resident_id) or resident_id
        lines = [
            *(
                ["[HATG Packet]"]
                if str(packet.get("id") or "") in envelope_ids
                else []
            ),
            (
                f"### {title} · {packet.get('key')} "
                f"(`{packet.get('id')}`)"
            ),
            f"priority: {int(packet.get('priority') or 0)}",
            "payload: "
            + json.dumps(
                (packet.get("message") or {}).get("data") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            lineage_primitives.packet_prompt(packet),
        ]
        sections.append("\n".join(lines))
    effect_targets: list[dict[str, Any]] = []
    seen_target_ids: set[str] = set()
    for item in (
        [target_node]
        if target_nodes is None
        else target_nodes
    ):
        if not isinstance(item, dict):
            continue
        resident_id = str(item.get("id") or "")
        if not resident_id or resident_id in seen_target_ids:
            continue
        seen_target_ids.add(resident_id)
        effect_targets.append(item)
    target_heading = "、".join(
        str(item.get("title") or item.get("id") or "Target")
        for item in effect_targets
    ) or "无 Resident endpoint"
    target_lines = [
        f"- {item.get('title') or item.get('id')} (`{item.get('id')}`)"
        for item in effect_targets
    ]
    return "\n\n".join([
        f"[Lineage Prompt Reduce] {reducer_node.get('title')} → {target_heading}",
        (
            f"Lineage：{lineage_id}\n"
            f"来源 Event：{normalized_event.type} ({normalized_event.id})\n"
            "下游 Effect 的 Resident endpoints：\n"
            + (
                "\n".join(target_lines)
                or "- 无；下游 Effect 不绑定 Resident endpoint"
            )
        ),
        f"Reducer 定位：\n{reducer.get('prompt') or '合并消息内容，保留输入事件事实。'}",
        "同一 Event 的上游 Packet：\n\n" + "\n\n".join(sections),
        (
            "必须且只能输出一个 <reducer-result> JSON block，内容是一个合并后的"
            "Packet。格式：<reducer-result>{\"key\":\""
            f"{reducer.get('output_key') or 'reduced'}"
            "\",\"message\":{\"text\":\"合并后的消息\"},\"events\":[],"
            "\"priority\":0}</reducer-result>。不要在 block 外写正文。"
        ),
    ])


def filter_event_prompt(
    *,
    lineage_id: str,
    filter_node: dict[str, Any],
    event: lineage_primitives.LineageEvent | dict[str, Any],
    packets: list[dict[str, Any]],
    source_titles: dict[str, str] | None = None,
    enveloped_packet_ids: set[str] | None = None,
) -> str:
    normalized_event = lineage_primitives.normalize_event(event)
    filter_config = (
        filter_node.get("config")
        if isinstance(filter_node.get("config"), dict)
        else {}
    )
    titles = source_titles or {}
    envelope_ids = enveloped_packet_ids or set()
    sections: list[str] = []
    for packet in packets:
        resident_id = str(packet.get("from_resident_id") or "")
        title = titles.get(resident_id) or resident_id
        lines = [
            *(
                ["[HATG Packet]"]
                if str(packet.get("id") or "") in envelope_ids
                else []
            ),
            (
                f"### {title} · {packet.get('key')} "
                f"(`{packet.get('id')}`)"
            ),
            f"priority: {int(packet.get('priority') or 0)}",
            "payload: "
            + json.dumps(
                (packet.get("message") or {}).get("data") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            lineage_primitives.packet_prompt(packet),
        ]
        sections.append("\n".join(lines))
    return "\n\n".join(
        [
            f"[Lineage Prompt Filter] {filter_node.get('title')}",
            (
                f"Lineage：{lineage_id}\n"
                f"来源 Event：{normalized_event.type} ({normalized_event.id})"
            ),
            f"Filter 定位：\n{filter_config.get('prompt') or '保留符合条件的 Packet。'}",
            "候选 Packet：\n\n" + "\n\n".join(sections),
            (
                "只过滤候选，不改写内容。必须且只能输出一个 <filter-result> "
                "JSON block：<filter-result>{\"selected_packet_ids\":["
                "\"packet_...\"]}</filter-result>。可选择零个或多个，ID 必须"
                "来自以上候选；不要在 block 外写正文。"
            ),
        ]
    )


def parse_filter_result(
    output: object,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_text = str(output or "").strip()
    match = _FILTER_RESULT_RE.fullmatch(raw_text)
    if not match:
        raise ValueError(
            "Filter 回复必须且只能包含一个 <filter-result>{...}</filter-result>"
        )
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("Filter result 不是有效 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "selected_packet_ids"
    }:
        raise ValueError("Filter result 只能包含 selected_packet_ids")
    raw_ids = payload.get("selected_packet_ids")
    if not isinstance(raw_ids, list):
        raise ValueError("selected_packet_ids 必须是数组")
    selected_ids = [_text(value, 120) for value in raw_ids]
    if any(not value for value in selected_ids):
        raise ValueError("selected_packet_ids 不能包含空值")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("selected_packet_ids 不能重复")
    candidates_by_id = {
        str(candidate.get("id") or ""): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("id")
    }
    unknown = [value for value in selected_ids if value not in candidates_by_id]
    if unknown:
        raise ValueError("Filter 选择了不存在的 Packet ID")
    return [dict(candidates_by_id[value]) for value in selected_ids]


def selector_event_prompt(
    *,
    lineage_id: str,
    selector_node: dict[str, Any],
    event: lineage_primitives.LineageEvent | dict[str, Any],
    packets: list[dict[str, Any]],
    source_titles: dict[str, str] | None = None,
    enveloped_packet_ids: set[str] | None = None,
) -> str:
    """Render the Resident-backed multi-branch routing contract."""

    normalized_event = lineage_primitives.normalize_event(event)
    selector = (
        selector_node.get("config")
        if isinstance(selector_node.get("config"), dict)
        else {}
    )
    branches = lineage_primitives.normalize_selector_branches(
        selector.get("branches")
    )
    titles = source_titles or {}
    envelope_ids = enveloped_packet_ids or set()
    sections: list[str] = []
    for packet in packets:
        resident_id = str(packet.get("from_resident_id") or "")
        title = titles.get(resident_id) or resident_id
        lines = [
            *(
                ["[HATG Packet]"]
                if str(packet.get("id") or "") in envelope_ids
                else []
            ),
            (
                f"### {title} · {packet.get('key')} "
                f"(`{packet.get('id')}`)"
            ),
            f"priority: {int(packet.get('priority') or 0)}",
            "payload: "
            + json.dumps(
                (packet.get("message") or {}).get("data") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            lineage_primitives.packet_prompt(packet),
        ]
        sections.append("\n".join(lines))
    branch_contract = [
        {
            "id": branch["id"],
            "output_port": lineage_primitives.selector_branch_port(
                branch["id"]
            ),
            "label": branch["label"],
            "description": branch["description"],
        }
        for branch in branches
    ]
    result_shape = {
        "branches": {branch["id"]: [] for branch in branches}
    }
    return "\n\n".join(
        [
            f"[Lineage Prompt Route] {selector_node.get('title')}",
            (
                f"Lineage：{lineage_id}\n"
                f"来源 Event：{normalized_event.type} ({normalized_event.id})"
            ),
            f"Selector 定位：\n{selector.get('prompt') or '把 Packet 路由到具名分支。'}",
            "输出分支：\n"
            + json.dumps(
                branch_contract,
                ensure_ascii=False,
                indent=2,
            ),
            "候选 Packet：\n\n" + "\n\n".join(sections),
            (
                "只做路由，不改写内容。必须且只能输出一个 <selector-result> "
                "JSON block；branches 必须恰好包含以上全部分支 ID，每个值是进入"
                "该分支的 Packet ID 数组。一个 Packet 可以进入零个、一个或多个"
                "分支，但每个分支内不得重复。格式：<selector-result>"
                + json.dumps(result_shape, ensure_ascii=False)
                + "</selector-result>。不要在 block 外写正文。"
            ),
        ]
    )


def parse_selector_result(
    output: object,
    candidates: list[dict[str, Any]],
    branches: object,
) -> dict[str, list[dict[str, Any]]]:
    """Parse branch routing while preserving canonical Packet envelopes."""

    normalized_branches = lineage_primitives.normalize_selector_branches(
        branches
    )
    expected_branch_ids = [branch["id"] for branch in normalized_branches]
    raw_text = str(output or "").strip()
    match = _SELECTOR_RESULT_RE.fullmatch(raw_text)
    if not match:
        raise ValueError(
            "Selector 回复必须且只能包含一个 "
            "<selector-result>{...}</selector-result>"
        )
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("Selector result 不是有效 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"branches"}:
        raise ValueError("Selector result 只能包含 branches")
    raw_routes = payload.get("branches")
    if not isinstance(raw_routes, dict):
        raise ValueError("Selector result branches 必须是 JSON object")
    if set(raw_routes) != set(expected_branch_ids):
        raise ValueError("Selector result 必须恰好返回全部已配置分支")
    candidates_by_id = {
        str(candidate.get("id") or ""): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("id")
    }
    routed: dict[str, list[dict[str, Any]]] = {}
    for branch_id in expected_branch_ids:
        raw_ids = raw_routes.get(branch_id)
        if not isinstance(raw_ids, list):
            raise ValueError("Selector 每个分支的 Packet ID 必须是数组")
        selected_ids = [_text(value, 120) for value in raw_ids]
        if any(not value for value in selected_ids):
            raise ValueError("Selector 分支不能包含空 Packet ID")
        if len(set(selected_ids)) != len(selected_ids):
            raise ValueError("Selector 同一分支内的 Packet ID 不能重复")
        unknown = [
            value for value in selected_ids if value not in candidates_by_id
        ]
        if unknown:
            raise ValueError("Selector 路由了不存在的 Packet ID")
        routed[lineage_primitives.selector_branch_port(branch_id)] = [
            dict(candidates_by_id[value]) for value in selected_ids
        ]
    return routed


def rule_selector_result(
    config: dict[str, Any],
    packets: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    return lineage_primitives.route_packets_to_selector_branches(
        packets,
        config.get("branches"),
    )


def rule_filter_result(
    config: dict[str, Any],
    packets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return lineage_primitives.select_packets(
        packets,
        config.get("selection"),
    )


def rule_reducer_result(
    config: dict[str, Any],
    packets: list[dict[str, Any]],
) -> lineage_primitives.Packet:
    if not packets:
        raise ValueError("Reducer 至少需要一个 Packet")
    strategy = _text(config.get("reduce_strategy"), 40).lower() or "join"
    if strategy not in RULE_REDUCE_STRATEGIES:
        raise ValueError("Reducer rule strategy 无效")
    selected = packets
    if strategy == "highest_priority":
        selected = [max(packets, key=lambda packet: int(packet.get("priority") or 0))]
    elif strategy == "first":
        selected = packets[:1]
    separator = str(config.get("reduce_separator") or "\n\n")[:200]
    messages = [packet["message"] for packet in selected if packet.get("message")]
    text = separator.join(message["text"] for message in messages if message.get("text"))
    data = ([message["data"] for message in messages if message.get("data")])
    parts = [part for message in messages for part in message.get("content_parts") or []]
    events = [event for packet in selected for event in packet.get("events") or []]
    return lineage_primitives.make_packet(
        config.get("output_key") or "reduced", text,
        data={"items": data} if data else None,
        content_parts=parts or None, events=events,
        priority=max(int(packet.get("priority") or 0) for packet in selected),
        parent_packet_ids=[packet["id"] for packet in packets if packet.get("id")],
    )


def parse_reducer_result(output: object) -> dict[str, Any]:
    """Parse the one merged Packet of a Resident-backed Reducer."""

    match = _REDUCER_RESULT_RE.fullmatch(str(output or "").strip())
    if not match:
        raise ValueError("Reducer 回复必须且只能包含一个 <reducer-result>{...}</reducer-result>")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("Reducer result 不是有效 JSON") from exc
    return lineage_primitives.normalize_packet(payload).to_dict()
