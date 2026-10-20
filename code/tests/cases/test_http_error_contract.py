from __future__ import annotations

from management_common.operation_errors import OperationError
from management_portal.http_errors import exception_http_error, normalize_json_error


def test_http_error_envelope_preserves_public_message_and_explicit_receipt() -> None:
    """HTTP 错误信封保留公开错误信息以及显式给出的错误码和提交状态"""
    assert normalize_json_error(403, {"error": "access denied"}) == {
        "error": "access denied",
        "code": "operation.forbidden",
        "message": "access denied",
        "http_status": 403,
        "retryable": False,
        "commit_state": "not_committed",
        "details": {},
    }
    assert normalize_json_error(200, {"error": "application data"}) == {"error": "application data"}
    error = OperationError("projection pending", code="projection.pending", http_status=503,
                           commit_state="committed", details={"domain_committed": True})
    status, payload = exception_http_error(error)
    assert status == 503
    assert payload["code"] == "projection.pending"
    assert payload["commit_state"] == "committed"
    assert payload["error"] == "projection pending"


def test_unexpected_http_error_hides_internal_details(caplog) -> None:
    """意外异常的 HTTP 错误只返回通用信息，日志记录异常类型但不记录异常内容"""
    try:
        raise RuntimeError("database password leaked")
    except RuntimeError as error:
        status, payload = exception_http_error(error)
    assert status == 500
    assert payload["error"] == "Portal request failed"
    assert payload["details"] == {}
    assert "RuntimeError" in caplog.text
    assert "test_unexpected_http_error_hides_internal_details" in caplog.text
    assert "database password leaked" not in caplog.text
