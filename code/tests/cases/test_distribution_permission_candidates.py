"""Release preview follows native permission rules and scheme-owned instances."""
from copy import deepcopy

import pytest

from management_portal.distribution.candidates import candidate
from management_portal.distribution.database import read_snapshot
from management_portal.distribution.permissions import selected_permissions
from management_portal.permission_system.engine.store import Store
from management_portal.permission_system.engine.model import Entry
from management_portal.permission_system.facts import Fact, Instance, user_fact
from management_portal.permission_system.scheme.repository import Schemes


def setup_schemes(mixed=False):
    store = Store()
    owner = Schemes(store)
    document = owner.get('default')['rules']
    facts = [Fact('project', 'SelectedProject', {}),
             Fact('lineage', 'lineage_111111111111', {'project': 'SelectedProject'}),
             Fact('resident', 'resident_111111111111', {'lineage': 'lineage_111111111111'}),
             user_fact('source-human')]
    Instance(owner.compiled('default'), store).generate(facts)
    if mixed:
        copied = deepcopy(document)
        copied.update(scheme_id='release_custom', title='Custom release rules')
        compiled = owner.create(copied, actor='test')
        Instance(compiled, store).generate(facts)
        with store.scoped('release_custom').transaction() as c:
            store.scoped('release_custom').add(c, [Entry('entry_custom', 'deny', '@users', '@viewer', 'SelectedProject')])
    return owner


@pytest.mark.parametrize('mixed', [False, True])
def test_native_builtin_and_custom_candidates_keep_identity_and_read_only(mixed):
    owner = setup_schemes(mixed)
    before = owner.list()
    active = owner.active()
    found = candidate('ListPermissionSchemes', {})['schemes']
    assert {r['scheme_id'] for r in found} == ({'default', 'release_custom'} if mixed else {'default'})
    assert next(r for r in found if r['scheme_id']=='default')['builtin']
    for item in found:
        identity = item['scheme_id']
        template = candidate('GetPermissionScheme', {'scheme_id': identity})
        assert template['scheme_id'] == template['rules']['scheme_id'] == identity
        assert template['rules'] == owner.get(identity)['rules']
        arguments = {'scheme_id':identity, 'r':'@portal', 'r_scope':'subtree', 'temporary':False, 'limit':1}
        page = candidate('ListPermissionEntries', arguments)
        assert page['scheme_id'] == identity and page['total'] == item['entry_count'] > 1
        entries = page['entries']
        while page.get('next_cursor'):
            page = candidate('ListPermissionEntries', {**arguments, 'cursor':page['next_cursor']})
            entries += page['entries']
        assert len(entries) == item['entry_count']
        assert ('entry_custom' in {r['entry_id'] for r in entries}) is (identity=='release_custom')
        assert all(not any(r['allows'].values()) for r in entries)
    with pytest.raises(KeyError):
        candidate('GetPermissionScheme', {'scheme_id':'missing_scheme'})
    assert owner.active() == active and owner.list() == before


def test_export_builtin_instance_preserves_rule_nodes_and_excludes_other_scheme():
    owner = setup_schemes(True)
    store = owner.store.scoped('default')
    with store.transaction() as c:
        store.add(c, [Entry('entry_selected', 'deny', 'resident_111111111111', '@viewer', 'SelectedProject'),
                      Entry('entry_human', 'permit', 'source-human', '@admin', 'SelectedProject')])
    selected = {'SelectedProject','lineage_111111111111','resident_111111111111'}
    with read_snapshot() as source:
        template_only = selected_permissions(source, [{'id':'default','template':True}], selected)
        exported = selected_permissions(source, [{'id':'default','instance':True}], selected)
        custom = selected_permissions(source, [{'id':'release_custom','instance':True}], selected)
    assert not template_only['schemes'] and not template_only['scheme_entries']
    assert not exported['schemes']  # builtin/default.json is the template authority
    assert all(r['scheme_id']=='default' for values in exported.values() for r in values)
    ids = {r['entry_id'] for r in exported['scheme_entries']}
    assert 'entry_selected' in ids and 'entry_human' not in ids and 'entry_custom' not in ids
    assert next(r for r in exported['scheme_entries'] if r['entry_id']=='entry_selected')['effect']=='deny'
    assert custom['schemes'][0]['document']['scheme_id']=='release_custom'
    assert any(r['entry_id']=='entry_custom' for r in custom['scheme_entries'])
    assert 'active_scheme' not in exported
