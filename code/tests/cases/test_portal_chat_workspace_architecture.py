from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from management_portal import (
    resident_chat_projection,
    resident_handoff,
    resident_workspace_adapter,
)


ROOT = Path(__file__).resolve().parents[2]


class _ResidentService:
    def __init__(self) -> None:
        self.delivered: list[tuple[dict, str]] = []

    def execute_portal_payload(self, payload: dict, *, actor: str) -> dict:
        self.delivered.append((payload, actor))
        return {
            "resident_id": "resident_workspace01",
            "message": payload["arguments"]["message"],
        }


def _service(monkeypatch: pytest.MonkeyPatch) -> _ResidentService:
    service = _ResidentService()
    service.invocation_requests = []
    monkeypatch.setattr(
        resident_handoff,
        "get_society_service",
        lambda: SimpleNamespace(hatg=service),
    )
    # Embedded discussions resolve their Resident through discussion_residents.resolve.
    def resolve(*, create=False, **options):
        if create:
            service.invocation_requests.append(options)
        return {"resident_id": "resident_workspace01", "lineage_id": "lineage_workspace01"}

    monkeypatch.setattr(resident_workspace_adapter.discussion_residents, "resolve", resolve)
    return service


def test_workspace_identity_is_stable_and_actor_scoped_contracts(subtests, isolated_portal_case):
    """Chat Workspace 身份按用户和界面稳定生成，投影隐藏 Provider 会话 ID，无旧 REST 入口"""
    with subtests.test(contract='test_workspace_identity_is_stable_and_actor_scoped'), isolated_portal_case() as case_path:
        assert resident_workspace_adapter._workspace_identity('CASE-A', 'paper', 'paper:1') == {'resident_scope': 'surface-chat:CASE-A:paper:paper:1', 'lineage_scope': 'surface-chat:CASE-A:paper'}
    # A Project has no per-user project Chat Workspace (its conversation is the
    # Project Resident, decision_46c37026e8ea); Paper keeps a canonical key.
    with subtests.test(contract='test_paper_workspace_rejects_a_mismatched_canonical_key'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match='workspace_key'):
            resident_workspace_adapter._context_for('paper', 'paper:wrong', '', {'paper_id': 'paper_right'})
    with subtests.test(contract='test_portal_projects_resident_chat_from_neutral_session_facts'), isolated_portal_case() as case_path:
        detail = resident_chat_projection.project_session({'schema': 'tfi.resident-session-projection.v1', 'resident_id': 'resident_bbbbbbbbbbbb', 'resident': {'resident_id': 'resident_bbbbbbbbbbbb', 'title': 'Portal Chat', 'lineage_id': 'lineage_000000000001', 'backend': 'codex', 'source_resident_id': 'resident_aaaaaaaaaaaa'}, 'lineage': {'title': 'Portal Engineering Lineage'}, 'provider_session': {'provider': 'codex', 'id': '019f0000-0000-7000-8000-000000000002'}, 'account_selection': {'source_account_id': 'one'}, 'backend_capabilities': {'backend': 'codex', 'image_input': True, 'context_input': True, 'session_restart': True, 'runtime_settings': ['model', 'reasoning_effort']}, 'runtime_settings': {'model': 'gpt-5.6-sol', 'reasoning_effort': 'high'}, 'allows': {'RestartSession': True, 'DeliverMessage': True}}, 'CASE-A')
        contract = detail['chat_workspace']
        assert contract['schema'] == 'portal.chat_workspace.v3'
        assert contract['target'] == {'kind': 'resident', 'id': 'resident_bbbbbbbbbbbb', 'lineage_id': 'lineage_000000000001'}
        assert contract['title'] == 'Chat Workspace - Portal Engineering Lineage - Portal Chat'
        assert all(part.get('identity') != 'session' for part in contract['header']['subtitle_parts'])
        assert '019f0000-0000-7000-8000-000000000002' not in contract['subtitle']
        assert detail['provider_session']['id'] == '019f0000-0000-7000-8000-000000000002'
        assert contract['composer']['placeholder'] == 'Ctrl/⌘ + Enter 发送；右键消息可引用、转发；/portal-help 查看指令'
        assert contract['composer']['meta'] == 'Ctrl/⌘ + Enter 发送；右键消息可引用、转发；/portal-help 查看指令'
        assert contract['composer']['runtime_settings']['model'] == 'gpt-5.6-sol'
        assert contract['actions']['send']['instruction'] == 'DeliverMessage'
        assert contract['actions']['send']['arguments']['resident_id'] == 'resident_bbbbbbbbbbbb'
        assert contract['messages_ref']['resident_id'] == 'resident_bbbbbbbbbbbb'
    with subtests.test(contract='test_resident_conversation_has_no_legacy_rest_entrypoint'), isolated_portal_case() as case_path:
        forbidden = {'/api/agent-workbench/residents/snapshot', '/api/agent-workbench/residents/conversation/messages', '/api/agent-workbench/residents/events', '/api/agent-workbench/residents/search-messages', '/api/agent-workbench/residents/input-state', '/api/agent-workbench/residents/runtime-settings', '/api/agent-workbench/residents/session-restart', '/api/agent-workbench/residents/state', '/api/agent-workbench/residents/input', '/api/agent-workbench/residents/actual-sent-prompts', '/api/agent-workbench/residents/reset-context', '/api/roles/nodes/state'}
        sources = [path for path in (ROOT / 'management_portal').rglob('*') if path.is_file() and path.suffix in {'.py', '.js', '.html', '.json'}]
        occurrences = {}
        for path in sources:
            source = path.read_text(encoding='utf-8')
            for endpoint in forbidden:
                if re.search(re.escape(endpoint) + '(?![-\\w/])', source):
                    occurrences[endpoint] = str(path.relative_to(ROOT))
        assert occurrences == {}


def test_paper_chat_resolves_identity_through_native_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    """论文 Chat 通过原生投影按用户和论文解析 Resident 身份"""
    from management_portal.workbench_projections import native
    observed = {}

    def ensure(name, **options):
        observed.update(name=name, options=options)
        return {"resident_id": "resident_workspace01", "lineage_id": "lineage_workspace01"}

    monkeypatch.setattr(native, "ensure", ensure)
    identity = resident_workspace_adapter._ensure("CASE-A", "paper", "paper:1", {"title": "Paper chat"})
    assert identity["lineage_id"] == "lineage_workspace01"
    assert observed["name"] == "paper"
    assert observed["options"]["actor"] == "CASE-A"
    assert observed["options"]["resident_scope"] == "surface-chat:CASE-A:paper:paper:1"


def test_workspace_projects_the_canonical_resident_chat_contracts(monkeypatch, subtests, isolated_portal_case):
    """Chat Workspace 投影规范的 Resident 对话，不直接内嵌消息列表"""
    with subtests.test(contract='test_workspace_projects_the_canonical_resident_chat'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            _service(monkeypatch)
            workspace = resident_workspace_adapter.get_workspace('CASE-A', 'default', 'main', {'title': 'Workspace'})
            assert workspace['resident_id'] == 'resident_workspace01'
            assert workspace['lineage_id'] == 'lineage_workspace01'
            assert 'messages' not in workspace
            assert workspace['chat_workspace']['target']['id'] == 'resident_workspace01'
            assert workspace['chat_workspace']['target']['lineage_id'] == 'lineage_workspace01'
            assert workspace['chat_workspace']['messages'] == []
