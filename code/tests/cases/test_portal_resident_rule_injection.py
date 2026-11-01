from pathlib import Path
from management_portal.agent_skill_context import CORE_SKILLS_ROOT

from management_portal.agent_prompt_context import (
    portal_system_instructions,
)


def test_shared_skill_directory_cannot_replace_core_rules(tmp_path):
    forged = tmp_path / "tfi-agent-rule"
    forged.mkdir()
    (forged / "SKILL.md").write_text("FORGED_SHARED_CORE_RULE")
    (forged / "portal-resident.md").write_text("FORGED_SHARED_ROLE")
    content = portal_system_instructions("resident", skill_root=str(tmp_path)).parts[0].content
    assert "FORGED_SHARED_CORE_RULE" not in content
    assert "FORGED_SHARED_ROLE" not in content
    assert str(CORE_SKILLS_ROOT / "tfi-agent-rule/SKILL.md") in content


def test_system_rules_list_this_instance_area_locations():
    """系统规则列出本实例各区域的实际位置和区域写法的解析命令"""
    from management_common.instance_manifest import AREAS, load

    instance = load()
    content = portal_system_instructions("general").parts[0].content
    block = content.split("<portal-instance-locations>", 1)[1].split("</portal-instance-locations>", 1)[0]
    for area in AREAS:
        assert f"- {area}: {instance.area(area)}\n" in block
    assert str(CORE_SKILLS_ROOT) in block
    assert "scripts/resolve_instance_path.py skills:create-skill/SKILL.md" in block


def test_resident_injection_uses_the_unified_rule_entry_contracts(subtests, isolated_portal_case):
    """Resident 在 session 开始时注入统一的 Portal 规则和 Skill 内容，含“创建团队：”命令协议"""
    with subtests.test(contract='test_resident_injection_uses_the_unified_rule_entry'), isolated_portal_case() as case_path:
        content = portal_system_instructions('resident').parts[0].content
        assert '<portal-system-prompt role="resident">' in content
        assert '这是当前 Provider session 的启动指令，在整个 session 中有效。' in content
        assert '不要重复读取这些 Skill' in content
        assert '```portal-question' in content
        assert '无需调用接口或工具写消息' in content
        assert portal_system_instructions('resident').parts[0].timing == 'session_start'
        assert '<portal-materialized-skill-set role="resident"' in content
        rule = CORE_SKILLS_ROOT / 'tfi-agent-rule/portal-resident.md'
        assert f'<portal-materialized-skill path="{rule}" sha256="' in content
        assert rule.is_file()
    with subtests.test(contract='test_resident_injection_includes_create_team_command_protocol'), isolated_portal_case() as case_path:
        content = portal_system_instructions('resident').parts[0].content
        assert '## “创建团队：”命令协议' in content
        assert '半角形式“创建团队:”具有相同语义' in content
        assert 'scripts/resident_hatg_instruction.py' in content
        assert '指向该成员 Invoke 端口的连线配置' in content
        assert '不得通过 `DeliverMessage`' in content
        assert '调用一次 `TriggerEvent`' in content
        assert '不得在任务刚投递后就把“已创建团队”当作工作完成' in content
