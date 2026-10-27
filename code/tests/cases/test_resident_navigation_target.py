from __future__ import annotations

import pytest

from management_portal.society_service import resident_domain, resident_lineage, resident_record_domain


from management_portal.society_service import resident_workspace_domain


def test_navigation_target_identity_read_does_not_initialize_agent(monkeypatch, isolated_portal_case):
    """导航解析 Resident 身份时只读取 Resident 与 Lineage ID，不初始化 Agent 数据"""
    from management_portal.society_service import resident_lifecycle_domain

    with isolated_portal_case():
        record = {
            'resident_id': 'resident_aaaaaaaaaaaa',
            'lineage_id': 'lineage_bbbbbbbbbbbb',
            'registration_state': 'ready',
        }
        monkeypatch.setattr(resident_record_domain, 'find_resident', lambda _id: record)
        monkeypatch.setattr(resident_record_domain, '_resident_visible_to_actor', lambda *_args: True)
        monkeypatch.setattr(resident_record_domain, 'ensure_resident_agent',
                            lambda _record: pytest.fail('navigation must not initialize Agent data'))
        assert resident_workspace_domain.resolve_resident_identity(record['resident_id'], 'sample_admin') == {
            'resident_id': record['resident_id'], 'lineage_id': record['lineage_id'],
        }

from management_portal.society_service import resident_workspace_domain

def test_resolve_resident_identity_uses_chat_protocol_identity_contracts(monkeypatch, subtests, isolated_portal_case):
    """解析 Resident 身份返回其 Lineage，不存在或无权读取时按不存在处理，未登录时报权限错误"""
    with subtests.test(contract='test_resolve_resident_identity_uses_chat_protocol_identity'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record = {'resident_id': 'resident_branch', 'lineage_id': 'lineage_live', 'source_resident_id': 'resident_root'}
            calls: list[tuple[str, str, bool]] = []
            monkeypatch.setattr(resident_record_domain, 'resolve_resident_runtime_target', lambda resident_id, actor, *, require_visible: calls.append((resident_id, actor, require_visible)) or resident_domain.ResidentRuntimeTarget(resident_id, record))
            monkeypatch.setattr(resident_lineage, 'lineage_id', lambda value: value['lineage_id'])
            assert resident_domain.resolve_resident_identity('resident_branch', 'sample_admin') == {'resident_id': 'resident_branch', 'lineage_id': 'lineage_live'}
            assert calls == [('resident_branch', 'sample_admin', True)]
    with subtests.test(contract='test_resolve_resident_identity_rejects_missing_resident'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            def missing(*_args, **_kwargs):
                raise KeyError('resident not found')
            monkeypatch.setattr(resident_record_domain, 'resolve_resident_runtime_target', missing)
            with pytest.raises(KeyError, match='resident not found'):
                resident_domain.resolve_resident_identity('resident_deleted', 'sample_admin')
    with subtests.test(contract='test_resolve_resident_identity_hides_an_unreadable_resident'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            resident_id = 'resident_private'
            monkeypatch.setattr(resident_record_domain, 'find_resident', lambda value: {'resident_id': value})
            monkeypatch.setattr(resident_record_domain, '_resident_visible_to_actor', lambda _record, _actor: False)
            # Answered like a missing Resident, as denied ISA reads are; only a
            # caller who is not signed in hears that it must sign in.
            with pytest.raises(KeyError, match='resident not found'):
                resident_domain.resolve_resident_identity(resident_id, 'sample_admin')
            with pytest.raises(PermissionError):
                resident_domain.resolve_resident_identity(resident_id, '')
