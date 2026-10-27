from __future__ import annotations

import json
import re
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .resident_discovery_client import ResidentDiscoveryClient, ResidentDiscoveryUnavailable
from .society_service import get_society_service, resident_media_domain
from .society_service.resident_protocol import WORKING_DIRECTORY_FIELD
from .portal_ui_state import get_portal_ui_state_store
from . import system_application_flows
from .lineage_workflows import WorkflowBindings, WorkflowPending, WorkflowFailed
from .resident_handoff import (
    run_resident_turn,
    submit_invocation,
)


PORTAL_ROOT = Path(__file__).resolve().parent.parent
RECOMMENDATION_UI_NAMESPACE = "resident-recommendation"
RECOMMENDATION_UI_KEY = "latest"
RECOMMENDATION_CAPABILITY = "resident-discovery"
RECOMMENDATION_ARGUMENT_BUDGET = 56_000
RECOMMENDATION_PROMPT_BUDGET = 128_000


class RecommendationIntakeClosed(ResidentDiscoveryUnavailable):
    pass


class RecommendationResultInvalid(ValueError):
    """The completed model output cannot become a recommendation result."""


class RecommendationPayloadTooLarge(ValueError):
    def __init__(self, section: str, size: int, budget: int):
        self.section, self.size, self.budget = section, size, budget
        super().__init__(f"推荐{section}过长：{size} 字节，预算 {budget} 字节；请缩短查询后重新提交。")


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))


