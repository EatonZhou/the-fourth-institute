"""The TAU report contract: observations describe use, never evidence gates."""
from typing import Any

PROCESS_STATES = frozenset({"completed", "blocked", "not_started"})
ISSUE_CATEGORIES = frozenset({"product", "case", "tool", "execution"})
SEVERITIES = frozenset({"blocking", "impact", "minor"})


def _items(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def usage_report(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("TAU 使用报告必须是对象")
    process = payload.get("process")
    if not isinstance(process, dict) or process.get("status") not in PROCESS_STATES:
        raise ValueError("TAU 使用报告须说明流程 completed、blocked 或 not_started")
    issues = _items(payload.get("issues"))
    if not isinstance(issues, list) or any(
        not isinstance(item, dict) or item.get("category") not in ISSUE_CATEGORIES
        or item.get("severity") not in SEVERITIES or not item.get("symptom")
        for item in issues
    ):
        raise ValueError("TAU 问题须包含 category、severity 与 symptom")
    return {
        "status": "reported",
        "summary": str(payload.get("summary") or "").strip(),
        "process": {"status": process["status"], "steps": _items(process.get("steps")),
                    "stopped_at": str(process.get("stopped_at") or ""),
                    "reason": str(process.get("reason") or "")},
        "issues": [{**item, "step": str(item.get("step") or "未记录"),
                    "evidence": _items(item.get("evidence"))} for item in issues],
        "performance": _items(payload.get("performance")),
        "evidence": [str(item) for item in _items(payload.get("evidence"))],
        "evidence_gaps": [str(item) for item in _items(payload.get("evidence_gaps"))],
        "diagnosis": payload.get("diagnosis") or {},
    }


def report_counts(results: list[dict[str, Any]]) -> dict[str, Any]:
    reports = [item.get("details") or {} for item in results if (item.get("details") or {}).get("process")]
    issues = [issue for report in reports for issue in _items(report.get("issues")) if isinstance(issue, dict)]
    return {
        "reported": len(reports),
        "process": {state: sum(report.get("process", {}).get("status") == state for report in reports)
                    for state in sorted(PROCESS_STATES)},
        "issues": {category: sum(issue.get("category") == category for issue in issues)
                   for category in sorted(ISSUE_CATEGORIES)},
        "product_severity": {severity: sum(issue.get("category") == "product" and issue.get("severity") == severity
                                            for issue in issues) for severity in sorted(SEVERITIES)},
    }


def interrupted_report(status: str, details: dict[str, Any], *, started: bool | None = None) -> dict[str, Any]:
    """Describe an uncompleted run without guessing a product defect."""
    if details.get("process"):
        return details
    summary = str(details.get("summary") or "本次运行未交付使用报告")
    original_notes = details.get("issues") or []
    issues = [{"category": "tool", "severity": "blocking", "step": "运行",
               "symptom": str(note), "evidence": []} for note in original_notes]
    if status not in {"cancelled", "unsupported"} and not issues:
        issues.append({"category": "tool", "severity": "blocking", "step": "运行",
                       "symptom": summary, "evidence": []})
    report = usage_report({
        "summary": summary,
        "process": {"status": "not_started" if started is False or status == "unsupported" else "blocked",
                    "steps": [], "stopped_at": "未记录", "reason": summary},
        "issues": issues, "performance": [], "evidence": details.get("evidence") or [],
        "evidence_gaps": ["完整过程、截图和关键步骤耗时未记录；可查阅本次原始输出"],
    })
    report.pop("status")
    return {**details, **report}
