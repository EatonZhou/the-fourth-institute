from __future__ import annotations

import re
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

PROJECT_PAGE_SCHEMA_VERSION = "portal.project-page.v2"
SUPPORTED_PROJECT_STATE_SCHEMAS = frozenset({"tfi.project_state.v0.2"})
PROJECT_STATE_SOURCE = Path("project_state") / "PROJECT_STATE.yaml"

_REQUIRED_SECTIONS: dict[str, type] = {
    "project": dict,
    "snapshot": dict,
    "current_attention": list,
    "roadmap": dict,
    "capabilities": list,
    "architecture": dict,
    "decisions": dict,
    "lineage": dict,
    "risks": list,
    "open_questions": list,
    "material_timeline": list,
    "knowledge_health": dict,
    "human_review": dict,
}

_CAPABILITY_STAGES = (
    "decision_state",
    "implementation_state",
    "test_state",
    "release_state",
    "runtime_state",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
            if isinstance(key, (str, int, float, bool))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    clean = str(value).strip()
    return clean or default


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        clean
        for item in value
        if (clean := _text(item))
    ]


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        _json_value(item)
        for item in value
        if isinstance(item, dict)
    ]


def _mapping(value: Any) -> dict[str, Any]:
    return _json_value(value) if isinstance(value, dict) else {}


