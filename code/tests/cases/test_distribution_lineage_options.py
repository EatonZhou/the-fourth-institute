"""Automatic Lineage preferences change content, never membership."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.distribution import database, resource_dependencies as dependencies
from management_portal.distribution.features import normalize_snapshot, resolve_features, selection_snapshot
from management_portal.distribution.lineage_options import effective_lineages
from management_portal.distribution.selection import SYSTEM, resolve
from management_portal.distribution.store import ReleaseStore
from tests.cases.test_distribution_resource_dependencies import KNOWLEDGE, OTHER, Source, selected

RESIDENT = 'resident_aaaaaaaaaaaa'
OWN = 'workspace_aaaaaaaaaaaa'
EXTRA = 'workspace_bbbbbbbbbbbb'


class ExportSource(Source):
    def __init__(self):
        super().__init__()
        self.definition = {
            ('lineage', 'lineages'): self.data['lineage', 'lineages'],
            ('lineage', 'lineage_nodes'): [{'lineage_id': KNOWLEDGE, 'node_id': RESIDENT, 'node_type': 'resident'}],
            ('lineage', 'residents'): [{'resident_id': RESIDENT, 'title': 'member', 'owned_workspace_id': OWN}],
            ('lineage', 'resident_workspace_bindings'): [{'resident_id': RESIDENT, 'workspace_id': EXTRA}],
        }
        self.data['workspace', 'workspaces'] = [{'workspace_id': OWN, 'path': '/owned'}, {'workspace_id': EXTRA, 'path': '/extra'}]
        self.tables = {key: SimpleNamespace(columns={}) for key in [*self.definition, *self.data, ('lineage', 'conversation_events')]}
        self.history = None

    def read_index(self):
        return {key: {'resource_type': 'lineage'} for key in (KNOWLEDGE, OTHER, SYSTEM)}

    def definition_rows(self, lineages):
        if KNOWLEDGE in lineages:
            return deepcopy(self.definition)
        return {}

    def historical_rows(self, residents, lineages):
        self.history = (set(residents), set(lineages))
        return {('lineage', 'conversation_events'): [{'resident_id': RESIDENT}] if RESIDENT in residents else []}


def test_export_keeps_schema_but_never_source_runtime_metadata():
    source = ExportSource()
    for key in (('lineage', 'lineage_meta'), ('agent', 'agent_store_meta'),
                ('resource_registry', 'registry_meta')):
        source.tables[key] = SimpleNamespace(columns={})
        source.data[key] = [
            {'key': 'schema', 'value': 'current-storage-version'},
            {'key': 'lineage_timer_lease', 'value': '{"holder":"source-host:123"}'},
            {'key': 'runtime_normalization', 'value': '42'},
            {'key': 'future_runtime_marker', 'value': 'source-only'},
        ]
    before = deepcopy(source.data)
    result = resolve(selected('decision'), source)
    for key in source.data:
        if key[1].endswith('_meta'):
            assert result['tables'][key] == [{'key': 'schema', 'value': 'current-storage-version'}]
    assert source.data == before


def test_preferences_activate_only_with_dependency_and_explicit_options_win():
    snapshot = selected('decision')
    prefs = {KNOWLEDGE: {'history': True, 'workspace': {'own_outputs': True, 'extra_bindings': 'all'}}}
    snapshot['resource_lineage_options'] = prefs
    normalized = normalize_snapshot(snapshot)
    assert selection_snapshot(normalized, resolve_features(normalized))['resource_lineage_options'] == prefs
    source = ExportSource()
    exported = resolve(normalized, source)
    assert not exported['unresolved']
    assert source.history == ({RESIDENT}, {KNOWLEDGE})
    assert {item['source'] for item in exported['files']} == {'/owned', '/extra'}
    assert exported['lineage_options'][KNOWLEDGE]['workspace'] == prefs[KNOWLEDGE]['workspace']
    snapshot['lineages'] = [{'id': KNOWLEDGE, 'history': False, 'workspace': {'own_outputs': False, 'extra_bindings': 'exclude'}}]
    explicit = resolve(snapshot, source)
    assert not explicit['unresolved']
    assert source.history == (set(), set())
    assert explicit['files'] == []
    assert len(explicit['tables']['workspace', 'workspaces']) == 1  # Base owned Workspace survives.
    snapshot.pop('lineages')
    snapshot['resource_types'] = []
    assert effective_lineages(snapshot, dependencies.resolve(snapshot, source)) == {}
    assert KNOWLEDGE not in resolve(snapshot, source)['selected_ids']
    assert snapshot['resource_lineage_options'] == prefs


@pytest.mark.parametrize('own_outputs', [False, True])
def test_fresh_progress_uses_owned_workspace_binding_even_when_outputs_selected(monkeypatch, own_outputs):
    from management_portal.distribution import projection_dependencies
    source = ExportSource()
    index = source.read_index()
    monkeypatch.setattr(source, 'read_index', lambda: {**index, OWN: {'resource_type': 'workspace'}})
    snapshot = selected('decision')
    snapshot['resource_lineage_options'] = {KNOWLEDGE: {
        'history': False, 'workspace': {'own_outputs': own_outputs, 'extra_bindings': 'exclude'}}}
    frozen = dependencies.resolve(snapshot, source)
    frozen['projection_resources'] = []
    monkeypatch.setattr(dependencies, 'frozen_or_resolve', lambda *a, **kw: frozen)
    monkeypatch.setattr(projection_dependencies, 'declarations', lambda *a: [])
    prefix = 'data/share_root/ProjectionEvidence/' + OWN
    scope = {'files': [], 'path_mappings': [('/owned', '${share_root}/ProjectionEvidence/' + OWN)],
             'workspaces': {OWN: {'workspace_id': OWN, 'path': '${share_root}/ProjectionEvidence/' + OWN}},
             'repositories': {}, 'reset_roots': ['/owned'],
             'generated_files': {prefix + '/reports/stage-index.json': '{"report_refs": []}'}}
    monkeypatch.setattr(projection_dependencies, 'resolve_files', lambda *a: deepcopy(scope))
    result = resolve(snapshot, source)
    assert not result['unresolved']
    actual = f'data/share_root/Agents/{KNOWLEDGE}/residents/{RESIDENT}/workspace/reports/stage-index.json'
    assert result['generated_files'][actual] == '{"report_refs": []}'
    assert not any(key.startswith(prefix) for key in result['generated_files'])
    assert result['file_scope_policy']['reset_roots'] == ['/owned']
    assert len(result['tables']['workspace', 'workspaces']) == 1


def test_unused_scope_text_does_not_block_but_active_scope_requires_resolution():
    snapshot = selected()
    snapshot['resource_lineage_options'] = {KNOWLEDGE: {'workspace': {'extra_scope': 'only notes', 'exclusions': 'private'}}}
    source = ExportSource()
    assert not resolve(snapshot, source)['unresolved']
    snapshot['resource_types'] = [{'type': 'decision', 'instances': []}]
    unresolved = resolve(snapshot, source)['unresolved']
    assert {item['path'] for item in unresolved} == {f'resource_lineage_options.{KNOWLEDGE}.workspace.{field}' for field in ('extra_scope', 'exclusions')}
    snapshot['lineages'] = [{'id': KNOWLEDGE, 'history': False, 'workspace': {'own_outputs': False, 'extra_bindings': 'exclude'}}]
    assert not resolve(snapshot, source)['unresolved']


def test_unset_survives_save_and_blocks_only_effective_members(monkeypatch):
    source = ExportSource()
    monkeypatch.setattr(database, 'read_snapshot', lambda: nullcontext(source))
    snapshot = selected('decision')
    snapshot['resource_lineage_options'] = {KNOWLEDGE: {'workspace': {'extra_bindings': 'unset'}}}
    store = ReleaseStore()
    saved = store.save_config(actor='scope-reviewer', name='unconfirmed', snapshot=snapshot)
    assert store.get_config(saved['config_id'])['snapshot']['resource_lineage_options'] == snapshot['resource_lineage_options']
    for bindings in (source.definition['lineage', 'resident_workspace_bindings'], []):
        source.definition['lineage', 'resident_workspace_bindings'] = bindings
        result = resolve(saved['snapshot'], source)
        assert result['lineage_options'][KNOWLEDGE]['workspace']['extra_bindings'] == 'unset'
        assert result['unresolved'] == [{'id': KNOWLEDGE, 'reason': 'Workspace extra binding scope is unset'}]
        assert result['tables']['lineage', 'resident_workspace_bindings'] == []
    snapshot['resource_types'] = []
    assert not resolve(snapshot, source)['unresolved']
    assert snapshot['resource_lineage_options'][KNOWLEDGE]['workspace']['extra_bindings'] == 'unset'


@pytest.mark.parametrize('value', [[], {KNOWLEDGE: {'history': 'true'}}, {KNOWLEDGE: {'workspace': {'own_outputs': None}}},
    {KNOWLEDGE: {'workspace': {'extra_bindings': 'unknown'}}}, {KNOWLEDGE: {'workspace': {'exclusions': []}}}])
def test_invalid_preferences_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_snapshot({'resource_lineage_options': value})


def test_projects_page_and_manual_projects_select_page_without_project_chat_target():
    # Per-user project chat was removed (c3a8cc2): the project conversation is
    # the Project Resident, so a leftover resource_chat:project row pulls nothing.
    source = Source()
    source.data['lineage', 'application_targets'].append({'application': 'resource_chat:project', 'lineage_id': OTHER, 'revision': 4})
    for snapshot in ({'projects_page': True}, {'feature_states': {'projects': {'included': True, 'default_enabled': True}}},
                     {'manual_projects': ['chosen']}, {'projects': [{'id': 'chosen'}]}):
        result = dependencies.resolve(snapshot, source)
        assert result['valid']
        assert result['types'] == ['project']
        assert [page['id'] for page in result['pages']] == ['resource:project']
        assert result['application_targets'] == []
        assert result['lineages'] == []
    result = dependencies.resolve({'projects_page': True, 'feature_states': {'projects': {'included': False}}}, source)
    assert not result['types'] and not result['pages']  # Canonical map overrides stale legacy boolean.


def test_save_copy_and_config_run_freeze_preferences_without_selecting_them(monkeypatch):
    source = Source()
    monkeypatch.setattr(database, 'read_snapshot', lambda: nullcontext(source))
    store = ReleaseStore()
    snapshot = selected('decision')
    snapshot['resource_lineage_options'] = {KNOWLEDGE: {'history': True}, OTHER: {'history': True}}
    config = store.save_config(actor='scope-reviewer', name='preferences', snapshot=snapshot)
    run = store.create_run(actor='scope-reviewer', request_key='preference-run', mode='publish', config_id=config['config_id'])
    assert run['snapshot']['resource_lineage_options'] == snapshot['resource_lineage_options']
    assert [item['id'] for item in run['snapshot']['resource_dependencies']['lineages']] == [KNOWLEDGE]
    copied = store.save_config(actor='scope-reviewer', name='copy', snapshot=config['snapshot'])
    assert copied['snapshot']['resource_lineage_options'] == snapshot['resource_lineage_options']
    changed = deepcopy(config['snapshot'])
    changed['resource_lineage_options'][KNOWLEDGE]['history'] = False
    with pytest.raises(ValueError, match='scope differs'):
        dependencies.frozen_or_resolve(changed, source)
    saved = store.save_config(actor='scope-reviewer', name='changed', snapshot=changed, config_id=config['config_id'])
    assert not saved['snapshot']['resource_lineage_options'][KNOWLEDGE]['history']
    assert store.get_run(run['run_id'])['snapshot'] == run['snapshot']


def test_frozen_v2_is_read_without_rewriting_or_adding_projects():
    source = Source()
    snapshot = selected('decision')
    frozen = dependencies.resolve(snapshot, source)
    frozen['schema'] = 'tfi.release-resource-dependencies.v2'
    frozen['input_sha256'] = dependencies.input_digest(snapshot, version=2)
    snapshot.update(resource_dependencies=frozen, projects_page=True)
    assert dependencies.frozen_or_resolve(snapshot, source) == frozen
