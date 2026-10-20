from __future__ import annotations

import re
from management_common.agent_data_schema import AGENT_DATA_SCHEMA
from management_common.format_tags import schema_matches
from management_common.portal_database import portal_database


RUNTIME_STATUS_SCHEMA = "tfi.portal-runtime-status.v1"


def _version_label(schema: str) -> str:
    clean = str(schema or "").strip()
    match = re.fullmatch(r"[^.]+\.agent-data\.(v\d+)", clean)
    return (match.group(1) if match and schema_matches(clean, "tfi.agent-data." + match.group(1))
            else clean or "未知")


def _actual_agent_data_schema() -> str:
    database = portal_database(schema="agent")
    with database.read() as connection:
        row = connection.execute(
            "SELECT value FROM agent_store_meta WHERE key = 'schema'"
        ).fetchone()
    return str(row[0] if row else "").strip()


def portal_runtime_status(
    expected_agent_data_schema: str | None = None,
) -> dict:
    """Report the durable schema marker without treating it as a runtime gate."""
    # The marker this code's AgentData store creates and checks.
    expected = str(expected_agent_data_schema or AGENT_DATA_SCHEMA).strip()
    try:
        actual = _actual_agent_data_schema()
    except Exception as exc:
        return {
            "schema": RUNTIME_STATUS_SCHEMA,
            "ok": False,
            "ready": False,
            "state": "unavailable",
            "code": "agent_data_schema_unreadable",
            "terminal": False,
            "retryable": True,
            "title": "数据库状态暂时不可用",
            "message": (
                f"无法读取数据库版本，期望 {_version_label(expected)}。"
                "Portal 将继续重试。"
            ),
            "agent_data": {
                "compatible": False,
                "expected_schema": expected,
                "actual_schema": "",
                "expected_version": _version_label(expected),
                "actual_version": "未知",
            },
            "detail": str(exc),
        }

    expected_match = re.fullmatch(r"[^.]+\.(agent-data\.v\d+)", expected)
    compatible = bool(actual) and (
        schema_matches(actual, "tfi." + expected_match.group(1))
        if expected_match and schema_matches(expected, "tfi." + expected_match.group(1)) else actual == expected
    )
    if not compatible:
        expected_version = _version_label(expected)
        actual_version = _version_label(actual)
        return {
            "schema": RUNTIME_STATUS_SCHEMA,
            "ok": True,
            "ready": True,
            "state": "ready",
            "code": "agent_data_schema_marker_mismatch",
            "terminal": False,
            "retryable": True,
            "title": "Portal 数据库可用",
            "message": (
                f"数据库标记为 {actual_version}，当前运行版本为 {expected_version}。"
                "请核对数据库版本与部署版本。"
            ),
            "agent_data": {
                "compatible": False,
                "expected_schema": expected,
                "actual_schema": actual,
                "expected_version": expected_version,
                "actual_version": actual_version,
            },
        }

    return {
        "schema": RUNTIME_STATUS_SCHEMA,
        "ok": True,
        "ready": True,
        "state": "ready",
        "code": "",
        "terminal": False,
        "retryable": False,
        "title": "Portal 数据库已就绪",
        "message": "AgentData 数据库版本与当前运行契约一致。",
        "agent_data": {
            "compatible": True,
            "expected_schema": expected,
            "actual_schema": actual,
            "expected_version": _version_label(expected),
            "actual_version": _version_label(actual),
        },
    }
