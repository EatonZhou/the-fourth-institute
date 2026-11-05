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
    def get(identity, *, actor):
        calls.append((identity, actor))
        return record
    monkeypatch.setattr(isa, '_service', lambda: SimpleNamespace(get=get,
        query=lambda **kwargs: pytest.fail('exact lookup cannot use a search page')))
    assert isa._project_record('TAU_renamed', 'reader') == record
    assert calls == [('TAU_renamed', 'reader')]


def test_other_resource_type_does_not_become_a_project(monkeypatch):
    monkeypatch.setattr(isa, '_service', lambda: SimpleNamespace(get=lambda *args, **kwargs: {'resource_type': 'user'}))
    with pytest.raises(InstructionError):
        isa._project_record('same-name', 'reader')
