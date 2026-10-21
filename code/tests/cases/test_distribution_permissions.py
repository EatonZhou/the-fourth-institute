"""Copied permission schemes stay compilable without widening grants."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from management_portal.distribution.permissions import project_scheme
from management_portal.permission_system.scheme import vocabulary
from management_portal.permission_system.scheme.compiler import compile_scheme, declared_decisions
from management_portal.permission_system.scheme.repository import Schemes, installed


@pytest.mark.parametrize('types', [
    {'family', 'lineage', 'personal_space', 'project', 'resident', 'skill', 'user', 'workbench', 'workspace'},
    {'personal_space', 'project', 'user'},
    set(vocabulary.OBJECT_TYPES['R']),
])
def test_actual_default_scheme_compiles_for_selected_registry(monkeypatch, types):
    path = Path(__file__).resolve().parents[2] / 'management_portal/permission_system/scheme/builtin/default.json'
    original = path.read_text()
    document = json.loads(original)
    allowed = {**vocabulary.OBJECT_TYPES, 'R': types}
    copied, evidence = project_scheme(document, allowed)
    monkeypatch.setattr(vocabulary, 'OBJECT_TYPES', allowed)
    compiled = compile_scheme(installed(copied), instructions=declared_decisions())
    assert compiled.user_categories['portal_admin'] == '@portal_admins'
    assert all(entry in document['entries'] for entry in copied['entries'])
    assert next(e for e in copied['entries'] if e['id'] == 'administrators') == next(e for e in document['entries'] if e['id'] == 'administrators')
    for tree, removed in evidence['removed_nodes'].items():
        assert all(n['id'] not in removed for n in copied['trees'][tree]['nodes'])
    for node in copied['trees']['R']['nodes']:
        before = next(n for n in document['trees']['R']['nodes'] if n['id'] == node['id'])
        assert {k:v for k,v in node.items() if k != 'each'} == {k:v for k,v in before.items() if k != 'each'}
        if 'each' in node:
            kinds = [node['each']] if isinstance(node['each'], str) else node['each']
            assert set(kinds) <= types
    assert path.read_text() == original
    assert document == json.loads(original)


def test_retained_child_of_removed_pattern_is_not_reparented():
    source = Schemes(None)._builtin['default']
    allowed = deepcopy(vocabulary.OBJECT_TYPES)
    allowed['R'] = {'resident', 'user', 'project', 'personal_space'}
    with pytest.raises(ValueError, match='placement dependency'):
        project_scheme(source, allowed)