def _clip_json_text(text: str, budget: int) -> str:
    """Count UTF-8 and JSON escaping without splitting a Unicode character."""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _json_size(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _fit_candidates(candidates: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Bound fresh projections, retaining every identity and the original order."""
    if _json_size(candidates) <= budget:
        return candidates
    per_candidate = (budget - 2 - max(0, len(candidates) - 1)) // max(1, len(candidates))
    for candidate in candidates:
        details = candidate.get("summary", candidate)
        # Spend space on primary ownership and current work before ancillary
        # expertise. Identity fields are never shortened or removed.
        fields = [(details, key) for key in (
            "supporting_expertise", "durable_expertise", "avoid_when",
            "route_when", "branch_delta", "current_focus", "primary_ownership",
        )] + [(candidate, "lineage_title"), (candidate, "title")]
        for owner, key in fields:
            excess = _json_size(candidate) - per_candidate
            if excess <= 0:
                break
            value = owner.get(key)
            if isinstance(value, list):
                while value and excess > 0:
                    value[-1] = _clip_json_text(value[-1], _json_size(value[-1]) - excess)
                    if not value[-1]:
                        value.pop()
                    excess = _json_size(candidate) - per_candidate
            elif isinstance(value, str):
                owner[key] = _clip_json_text(value, _json_size(value) - excess)
    if _json_size(candidates) > budget:
        raise RecommendationPayloadTooLarge("候选身份资料", _json_size(candidates), budget)
    return candidates


def _available_query_media(query: str) -> str:
    def replace(match):
        try:
            if Path(match.group(1).strip()).is_file():
                return match.group(0)
        except (OSError, ValueError):
            pass
        return "[image unavailable]"

    return resident_media_domain.RESIDENT_IMAGE_MARKER_RE.sub(replace, query)


def _recommendation_query(query: str) -> str:
    clean = _available_query_media(str(query or "").strip())
    if not clean:
        raise ValueError("query required")
    if len(clean) > 4000:
        raise ValueError("推荐查询不能超过 4000 字符，请缩短查询后重新提交。")
    return clean


def recommendation_chat_binding(actor: str) -> dict[str, str]:
    """Resolve the actual capability identity; opening Chat creates no Resident."""
    return _recommendation_resident_binding(actor)


def _recommendation_resident_binding(actor: str) -> dict[str, str]:
    """The Resident configured for discovery."""
    from . import application_targets

    del actor
    try:
        holder = application_targets.discovery_resident()
    except (application_targets.ApplicationUnconfigured, KeyError) as exc:
        raise ResidentDiscoveryUnavailable(str(exc)) from exc
    return {"lineage_id": holder["lineage_id"], "resident_id": holder["resident_id"]}


def _available_recommendation_resident_binding(actor: str) -> dict[str, str]:
    """Resolve the capability and reject a Resident that cannot accept work."""
    binding = _recommendation_resident_binding(actor)
    snapshot = get_society_service().resident.get_resident_conversation_snapshot(
        binding["resident_id"], actor,
    )
    state = snapshot.get("state") if isinstance(snapshot, Mapping) else {}
    if isinstance(state, Mapping) and state.get("intake_state") == "closed":
        raise RecommendationIntakeClosed(
            "Resident discovery is unavailable because its capability Resident has closed intake"
        )
    try:
        return system_application_flows.binding("resident_discovery", actor=actor, target=binding)
    except system_application_flows.ApplicationFlowUnavailable as exc:
        raise RecommendationIntakeClosed(str(exc)) from exc


def _compact_candidate(item: Mapping[str, Any]) -> dict[str, Any]:
    summary = item.get("summary") if isinstance(item.get("summary"), Mapping) else {}

    def short_list(value: object) -> list[str]:
        return [str(entry)[:180] for entry in list(value or [])[:5]]

    primary_ownership = summary.get("primary_ownership")
    return {
        "resident_id": str(item.get("resident_id") or ""),
        "title": str(item.get("title") or "")[:160],
        "lineage_id": str(item.get("lineage_id") or ""),
        "lineage_title": str(item.get("lineage_title") or "")[:160],
        "current_focus": str(summary.get("current_focus") or "")[:500],
        "branch_delta": str(summary.get("branch_delta") or "")[:350],
        "durable_expertise": short_list(summary.get("durable_expertise")),
        "primary_ownership": short_list(primary_ownership),
        "supporting_expertise": short_list(summary.get("supporting_expertise")),
        "route_when": short_list(summary.get("route_when")),
        "avoid_when": short_list(summary.get("avoid_when")),
    }


def _candidate_snapshot(item: Mapping[str, Any]) -> dict[str, Any]:
    compact = _compact_candidate(item)
    return {
        "resident_id": compact["resident_id"],
        "title": compact["title"],
        "lineage_id": compact["lineage_id"],
        "lineage_title": compact["lineage_title"],
        "summary": {"current_focus": compact["current_focus"][:100]},
    }


def _routing_context(query: str, candidates: list[dict[str, Any]], limit: int, request_id: str) -> dict[str, Any]:
    context = {
        WORKING_DIRECTORY_FIELD: str(PORTAL_ROOT),
        "resident_routing": {
            "query": query, "limit": limit, "request_id": request_id, "candidates": [],
        },
    }
    arguments = {"content_parts": [{"type": "context_reference", "context": context}]}
    base_size = _json_size(arguments)
    if base_size > RECOMMENDATION_ARGUMENT_BUDGET:
        raise RecommendationPayloadTooLarge("结构化参数", base_size, RECOMMENDATION_ARGUMENT_BUDGET)
    available = RECOMMENDATION_ARGUMENT_BUDGET - base_size + _json_size([])
    context["resident_routing"]["candidates"] = _fit_candidates(
        [_candidate_snapshot(item) for item in candidates], available,
    )
    size = _json_size(arguments)
    if size > RECOMMENDATION_ARGUMENT_BUDGET:
        raise RecommendationPayloadTooLarge("结构化参数", size, RECOMMENDATION_ARGUMENT_BUDGET)
    return context


def _recommendation_card(
    item: Mapping[str, Any], *, reason: str, confidence: str, action: str,
) -> dict[str, Any]:
    # Discovery records include full summaries, duplicate search text and ranking
    # metadata. Only the card projection belongs in the response/latest UI state;
    # input messages keep a minimal identity snapshot for result recovery.
    compact = _compact_candidate(item)
    return {
        **{key: compact[key] for key in (
            "resident_id", "title", "lineage_id", "lineage_title",
        )},
        "reason": reason or compact["current_focus"],
        "confidence": confidence,
        "recommended_action": action,
    }


def _prompt(query: str, candidates: list[dict[str, Any]], limit: int) -> str:
    payload = {
        "request": query,
        "maximum_recommendations": limit,
        "preferred_minimum_recommendations": min(3, limit, len(candidates)),
        "candidates": [],
    }
    instructions = (
        "Select the best Resident destinations only from the supplied candidates. "
        "This is an independent routing request: candidates from earlier turns "
        "are not eligible unless present in this request's candidates. "
        "Honor an explicit recommendation count in the request. Otherwise offer a "
        "ranked shortlist of 3-5 relevant Residents when available. Aim for "
        "preferred_minimum_recommendations, never "
        "exceed maximum_recommendations, and never pad with unrelated Residents. "
        "If fewer candidates genuinely fit, return fewer and explain why in summary. "
        "Distinguish the requested destination from referenced evidence sources. "
        "Primary ownership is stronger than topical overlap; supporting expertise is "
        "not ownership. Choose action=open when the existing Resident context fits, "
        "or action=fork when that Resident is the right base but the request is a "
        "materially separate branch. Never invent IDs. If no supplied candidate "
        "fits, return recommendations=[] and explain why in summary. "
        "Give every recommendation its own confidence: high for clear ownership "
        "and a strong context match, medium for a relevant alternative with gaps "
        "or uncertain ownership, low for a plausible but weakly supported match. "
        "Assess confidence independently for each Resident; these are qualitative "
        "model judgments, not calibrated probabilities. Order by confidence and "
        "task fit, strongest first. Prefer concise Chinese reasons explaining each "
        "Resident's fit and any important uncertainty. "
        "Return only one JSON object with: summary (string), recommendations (array of "
        "resident_id, reason, confidence high|medium|low, action open|fork), and "
        "fork_advice (recommended boolean, source_resident_id, reason, suggested_title).\n\n"
    )
    base_size = len(instructions.encode("utf-8")) + _json_size(payload)
    if base_size > RECOMMENDATION_PROMPT_BUDGET:
        raise RecommendationPayloadTooLarge("提示词", base_size, RECOMMENDATION_PROMPT_BUDGET)
    available = RECOMMENDATION_PROMPT_BUDGET - base_size + _json_size([])
    payload["candidates"] = _fit_candidates([_compact_candidate(item) for item in candidates], available)
    prompt = instructions + json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(prompt.encode("utf-8")) > RECOMMENDATION_PROMPT_BUDGET:
        raise RecommendationPayloadTooLarge("提示词", len(prompt.encode("utf-8")), RECOMMENDATION_PROMPT_BUDGET)
    return prompt


def _json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    candidates = [raw]
    if "```" in raw:
        candidates.extend(
            part.removeprefix("json").strip()
            for part in raw.split("```")[1::2]
        )
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise RecommendationResultInvalid("Resident recommendation returned invalid JSON")


def _fallback_reason(item: Mapping[str, Any]) -> str:
    summary = item.get("summary") if isinstance(item.get("summary"), Mapping) else {}
    ownership = summary.get("primary_ownership") or summary.get("route_when") or []
    if isinstance(ownership, list) and ownership:
        return ("目录检索匹配；主要职责：" + "；".join(str(value) for value in ownership[:3]))[:500]
    focus = str(summary.get("current_focus") or "").strip()
    return (f"目录检索匹配；当前职责：{focus}" if focus else "目录检索匹配；候选摘要与请求相关")[:500]


def _fallback_result(
    query: str,
    candidates: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    return {
        "schema": "tfi.resident-discovery.recommendation.v2",
        "query": query,
        "summary": "推荐能力当前不可接单，以下结果来自可访问 Resident 目录检索，未经过模型分析。",
        "fallback": True,
        "fallback_reason": "recommendation-capability-intake-closed",
        "recommendation_source": "directory-search",
        "recommendations": [
            _recommendation_card(
                item, reason=_fallback_reason(item), confidence="low", action="open",
            ) for item in candidates[:limit]
        ],
        "fork_advice": {
            "recommended": False,
            "source_resident_id": "",
            "reason": "",
            "suggested_title": "",
        },
    }


def _normalize(
    query: str,
    candidates: list[dict[str, Any]],
    raw: Mapping[str, Any],
    limit: int,
) -> dict[str, Any]:
    allowed = {
        str(item.get("resident_id") or ""): item
        for item in candidates
        if str(item.get("resident_id") or "")
    }
    raw_recommendations = raw.get("recommendations")
    if not isinstance(raw_recommendations, list):
        raise RecommendationResultInvalid("Resident recommendation must contain a recommendations array")
    recommendations = []
    seen = set()
    for value in raw_recommendations:
        if not isinstance(value, Mapping):
            continue
        resident_id = str(value.get("resident_id") or "")
        if resident_id not in allowed or resident_id in seen:
            continue
        seen.add(resident_id)
        action = str(value.get("action") or "open").lower()
        confidence = str(value.get("confidence") or "low").lower()
        recommendations.append(_recommendation_card(
            allowed[resident_id],
            reason=str(value.get("reason") or "")[:800],
            confidence=confidence if confidence in {"high", "medium", "low"} else "low",
            action=action if action in {"open", "fork"} else "open",
        ))
        if len(recommendations) >= limit:
            break
    # An explicit empty array is a valid no-match result. A non-empty array
    # whose entries all fail candidate validation is still a protocol error.
    if raw_recommendations and not recommendations:
        raise RecommendationResultInvalid("Resident recommendation returned no valid candidate")
    fork = raw.get("fork_advice") if isinstance(raw.get("fork_advice"), Mapping) else {}
    source_id = str(fork.get("source_resident_id") or "")
    if source_id not in allowed or fork.get("recommended") is not True:
        fork_result = {
            "recommended": False,
            "source_resident_id": "",
            "reason": "",
            "suggested_title": "",
        }
    else:
        source = allowed[source_id]
        fork_result = {
            "recommended": True,
            "source_resident_id": source_id,
            "source_title": str(source.get("title") or source_id),
            "source_lineage_id": str(source.get("lineage_id") or ""),
            "source_lineage_title": str(source.get("lineage_title") or ""),
            "reason": str(fork.get("reason") or "")[:800],
            "suggested_title": str(fork.get("suggested_title") or "")[:200],
        }
    return {
        "schema": "tfi.resident-discovery.recommendation.v2",
        "query": query,
        "summary": str(raw.get("summary") or "")[:1000],
        "fallback": False,
        "fallback_reason": "",
        "recommendation_source": "resident-analysis",
        "recommendations": recommendations,
        "fork_advice": fork_result,
    }


def _candidate_search(
    *,
    actor: str,
    query: str,
    limit: int,
    resident_ids: list[str] | None,
    client: ResidentDiscoveryClient | None,
) -> tuple[int, list[dict[str, Any]]]:
    requested_limit = max(1, min(8, int(limit or 5)))
    explicit_count = re.search(
        r"(?:只|仅)?(?:给我)?(?:推荐|挑选|选出|列出|返回|找|要)\s*"
        r"(?:给我)?\s*(?:只|仅)?\s*([1-8一二三四五六七八两])\s*"
        r"(?:位|个|名|条)",
        query,
    )
    if explicit_count:
        count = explicit_count.group(1)
        requested_limit = min(requested_limit, int(count) if count.isdigit() else {
            "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8,
        }[count])
    discovery = client or ResidentDiscoveryClient()
    search = discovery.routing_candidates(
        actor=actor,
        query=query,
        limit=min(48, max(24, requested_limit * 8)),
        resident_ids=resident_ids,
    )
    return requested_limit, [
        dict(item)
        for item in search.get("results") or []
        if isinstance(item, Mapping) and item.get("resident_id")
    ]


def _store_latest_recommendation(
    *,
    actor: str,
    query: str,
    response: Mapping[str, Any],
) -> None:
    status = str(response.get("status") or "").strip()
    value: dict[str, Any] = {
        "status": status,
        "query": query,
        "request_id": str(response.get("request_id") or ""),
    }
    if status == "completed" and isinstance(response.get("result"), Mapping):
        value["result"] = dict(response["result"])
    elif status == "failed":
        value.update({"error": str(response.get("error") or ""), "retryable": False})
        value.update({key: response[key] for key in ("error_code", "payload_section", "size_bytes", "budget_bytes") if key in response})
    elif status in {"accepted", "workflow_pending"}:
        value.update({
            "resident_id": str(response.get("resident_id") or ""),
            "lineage_id": str(response.get("lineage_id") or ""),
            "message_id": str(response.get("message_id") or ""),
        })
        value.update({key: response[key] for key in ("event_id", "event_source_id", "effect_id", "trigger_path_id") if key in response})
    else:
        return
    get_portal_ui_state_store().put(
        actor,
        RECOMMENDATION_UI_NAMESPACE,
        RECOMMENDATION_UI_KEY,
        value,
    )


def start_recommendation(
    *,
    actor: str,
    query: str,
    limit: int = 5,
    resident_ids: list[str] | None = None,
    client: ResidentDiscoveryClient | None = None,
) -> dict[str, Any]:
    actor_id = str(actor or "").strip()
    clean_query = _recommendation_query(query)
    if not actor_id:
        raise ValueError("actor required")
    previous = get_portal_ui_state_store().get(actor_id, RECOMMENDATION_UI_NAMESPACE, RECOMMENDATION_UI_KEY)
    previous_value = (previous or {}).get("value") or {}
    if previous_value.get("status") == "workflow_pending":
        # A retry observes the request that was already admitted, preserving
        # its original query and candidate snapshot even while an Effect closes.
        return _recover_recommendation_workflow(actor_id, previous_value)
    request_id = f"resident-recommendation-{secrets.token_hex(12)}"
    requested_limit, candidates = _candidate_search(
        actor=actor_id,
        query=clean_query,
        limit=limit,
        resident_ids=resident_ids,
        client=client,
    )
    if not candidates:
        response = {
            "status": "completed",
            "request_id": request_id,
            "result": {
                "schema": "tfi.resident-discovery.recommendation.v2",
                "query": clean_query,
                "summary": "",
                "fallback": False,
                "fallback_reason": "",
                "recommendation_source": "candidate-search",
                "recommendations": [],
                "fork_advice": {
                    "recommended": False,
                    "source_resident_id": "",
                    "reason": "",
                    "suggested_title": "",
                },
            },
        }
        _store_latest_recommendation(actor=actor_id, query=clean_query, response=response)
        return response
    try:
        prompt = _prompt(clean_query, candidates, requested_limit)
        context = _routing_context(clean_query, candidates, requested_limit, request_id)
    except RecommendationPayloadTooLarge as exc:
        response = {
            "status": "failed", "request_id": request_id,
            "error": str(exc), "error_code": "recommendation.input_too_large", "retryable": False,
            "payload_section": exc.section, "size_bytes": exc.size, "budget_bytes": exc.budget,
        }
        _store_latest_recommendation(actor=actor_id, query=clean_query, response=response)
        return response
    try:
        binding = _available_recommendation_resident_binding(actor_id)
    except RecommendationIntakeClosed:
        response = {
            "status": "completed",
            "request_id": request_id,
            "result": _fallback_result(clean_query, candidates, requested_limit),
        }
        _store_latest_recommendation(actor=actor_id, query=clean_query, response=response)
        return response
    try:
        handle = _submit_recommendation(binding, actor_id, prompt, context, request_id)
    except WorkflowPending as exc:
        _store_latest_recommendation(actor=actor_id, query=clean_query, response={
            **exc.handle, "status": "workflow_pending", "request_id": request_id,
        })
        raise ResidentDiscoveryUnavailable(str(exc)) from exc
    result = {**handle, "status": "accepted", "request_id": request_id}
    _store_latest_recommendation(actor=actor_id, query=clean_query, response=result)
    return result


def _submit_recommendation(binding, actor_id, prompt, context, request_id):
    return submit_invocation(
        resident_binding=binding,
        workflow_binding=binding,
        actor=actor_id,
        message=prompt,
        resident_scope=f"resident-routing:{actor_id}",
        lineage_scope="resident-routing",
        title="Resident routing",
        backend="",
        cwd=str(PORTAL_ROOT),
        context_refs=context,
        business_ref=request_id,
        metadata={"surface": "resident-discovery"},
    )


def _recover_recommendation_workflow(actor, value):
    try:
        handle = WorkflowBindings().observe(value, actor=actor)
    except WorkflowPending as exc:
        raise ResidentDiscoveryUnavailable(str(exc)) from exc
    except WorkflowFailed as exc:
        response = {**value, "status": "failed", "error": str(exc), "retryable": False}
        _store_latest_recommendation(actor=actor, query=value["query"], response=response)
        return response
    response = {**handle, "status": "accepted", "request_id": value["request_id"], "query": value["query"]}
    _store_latest_recommendation(actor=actor, query=value["query"], response=response)
    return response


def latest_recommendation(*, actor: str) -> dict[str, Any]:
    actor_id = str(actor or "").strip()
    if not actor_id:
        raise ValueError("actor required")
    store = get_portal_ui_state_store()
    association = store.get(
        actor_id,
        RECOMMENDATION_UI_NAMESPACE,
        RECOMMENDATION_UI_KEY,
    )
    value = association.get("value") if isinstance(association, Mapping) else None
    if not isinstance(value, Mapping):
        return {"status": "none"}
    status = str(value.get("status") or "accepted").strip()
    if status == "workflow_pending":
        return _recover_recommendation_workflow(actor_id, value)
    if status == "failed":
        return dict(value)
    if status == "completed" and isinstance(value.get("result"), Mapping):
        return {
            "status": "completed",
            "query": str(value.get("query") or ""),
            "request_id": str(value.get("request_id") or ""),
            "result": dict(value["result"]),
        }
    if status != "accepted":
        return {"status": "none"}
    resident_id = str(value.get("resident_id") or "").strip()
    message_id = str(value.get("message_id") or "").strip()
    if not resident_id or not message_id:
        return {"status": "none"}
    return {
        "status": "accepted",
        "query": str(value.get("query") or ""),
        "request_id": str(value.get("request_id") or ""),
        "resident_id": resident_id,
        "lineage_id": str(value.get("lineage_id") or ""),
        "message_id": message_id,
        **{key: value[key] for key in ("event_id", "event_source_id", "effect_id", "trigger_path_id") if key in value},
    }


def recommendation_result(
    *,
    actor: str,
    resident_id: str,
    message_id: str,
) -> dict[str, Any]:
    service = get_society_service().resident
    input_state = dict(
        service.get_resident_input_state(resident_id, actor, message_id) or {}
    )
    status = str(input_state.get("state") or "").strip().casefold()
    if status != "completed":
        if status in {"queued", "running"}:
            snapshot = service.get_resident_conversation_snapshot(
                resident_id, actor,
            )
            state = snapshot.get("state") if isinstance(snapshot, Mapping) else {}
            if isinstance(state, Mapping) and state.get("intake_state") == "closed":
                return {
                    "status": "failed",
                    "error": "Resident discovery capability is not accepting work",
                    "input_state": input_state,
                }
        return {"status": status or "queued", "input_state": input_state}
    input_message = dict(
        service.get_resident_message(resident_id, actor, message_id) or {}
    )
    routing_context: dict[str, Any] = {}
    for part in input_message.get("content_parts") or []:
        if not isinstance(part, Mapping) or part.get("type") != "context_reference":
            continue
        context = part.get("context")
        if isinstance(context, Mapping) and isinstance(
            context.get("resident_routing"), Mapping
        ):
            routing_context = dict(context["resident_routing"])
            break
    if not routing_context:
        return {
            "status": "failed",
            "error": "推荐请求缺少候选上下文，请重新提交查询。",
            "error_code": "recommendation.context_missing",
            "retryable": False,
            "input_state": input_state,
        }
    reply_text = "\n".join(
        str(part.get("text") or "")
        for reply_id in input_state.get("reply_message_ids") or []
        for part in (
            service.get_resident_message(resident_id, actor, str(reply_id)) or {}
        ).get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") == "text"
    ).strip()
    if not reply_text:
        # Completion state and reply projection are published independently.
        # Keep the polling contract non-terminal until the reply is readable.
        return {"status": "running", "input_state": input_state}
    candidates = [
        dict(item)
        for item in routing_context.get("candidates") or []
        if isinstance(item, Mapping)
    ]
    request_id = str(routing_context.get("request_id") or "")
    try:
        result = _normalize(
            str(routing_context.get("query") or ""),
            candidates,
            _json_object(reply_text),
            int(routing_context.get("limit") or 5),
        )
    except RecommendationResultInvalid as exc:
        # The Resident turn completed, but the business result failed validation.
        # Re-reading immutable replies cannot repair them. Keep transport/read
        # failures outside this boundary so clients can still retry those.
        return {
            "status": "failed",
            "request_id": request_id,
            "error": f"推荐结果无效，请重新提交查询：{exc}",
            "error_code": "recommendation.invalid_result",
            "retryable": False,
            "input_state": input_state,
        }
    return {
        "status": "completed",
        "request_id": request_id,
        "result": result,
    }


def recommend_residents(
    *,
    actor: str,
    query: str,
    limit: int = 5,
    resident_ids: list[str] | None = None,
    allow_fallback: bool = False,
    client: ResidentDiscoveryClient | None = None,
) -> dict[str, Any]:
    actor_id = str(actor or "").strip()
    clean_query = _recommendation_query(query)
    if not actor_id:
        raise ValueError("actor required")
    requested_limit, candidates = _candidate_search(
        actor=actor_id, query=clean_query, limit=limit,
        resident_ids=resident_ids, client=client,
    )
    if not candidates:
        return {
            "schema": "tfi.resident-discovery.recommendation.v2",
            "query": clean_query,
            "summary": "",
            "fallback": False,
            "fallback_reason": "",
            "recommendation_source": "candidate-search",
            "recommendations": [],
            "fork_advice": {"recommended": False, "source_resident_id": "", "reason": "", "suggested_title": ""},
        }
    try:
        binding = _available_recommendation_resident_binding(actor_id)
        result = run_resident_turn(
            resident_binding=binding,
            workflow_binding=binding,
            actor=actor_id,
            prompt=_prompt(clean_query, candidates, requested_limit),
            cwd=str(PORTAL_ROOT),
            resident_scope=f"resident-routing:{actor_id}",
            business_ref=f"routing-{secrets.token_hex(12)}",
            metadata={"surface": "resident-discovery"},
        )
        return _normalize(
            clean_query,
            candidates,
            _json_object(str(result.get("message") or "")),
            requested_limit,
        )
    except (RecommendationIntakeClosed, RecommendationPayloadTooLarge, WorkflowPending, WorkflowFailed):
        raise
    except Exception:
        if not allow_fallback:
            raise
        return _fallback_result(clean_query, candidates, requested_limit)