def _source_path(
    project_root: str | Path,
    source_relative_path: str | Path = PROJECT_STATE_SOURCE,
) -> tuple[Path, Path]:
    root = Path(project_root).expanduser().resolve(strict=False)
    relative = Path(source_relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise PermissionError(
            "Project State source must be a relative path inside the "
            "registered Project root."
        )
    source = (root / relative).resolve(strict=False)
    if not source.is_relative_to(root):
        raise PermissionError(
            "Project State source is outside the registered Project root."
        )
    return root, source


def _empty_contract(
    project_id: str,
    *,
    source_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    try:
        relative_path = source_path.relative_to(project_root).as_posix()
    except ValueError:
        relative_path = PROJECT_STATE_SOURCE.as_posix()
    return {
        "view_schema_version": PROJECT_PAGE_SCHEMA_VERSION,
        "status": "missing",
        "missing_sections": list(_REQUIRED_SECTIONS),
        "source": {
            "status": "missing",
            "code": "missing_file",
            "message": "Project State has not been published for this project.",
            "relative_path": relative_path,
            "schema_version": "",
            "generated_at": "",
            "last_modified_at": "",
        },
        "project": {
            "id": project_id,
            "status": "unknown",
            "current_phase": "unknown",
            "phase_inference": {},
            "objective": "",
            "non_goals": [],
            "project_level_success_criteria": "unknown",
            "registered_deadline": "",
            "deadline_deliverable": "unknown",
        },
        "snapshot": {
            "generated_at": "",
            "knowledge_revision": "",
            "source_coverage": {},
            "blind_spots": [],
        },
        "current_attention": [],
        "roadmap": {
            "confirmed_milestones": [],
            "candidate_milestones": [],
        },
        "capabilities": [],
        "architecture": {
            "workstreams": [],
            "components": [],
            "interfaces": [],
            "dependencies": [],
            "ownership_gaps": [],
        },
        "decisions": {
            "active": [],
            "provisional": [],
            "superseded": [],
            "conflicted": [],
        },
        "lineage": {"summary": "", "residents": []},
        "risks": [],
        "open_questions": [],
        "commitments": [],
        "material_timeline": [],
        "knowledge_health": {},
        "human_review": {"count": 0, "highest_priority": []},
        "brief": {
            "headline": "",
            "current_state": "",
            "progress": "",
            "needs_attention": "",
            "next_move": "",
            "next_move_state": "unknown",
            "deadline": "",
            "deadline_deliverable": "unknown",
            "success_criteria": "unknown",
            "signals": {
                "capabilities": 0,
                "residents": 0,
                "human_review": 0,
                "conflicts": 0,
                "risks": 0,
            },
        },
    }


def _invalid_contract(
    project_id: str,
    *,
    source_path: Path,
    project_root: Path,
    code: str,
    message: str,
    schema_version: str = "",
) -> dict[str, Any]:
    contract = _empty_contract(
        project_id,
        source_path=source_path,
        project_root=project_root,
    )
    contract["status"] = "invalid"
    contract["source"].update(
        {
            "status": "invalid",
            "code": code,
            "message": message,
            "schema_version": schema_version,
        }
    )
    return contract


def _normalize_project(
    value: Any,
    project_id: str,
) -> dict[str, Any]:
    # The registered ID is the Project's only name (decision_4648f7fa3295); a name
    # in the file, or an ID left from before a rename, is not passed on.
    source = {key: item for key, item in _mapping(value).items() if key != "name"}
    phase_inference = _mapping(source.get("phase_inference"))
    return {
        **source,
        "id": project_id,
        "status": _text(source.get("status"), "unknown"),
        "current_phase": _text(source.get("current_phase"), "unknown"),
        "phase_inference": phase_inference,
        "objective": _text(source.get("objective")),
        "non_goals": _string_list(source.get("non_goals")),
        "project_level_success_criteria": _text(
            source.get("project_level_success_criteria"),
            "unknown",
        ),
        "registered_deadline": _text(source.get("registered_deadline")),
        "deadline_deliverable": _text(
            source.get("deadline_deliverable"),
            "unknown",
        ),
    }


def _normalize_snapshot(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    return {
        **source,
        "generated_at": _text(source.get("generated_at")),
        "knowledge_revision": _text(source.get("knowledge_revision")),
        "source_coverage": _mapping(source.get("source_coverage")),
        "blind_spots": _string_list(source.get("blind_spots")),
    }


def _normalize_capabilities(value: Any) -> list[dict[str, Any]]:
    capabilities = []
    for index, item in enumerate(_records(value)):
        capability = {
            **item,
            "id": _text(item.get("id"), f"capability-{index + 1}"),
            "name": _text(item.get("name"), "Unnamed capability"),
            "scope": _string_list(item.get("scope")),
            "last_verified_at": _text(item.get("last_verified_at")),
            "evidence_refs": _string_list(item.get("evidence_refs")),
            "remaining_uncertainty": _string_list(
                item.get("remaining_uncertainty")
            ),
        }
        for stage in _CAPABILITY_STAGES:
            capability[stage] = _text(item.get(stage), "unknown")
        capabilities.append(capability)
    return capabilities


def _normalize_roadmap(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    return {
        **source,
        "confirmed_milestones": _records(
            source.get("confirmed_milestones")
        ),
        "candidate_milestones": _records(
            source.get("candidate_milestones")
        ),
    }


def _normalize_architecture(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    return {
        **source,
        "workstreams": _records(source.get("workstreams")),
        "components": _records(source.get("components")),
        "interfaces": _records(source.get("interfaces")),
        "dependencies": _records(source.get("dependencies")),
        "ownership_gaps": _string_list(source.get("ownership_gaps")),
    }


def _normalize_decisions(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    return {
        **source,
        "active": _records(source.get("active")),
        "provisional": _records(source.get("provisional")),
        "superseded": _records(source.get("superseded")),
        "conflicted": _records(source.get("conflicted")),
    }


def _normalize_lineage(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    return {
        **source,
        "summary": _text(source.get("summary")),
        "residents": _records(source.get("residents")),
    }


def _normalize_human_review(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    priorities = source.get("highest_priority")
    try:
        count = max(0, int(source.get("count") or 0))
    except (TypeError, ValueError):
        count = 0
    return {
        **source,
        "count": count,
        "highest_priority": (
            _records(priorities)
            if isinstance(priorities, list)
            and any(isinstance(item, dict) for item in priorities)
            else _string_list(priorities)
        ),
    }


def _record_text(value: Any, *keys: str) -> str:
    if isinstance(value, str):
        return _text(value)
    if not isinstance(value, dict):
        return ""
    for key in keys:
        if clean := _text(value.get(key)):
            return clean
    return ""


def _first_record_text(value: Any, *keys: str) -> str:
    if not isinstance(value, list):
        return ""
    for item in value:
        if clean := _record_text(item, *keys):
            return clean
    return ""


def _brief_text_similarity(left: str, right: str) -> float:
    def normalize(value: str) -> str:
        return re.sub(
            r"[^\w\u4e00-\u9fff]+",
            "",
            _text(value).lower(),
        )

    left_normalized = normalize(left)
    right_normalized = normalize(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(
        None,
        left_normalized,
        right_normalized,
    ).ratio()


def _first_distinct_record_text(
    value: Any,
    *,
    avoid: str,
    keys: tuple[str, ...],
) -> str:
    if not isinstance(value, list):
        return ""
    first = ""
    for item in value:
        clean = _record_text(item, *keys)
        if not clean:
            continue
        if not first:
            first = clean
        if not avoid or _brief_text_similarity(clean, avoid) < 0.72:
            return clean
    return first


def _build_project_brief(contract: dict[str, Any]) -> dict[str, Any]:
    project = _mapping(contract.get("project"))
    inference = _mapping(project.get("phase_inference"))
    attention = _records(contract.get("current_attention"))
    roadmap = _mapping(contract.get("roadmap"))
    confirmed = _records(roadmap.get("confirmed_milestones"))
    candidates = _records(roadmap.get("candidate_milestones"))
    human_review = _mapping(contract.get("human_review"))
    priorities = human_review.get("highest_priority")
    risks = _records(contract.get("risks"))
    open_questions = _records(contract.get("open_questions"))
    decisions = _mapping(contract.get("decisions"))
    lineage = _mapping(contract.get("lineage"))

    progress = next(
        (
            _record_text(item, "statement", "title")
            for item in attention
            if _text(item.get("type")).lower() == "fact"
            and _record_text(item, "statement", "title")
        ),
        "",
    )
    if not progress:
        progress = _first_record_text(attention, "statement", "title")

    needs_attention = _first_record_text(
        priorities,
        "statement",
        "title",
    )
    if not needs_attention:
        needs_attention = _first_record_text(
            risks,
            "statement",
            "title",
        )
    if not needs_attention:
        needs_attention = _first_record_text(
            open_questions,
            "statement",
            "title",
        )

    next_move = _first_record_text(
        confirmed,
        "title",
        "statement",
    )
    next_move_state = "confirmed" if next_move else "unknown"
    if not next_move:
        next_move = _first_distinct_record_text(
            candidates,
            avoid=needs_attention,
            keys=("title", "statement"),
        )
        if next_move:
            next_move_state = "candidate"

    current_phase = _text(project.get("current_phase"))
    current_state = _text(inference.get("statement"))
    if not current_state and current_phase.lower() not in {"", "unknown"}:
        current_state = current_phase

    try:
        review_count = max(0, int(human_review.get("count") or 0))
    except (TypeError, ValueError):
        review_count = 0

    return {
        "headline": _text(project.get("objective")),
        "current_state": current_state,
        "progress": progress,
        "needs_attention": needs_attention,
        "next_move": next_move,
        "next_move_state": next_move_state,
        "deadline": _text(project.get("registered_deadline")),
        "deadline_deliverable": _text(
            project.get("deadline_deliverable"),
            "unknown",
        ),
        "success_criteria": _text(
            project.get("project_level_success_criteria"),
            "unknown",
        ),
        "signals": {
            "capabilities": len(_records(contract.get("capabilities"))),
            "residents": len(_records(lineage.get("residents"))),
            "human_review": review_count,
            "conflicts": len(_records(decisions.get("conflicted"))),
            "risks": len(risks),
        },
    }


def _last_modified_at(path: Path) -> str:
    try:
        timestamp = path.stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def load_project_state(
    project_id: str,
    project_root: str | Path,
    *,
    source_relative_path: str | Path = PROJECT_STATE_SOURCE,
) -> dict[str, Any]:
    clean_project_id = _text(project_id)
    if not clean_project_id:
        raise ValueError("Missing project id.")
    root, source = _source_path(project_root, source_relative_path)
    contract = _empty_contract(
        clean_project_id,
        source_path=source,
        project_root=root,
    )
    if not source.is_file():
        return contract

    try:
        payload = yaml.safe_load(
            source.read_text(encoding="utf-8", errors="strict")
        )
    except yaml.YAMLError as exc:
        return _invalid_contract(
            clean_project_id,
            source_path=source,
            project_root=root,
            code="invalid_yaml",
            message=f"Project State YAML is invalid: {exc}",
        )
    except (OSError, UnicodeError) as exc:
        contract["status"] = "error"
        contract["source"].update(
            {
                "status": "error",
                "code": "read_error",
                "message": f"Project State could not be read: {exc}",
            }
        )
        return contract

    if not isinstance(payload, dict):
        return _invalid_contract(
            clean_project_id,
            source_path=source,
            project_root=root,
            code="invalid_document",
            message="Project State must be a YAML object.",
        )

    schema_version = _text(payload.get("schema_version"))
    if schema_version not in SUPPORTED_PROJECT_STATE_SCHEMAS:
        return _invalid_contract(
            clean_project_id,
            source_path=source,
            project_root=root,
            code="unsupported_schema",
            message=(
                "Unsupported Project State schema: "
                f"{schema_version or '(missing)'}."
            ),
            schema_version=schema_version,
        )

    source_project = payload.get("project")
    source_project_id = (
        _text(source_project.get("id"))
        if isinstance(source_project, dict)
        else ""
    )
    if source_project_id and source_project_id != clean_project_id:
        return _invalid_contract(
            clean_project_id,
            source_path=source,
            project_root=root,
            code="project_id_mismatch",
            message=(
                "Project State project id does not match the registered "
                f"Project: {source_project_id}."
            ),
            schema_version=schema_version,
        )

    missing_sections = [
        name
        for name, expected_type in _REQUIRED_SECTIONS.items()
        if not isinstance(payload.get(name), expected_type)
    ]
    project = _normalize_project(payload.get("project"), clean_project_id)
    snapshot = _normalize_snapshot(payload.get("snapshot"))
    contract.update(
        {
            "status": "partial" if missing_sections else "ready",
            "missing_sections": missing_sections,
            "project": project,
            "snapshot": snapshot,
            "current_attention": _records(
                payload.get("current_attention")
            ),
            "roadmap": _normalize_roadmap(payload.get("roadmap")),
            "capabilities": _normalize_capabilities(
                payload.get("capabilities")
            ),
            "architecture": _normalize_architecture(
                payload.get("architecture")
            ),
            "decisions": _normalize_decisions(payload.get("decisions")),
            "lineage": _normalize_lineage(payload.get("lineage")),
            "risks": _records(payload.get("risks")),
            "open_questions": _records(payload.get("open_questions")),
            "commitments": _records(payload.get("commitments")),
            "material_timeline": _records(
                payload.get("material_timeline")
            ),
            "knowledge_health": _mapping(
                payload.get("knowledge_health")
            ),
            "human_review": _normalize_human_review(
                payload.get("human_review")
            ),
        }
    )
    contract["brief"] = _build_project_brief(contract)
    contract["source"].update(
        {
            "status": contract["status"],
            "code": "partial_sections" if missing_sections else "ok",
            "message": (
                "Project State is missing sections: "
                + ", ".join(missing_sections)
                if missing_sections
                else ""
            ),
            "schema_version": schema_version,
            "generated_at": _text(snapshot.get("generated_at")),
            "last_modified_at": _last_modified_at(source),
        }
    )
    return contract


__all__ = [
    "PROJECT_PAGE_SCHEMA_VERSION",
    "PROJECT_STATE_SOURCE",
    "SUPPORTED_PROJECT_STATE_SCHEMAS",
    "load_project_state",
]
