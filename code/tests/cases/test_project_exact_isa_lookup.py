"""Project identity lookup is independent of discovery search ranking.

:module: resource.service
"""
from types import SimpleNamespace
import pytest
from management_portal.resources import isa
from management_portal.hatg.contracts import InstructionError


def test_project_lookup_uses_exact_id_after_rename(monkeypatch):
    record = {'resource_id': 'TAU_renamed', 'resource_type': 'project', 'title': 'Old title'}
    calls = []
    def resolve(identity, *, actor):
        calls.append((identity, actor))
        return {'record': record}
    monkeypatch.setattr(isa, '_service', lambda: SimpleNamespace(resolve=resolve,
        get=lambda *a, **k: pytest.fail('discovery index may lag after rename'),
        query=lambda **kwargs: pytest.fail('exact lookup cannot use a search page')))
    assert isa._project_record('TAU_renamed', 'reader') == record
    assert calls == [('TAU_renamed', 'reader')]


def test_other_resource_type_does_not_become_a_project(monkeypatch):
    monkeypatch.setattr(isa, '_service', lambda: SimpleNamespace(resolve=lambda *args, **kwargs: {'record': {'resource_type': 'user'}}))
    with pytest.raises(InstructionError):
        isa._project_record('same-name', 'reader')


def test_deleted_project_is_not_restored_from_stale_discovery(monkeypatch):
    def missing(*a, **k):
        raise KeyError('deleted-project')
    monkeypatch.setattr(isa, '_service', lambda: SimpleNamespace(resolve=missing,
        get=lambda *a, **k: {'resource_type': 'project'}))
    with pytest.raises(InstructionError) as raised:
        isa._project_record('deleted-project', 'reader')
    assert raised.value.code == 'instruction.not_found'
