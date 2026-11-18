from types import SimpleNamespace
import pytest
from management_portal.resources import directory_isa
from management_portal.hatg.contracts import InstructionError
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401


@pytest.mark.parametrize('caller,readable,expected', [
    ('alice', False, ['own']), ('bob', False, None),
    ('bob', True, ['system', 'own']),
])
def test_mixed_cleanup_rows_preserve_initiator_access(monkeypatch, caller, readable, expected):
    from management_portal import permission_system
    monkeypatch.setattr(permission_system, 'allows', lambda *args: False)
    rows = [{'cleanup_id': 'system', 'actor': 'directory-retention'},
            {'cleanup_id': 'own', 'actor': 'alice'}]
    monkeypatch.setattr(directory_isa, 'get_directory_cleanup_store',
                        lambda: SimpleNamespace(status=lambda **_: rows))
    def get(*args, **kwargs):
        if not readable:
            raise KeyError('deleted')
        return {}
    invocation = SimpleNamespace(caller=caller, argument=lambda _: 'paper_fixture')
    if expected is None:
        with pytest.raises(InstructionError):
            directory_isa.cleanup_status(invocation, SimpleNamespace(get=get))
    else:
        result = directory_isa.cleanup_status(invocation, SimpleNamespace(get=get))
        assert [r['cleanup_id'] for r in result['items']] == expected


@pytest.mark.parametrize('authorized', [True, False])
def test_deleted_resource_cleanup_uses_current_explicit_permission(monkeypatch, authorized):
    from management_portal import permission_system

    resource_id = 'article_268936d880b3'
    rows = [{'cleanup_id': 'cleanup_fixture', 'actor': 'original-deleter'}]
    monkeypatch.setattr(directory_isa, 'get_directory_cleanup_store',
                        lambda: SimpleNamespace(status=lambda **_: rows))
    calls = []

    def allows(actor, instruction, arguments):
        calls.append((actor, instruction, arguments))
        return authorized

    monkeypatch.setattr(permission_system, 'allows', allows)
    def gone(*args, **kwargs):
        raise KeyError(resource_id)
    inv = SimpleNamespace(caller='resident_111111111111', argument=lambda _: resource_id)
    if authorized:
        assert directory_isa.cleanup_status(inv, SimpleNamespace(get=gone))['items'] == rows
    else:
        with pytest.raises(InstructionError):
            directory_isa.cleanup_status(inv, SimpleNamespace(get=gone))
    assert calls == [(inv.caller, 'ReadResource', {'address': resource_id})]


def test_cleanup_does_not_override_domain_permission_denial(monkeypatch):
    from management_portal import permission_system
    monkeypatch.setattr(directory_isa, 'get_directory_cleanup_store',
                        lambda: SimpleNamespace(status=lambda **_: [{'actor': 'deleter'}]))
    monkeypatch.setattr(permission_system, 'allows', lambda *args: pytest.fail('domain denial must stand'))
    def denied(*args, **kwargs):
        raise PermissionError('private resource')
    inv = SimpleNamespace(caller='other', argument=lambda _: 'article_111111111111')
    with pytest.raises(InstructionError):
        directory_isa.cleanup_status(inv, SimpleNamespace(get=denied))


def test_deleted_cleanup_observation_respects_real_grant_and_deny(permissions, monkeypatch):
    target, other = 'article_111111111111', 'article_222222222222'
    caller = 'resident_333333333333'
    permissions.place(Fact('lineage', 'lineage_111111111111'),
                      Fact('resident', caller, {'lineage': 'lineage_111111111111'}),
                      Fact('article', target), Fact('article', other))
    rows = [{'cleanup_id': 'fixture', 'actor': 'original-deleter'}]
    monkeypatch.setattr(directory_isa, 'get_directory_cleanup_store',
                        lambda: SimpleNamespace(status=lambda **_: rows))
    def gone(*args, **kwargs):
        raise KeyError('deleted identity')
    def read(resource):
        inv = SimpleNamespace(caller=caller, argument=lambda _: resource)
        return directory_isa.cleanup_status(inv, SimpleNamespace(get=gone))
    # Articles are shared-readable in the default scheme. A specific deny
    # supplies an inaccessible second target; do not assume a missing grant
    # removes the scheme's inherited viewer permission.
    permissions.deny(caller, other, 'ReadResource')
    permissions.grant(caller, target, '@admin')
    assert read(target)['items'] == rows
    with pytest.raises(InstructionError):
        read(other)
    permissions.deny(caller, target, 'ReadResource')
    with pytest.raises(InstructionError):
        read(target)
