"""Release dependencies use native page bindings without selecting user data."""
from contextlib import nullcontext
from copy import deepcopy

import pytest

from management_portal.distribution import resource_dependencies as dependencies
from management_portal.distribution.features import resolve_features
from management_portal.distribution.store import ReleaseStore

KNOWLEDGE = 'lineage_111111111111'
PAPER = 'lineage_222222222222'
OTHER = 'lineage_333333333333'
APP = 'workbench_444444444444'


class Source:
    def __init__(self):
        self.data = {
            ('lineage', 'application_targets'): [
                {'application': 'resource_chat:knowledge', 'lineage_id': KNOWLEDGE, 'revision': 1},
                {'application': 'resource_chat:skill', 'lineage_id': OTHER, 'revision': 1},
            ],
            ('workbenches', 'apps'): [{'id': APP, 'builtin': 'paper', 'published': 2, 'revision': 3}],
            ('workbenches', 'bindings'): [
                {'app_id': APP, 'revision': 1, 'alias': 'main', 'lineage_id': OTHER},
                {'app_id': APP, 'revision': 2, 'alias': 'main', 'lineage_id': PAPER},
                {'app_id': APP, 'revision': 3, 'alias': 'main', 'lineage_id': OTHER},
            ],
            ('lineage', 'lineages'): [
                {'lineage_id': identity, 'project_id': 'AI员工', 'deleted_at': None}
                for identity in (KNOWLEDGE, PAPER, OTHER)
            ],
        }

    def rows(self, schema, table, *, predicates=None):
        values = deepcopy(self.data.get((schema, table), []))
        if predicates is not None:
            values = [row for row in values if any(row.get(key) in accepted for key, accepted in predicates.items())]
        return values

    def definition_rows(self, _):
        return {}


def selected(*types):
    return {'resource_types': [{'type': kind, 'instances': []} for kind in types], 'system_lineage': {'selected': []}}


def identities(result):
    return {row['id'] for row in result['lineages']}


def test_type_only_uses_registered_owners_and_published_binding():
    snapshot = selected('decision', 'paper')
    before = deepcopy(snapshot)
    result = dependencies.resolve(snapshot, Source())
    assert result['valid'] and identities(result) == {KNOWLEDGE, PAPER}
    assert result['projects'] == [{'id': 'AI员工', 'required_by': sorted([KNOWLEDGE, PAPER])}]
    assert result['projections'] == [{'id': APP, 'revision': 2, 'required_by': ['resource:paper'],
                                     'bindings': [{'alias': 'main', 'lineage_id': PAPER}]}]
    assert result['features'] == ['paper']
    assert snapshot == before  # No instance/history/default/workspace expansion in user choices.


def test_indirect_documents_shared_lineage_deselect_and_manual_retention():
    snapshot = selected('decision')
    snapshot['document_indexes'] = {'SPEC.md': {'enabled': True, 'entries': ['plan_aaaaaaaaaaaa']}}
    result = dependencies.resolve(snapshot, Source())
    assert identities(result) == {KNOWLEDGE}
    assert result['lineages'][0]['required_by'] == ['resource:decision', 'resource:plan']
    assert result['type_reasons']['plan'] == ['document_indexes.SPEC.md:plan_aaaaaaaaaaaa']
    snapshot['resource_types'] = []
    assert identities(dependencies.resolve(snapshot, Source())) == {KNOWLEDGE}
    snapshot['document_indexes']['SPEC.md']['enabled'] = False
    assert not identities(dependencies.resolve(snapshot, Source()))
    snapshot['lineages'] = [{'id': KNOWLEDGE, 'history': True, 'workspace': {'own_outputs': True}}]
    assert dependencies.resolve(snapshot, Source())['lineages'] == [{'id': KNOWLEDGE, 'required_by': ['manual.lineages']}]
    assert snapshot['lineages'][0]['history'] and snapshot['lineages'][0]['workspace']['own_outputs']


def test_other_native_chat_owner_and_indirect_feature_requirements():
    result = dependencies.resolve(selected('skill'), Source())
    assert identities(result) == {KNOWLEDGE, OTHER}
    assert result['application_targets'] == ['resource_chat:knowledge', 'resource_chat:skill']
    snapshot = {'document_indexes': {'IMPROVE.md': {'enabled': True, 'entries': ['paper_aaaaaaaaaaaa']}}}
    assert 'paper' in resolve_features(snapshot)['included_features']
    assert identities(dependencies.resolve(snapshot, Source())) == {PAPER}
    assert identities(dependencies.resolve({}, Source(), additional_ids=['decision_aaaaaaaaaaaa'])) == {KNOWLEDGE}


def test_published_projection_project_membership_is_included():
    source = Source()
    source.data['workbenches', 'projects'] = [
        {'app_id': APP, 'revision': 2, 'project_id': 'Paper owners'},
        {'app_id': APP, 'revision': 3, 'project_id': 'Unpublished project'},
    ]
    result = dependencies.resolve(selected('paper'), source)
    assert result['projects'] == [{'id': 'AI员工', 'required_by': [PAPER]}, {'id': 'Paper owners', 'required_by': [APP]}]


