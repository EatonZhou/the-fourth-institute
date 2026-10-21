from __future__ import annotations

import pytest

from management_portal.society_service.resident_runtime_payload import runtime_settings_updates_from_payload


def test_runtime_settings_creation_ignores_blank_values_but_update_can_clear() -> None:
    """创建时运行设置中的空值被忽略，更新时空值用于清除对应设置"""
    payload = {
        "model": "  ",
        "permission_preset": "",
        "prompt_injection": {"preset": "custom", "parts": {"runtime_identity": "off"}},
    }
    custom = {"preset": "custom", "parts": {
        "core_rules": "session_start_and_after_compaction", "resident_md": "session_start_and_after_compaction",
        "runtime_identity": "off", "authorization_context": "every_turn", "message_envelope": "every_turn",
        "turn_skills": "list",
    }}
    assert runtime_settings_updates_from_payload(payload) == {
        "prompt_injection": custom,
    }
    assert runtime_settings_updates_from_payload(payload, include_blank=True) == {
        "model": "",
        "permission_preset": "",
        "permissions": "",
        "approvals_reviewer": "",
        "approval_policy": "",
        "sandbox": "",
        "profile": "",
        "prompt_injection": custom,
    }


def test_runtime_settings_rejects_unsupported_collaboration_mode() -> None:
    """运行设置拒绝不支持的 collaboration_mode"""
    with pytest.raises(ValueError, match="collaboration_mode"):
        runtime_settings_updates_from_payload({"collaboration_mode": "observe"})


@pytest.mark.parametrize("payload", [
    [], {"extra": {}}, {"agent_harness": []},
    {"prompt_injection": {"preset": "loud"}},
])
def test_runtime_settings_rejects_unknown_fields_and_invalid_shapes(payload) -> None:
    """运行设置拒绝未知字段、非对象 payload 和无效的 prompt_injection 预设

    :module: lineage.service"""
    with pytest.raises(ValueError):
        runtime_settings_updates_from_payload(payload)
