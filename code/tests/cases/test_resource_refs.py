from __future__ import annotations

from management_portal.agent_prompt_context import (
    portal_system_instructions,
)
from management_portal import agent_context_selection
from management_portal.resource_refs import (
    extract_resource_addresses,
    resolve_resource_references,
    resource_references_prompt,
)


class _FakeResourceService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def parse_address(self, address: str):
        if address.startswith("unknown_"):
            raise ValueError("unregistered resource type")
        return type("Parsed", (), {"value": address})()

    def reference(self, address: str, *, actor: str = "") -> dict:
        self.calls.append((address, actor))
        if address.startswith("repo_ffffffffffff"):
            raise PermissionError("forbidden")
        root_id = address.partition("/")[0]
        return {
            "address": address,
            "resource_id": root_id,
            "resource_type": root_id.rpartition("_")[0],
            "title": "Resolved resource",
            "summary": "Resolver metadata",
            "status": "resolved",
            "url": f"/r/{address}",
        }


def test_inline_resource_addresses_are_resolved_with_the_current_actor_contracts(subtests, isolated_portal_case):
    """消息正文中的资源地址按当前用户身份解析，命令行和路径里的 ID 不提取"""
    with subtests.test(contract='test_inline_resource_addresses_are_resolved_with_the_current_actor'), isolated_portal_case() as case_path:
        service = _FakeResourceService()
        content = '请比较 repo_0123456789ab/file/src/main.py，以及 article_abcdef012345。repo_0123456789ab/file/src/main.py'
        refs = resolve_resource_references([{'address': 'repo_ffffffffffff'}], content=content, actor='sample_admin', service=service)
        assert [item['address'] for item in refs] == ['repo_ffffffffffff', 'repo_0123456789ab/file/src/main.py', 'article_abcdef012345']
        assert refs[0]['status'] == 'forbidden'
        assert refs[1]['status'] == 'resolved'
        assert service.calls == [('repo_ffffffffffff', 'sample_admin'), ('repo_0123456789ab/file/src/main.py', 'sample_admin'), ('article_abcdef012345', 'sample_admin')]
    with subtests.test(contract='test_resource_reference_prompt_carries_addresses_without_copying_raw_content'), isolated_portal_case() as case_path:
        block = resource_references_prompt({'resources': [{'address': 'article_abcdef012345', 'resource_id': 'article_abcdef012345', 'resource_type': 'article', 'title': 'Portal protocol', 'summary': 'Structured reference', 'status': 'resolved', 'url': '/r/article_abcdef012345'}]})
        assert 'address="article_abcdef012345"' in block
        assert 'portal_actor' not in block
        assert 'resource_id.py' not in block
        assert 'ResourceService.read_content' not in block
        assert '当前 Agent 角色已经提供的资源读取能力' in block
        assert 'raw resolver payload' not in block
    with subtests.test(contract='test_resource_extractor_ignores_noncanonical_codes'), isolated_portal_case() as case_path:
        assert extract_resource_addresses('repo_0123456789ab, repo_0123456789AB and repo_123.') == ['repo_0123456789ab']
    with subtests.test(contract='test_resource_extractor_only_discovers_addresses_in_natural_language'), isolated_portal_case() as case_path:
        content = '\n'.join(['Natural language opens skill_111111111111.', 'python3 /share/Skills/tfi-unified-id/scripts/resource_id.py context resident_f4407a6779d5 --relative-to resident_cb5840e6a610 --actor sample_admin', '', 'Lineage 参考工作区：/srv/agent-staging/shared/portal_lineages/lineages/lineage_4d9182d0fa61/workspaces/workspace_4d9182d0fa61', '', 'service.query(article_222222222222);', '', 'Inline code stays `repo_333333333333`.', '', '```text', 'paper_444444444444', '```', '', 'Existing link stays [device_555555555555](https://example.test/device).', '', 'URL stays https://example.test/resource?id=document_666666666666.'])
        assert extract_resource_addresses(content) == ['skill_111111111111']
    with subtests.test(contract='test_ignored_inline_addresses_are_not_sent_to_resource_service'), isolated_portal_case() as case_path:
        service = _FakeResourceService()
        refs = resolve_resource_references(content='工作区：/srv/portal/lineage_4d9182d0fa61/workspaces/workspace_4d9182d0fa61；请打开 article_abcdef012345。', actor='sample_admin', service=service)
        assert [item['address'] for item in refs] == ['article_abcdef012345']
        assert service.calls == [('article_abcdef012345', 'sample_admin')]
    with subtests.test(contract='test_failed_reference_keeps_the_full_nested_address_as_its_title'), isolated_portal_case() as case_path:
        address = 'unknown_0123456789ab/workspace'
        refs = resolve_resource_references(content=f'请检查 {address}。', actor='sample_admin', service=_FakeResourceService())
        assert refs == [{'address': address, 'resource_id': 'unknown_0123456789ab', 'resource_type': 'unknown', 'title': address, 'summary': '', 'status': 'invalid', 'url': f'/r/{address.replace('/', '%2F')}', 'error': 'unregistered resource type'}]


def test_agent_context_prompt_has_one_capability_neutral_renderer(
    tmp_path,
    monkeypatch,
) -> None:
    """Skill 和资源引用由同一渲染器写入提示词，不暴露主机路径和调用者身份"""
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Example\n\nShared instructions.\n", encoding="utf-8")
    monkeypatch.setattr(agent_context_selection, "HOST_SKILLS_ROOT", tmp_path)

    block = agent_context_selection.prompt_references(
        {
            "skills": [
                {
                    "id": "example",
                    "name": "Example",
                    "path": "/nas_ro/Skills/example",
                }
            ],
            "resources": [
                {
                    "address": "article_abcdef012345",
                    "resource_id": "article_abcdef012345",
                    "resource_type": "article",
                    "title": "Portal protocol",
                    "status": "resolved",
                }
            ],
        },
        item_char_limit=10_000,
        total_char_limit=20_000,
    )

    assert "Shared instructions." in block
    assert 'address="article_abcdef012345"' in block
    assert "host_marker_path" not in block
    assert "portal_actor" not in block
    assert "resource_id.py" not in block


def test_portal_system_prompt_uses_one_skill_entry_and_explicit_role() -> None:
    """Portal 系统提示词只引用一次 tfi-agent-rule Skill 并写明 Resident 角色"""
    prompt = portal_system_instructions("resident").parts[0].content

    assert prompt.count("/share/Skills/tfi-agent-rule/SKILL.md") == 1
    assert 'role="resident"' in prompt
    assert "tfi-agent-portal-review-solver" not in prompt


def test_shared_chat_context_resolver_adds_inline_resources(monkeypatch) -> None:
    """共享对话上下文解析把消息正文里的资源地址加入引用列表"""
    calls = []

    def fake_resolve(raw, *, content="", actor="", service=None):
        calls.append((raw, content, actor))
        return [
            {
                "address": "repo_0123456789ab",
                "resource_id": "repo_0123456789ab",
                "resource_type": "repo",
                "title": "Portal",
                "summary": "",
                "status": "resolved",
                "url": "/r/repo_0123456789ab",
            }
        ]

    monkeypatch.setattr(
        agent_context_selection,
        "resolve_resource_references",
        fake_resolve,
    )

    refs = agent_context_selection.resolve(
        {},
        actor="sample_admin",
        content="检查 repo_0123456789ab",
    )

    assert refs["resources"][0]["address"] == "repo_0123456789ab"
    assert calls == [(None, "检查 repo_0123456789ab", "sample_admin")]