@pytest.mark.parametrize('missing', ['target', 'lineage', 'projection', 'binding', 'published'])
def test_missing_native_dependencies_are_explicit_gaps(missing):
    source = Source()
    if missing == 'target':
        source.data['lineage', 'application_targets'] = []
    elif missing == 'lineage':
        source.data['lineage', 'lineages'] = []
    elif missing == 'projection':
        source.data['workbenches', 'apps'] = []
    elif missing == 'binding':
        source.data['workbenches', 'bindings'] = []
    else:
        source.data['workbenches', 'apps'][0]['published'] = None
    result = dependencies.resolve(selected('decision', 'paper'), source)
    assert not result['valid'] and result['gaps']


def test_config_copy_new_run_and_retries_preserve_reviewed_scope(monkeypatch):
    from management_portal.distribution import database
    source = Source()
    monkeypatch.setattr(database, 'read_snapshot', lambda: nullcontext(source))
    store = ReleaseStore()
    arguments = dict(actor='dependency-reviewer', name='type-only', snapshot=selected('decision', 'paper'), request_key='save-v54')
    config = store.save_config(**arguments)
    frozen = config['snapshot']['resource_dependencies']
    source.data['workbenches', 'apps'][0]['published'] = 3
    assert store.save_config(**arguments) == config
    run = store.create_run(actor='dependency-reviewer', request_key='run-v54', mode='publish', config_id=config['config_id'])
    assert run['snapshot']['resource_dependencies'] == frozen
    copied = store.save_config(actor='dependency-reviewer', name='copy', snapshot=config['snapshot'])
    assert copied['snapshot']['resource_dependencies']['projections'][0]['revision'] == 3
    assert store.get_config(config['config_id']) == config
    assert store.create_run(actor='dependency-reviewer', request_key='run-v54', mode='publish', config_id=config['config_id'])['snapshot'] == run['snapshot']
    with pytest.raises(ValueError, match='scope differs'):
        dependencies.frozen_or_resolve({**config['snapshot'], 'resource_types': []}, source)
    assert dependencies.frozen_or_resolve(config['snapshot'], source) == frozen


def test_candidate_and_saved_scope_share_the_resolver(monkeypatch):
    from management_portal.distribution import candidates, database
    source = Source()
    monkeypatch.setattr(candidates, 'read_snapshot', lambda: nullcontext(source))
    monkeypatch.setattr(database, 'read_snapshot', lambda: nullcontext(source))
    snapshot = selected('paper')
    result = candidates._candidate('release_features', {'snapshot': snapshot})
    saved = ReleaseStore().save_config(actor='dependency-reviewer', name='candidate', snapshot=snapshot)
    assert result['resolution']['resource_dependencies'] == saved['snapshot']['resource_dependencies']


def test_additional_resolution_cannot_refresh_frozen_page_bindings():
    source = Source()
    snapshot = selected('paper')
    snapshot['resource_dependencies'] = dependencies.resolve(snapshot, source)
    source.data['workbenches', 'apps'][0]['published'] = 3
    result = dependencies.frozen_or_resolve(snapshot, source, additional_ids=['decision_aaaaaaaaaaaa'])
    assert not result['valid']
    assert any(gap['id'] == APP and 'changed frozen' in gap['reason'] for gap in result['gaps'])


def test_project_principal_uses_native_entry_not_title_or_siblings():
    source = Source()
    principal = 'lineage_555555555555'
    source.data['lineage', 'project_lineage_entries'] = [{'project_id': 'AI员工', 'lineage_id': principal}]
    source.data['lineage', 'lineages'].append({'lineage_id': principal, 'project_id': 'AI员工', 'lineage_membership': 'shared'})
    result = dependencies.resolve(selected('decision'), source)
    assert result['valid']
    assert identities(result) == {KNOWLEDGE, principal}
    assert result['project_principals'] == [{'project_id': 'AI员工', 'lineage_id': principal}]
    assert OTHER not in identities(result)
    assert {'from': 'AI员工', 'to': principal, 'relation': 'project_principal'} in result['edges']


def test_project_principal_missing_definition_blocks_but_uninitialized_project_does_not():
    source = Source()
    source.data['lineage', 'application_targets'].append(
        {'application': 'resource_chat:project', 'lineage_id': KNOWLEDGE, 'revision': 1})
    result = dependencies.resolve({'manual_projects': ['Example']}, source)
    assert result['valid'] and result['project_principals'] == []
    source.data['lineage', 'project_lineage_entries'] = [{'project_id': 'Example', 'lineage_id': OTHER}]
    result = dependencies.resolve({'manual_projects': ['Example']}, source)
    assert not result['valid']
    assert OTHER not in identities(result)
    assert 'Project principal' in result['gaps'][0]['reason']


def test_frozen_v3_does_not_gain_project_principal_retroactively():
    snapshot = selected('decision')
    frozen = dependencies.resolve(snapshot, Source())
    frozen['schema'] = 'tfi.release-resource-dependencies.v3'
    frozen.pop('project_principals')
    snapshot['resource_dependencies'] = frozen
    assert dependencies.frozen_or_resolve(snapshot, Source()) == frozen
