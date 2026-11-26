"""Portable current definitions use the installed ISA without rewriting history.

:module: operations.deployment
:design: decision_898b8911a2ff
:profiles: local.fixture
:risk: no-external-side-effects
"""
from copy import deepcopy
import json
import pytest

from management_portal.distribution.database import DatabaseSnapshot, Table, read_snapshot
from tests.cases.test_lineage_generic_primitives import ACTOR, WORK_A, WORK_B, _worker, primitive_store  # noqa: F401


def test_deferred_snapshot_never_reads_graph_or_structure(monkeypatch):
    from management_portal.society_service import lineage_data_definitions

    source = object.__new__(DatabaseSnapshot)
    # No connection or table inventory: deferral must precede both reads.
    monkeypatch.setattr(lineage_data_definitions, 'definition_from_connection',
                        lambda *_: pytest.fail('deferred graph read'))
    source.rows = lambda *_a, **_k: pytest.fail('deferred structure read')
    assert source.current_definitions({'lineage_7983b047d221'}) == {}
    assert source.definition_rows({'lineage_7983b047d221'}) == {}


def test_definition_export_normalizes_text_and_json_without_mutating_snapshot():
    records = {
        'lineage_nodes': [{'node_id': 'effect', 'lineage_id': 'selected', 'node_type': 'effect'}],
        'lineage_components': [{'node_id': 'effect', 'config_json': {
            'script': 'PreviewPromptInjection resident_id="resident_fixture"',
            'script_draft': 'PreviewPromptInjection resident_id="reviewed"',
        }}],
        'lineage_edges': [{'edge_id': 'wire', 'lineage_id': 'selected', 'config_json': json.dumps({
            'arguments': {'prompt': '先用 PreviewPromptInjection 查看注入内容。'},
            'resource_id': 'PreviewPromptInjection',
        })}],
    }
    original = deepcopy(records)
    source = object.__new__(DatabaseSnapshot)
    source.tables = {('lineage', name): Table('lineage', name,
        {key: 'text' for key in rows[0]}, ('edge_id' if name == 'lineage_edges' else 'node_id',))
        for name, rows in records.items()}
    source.rows = lambda schema, name, **kwargs: deepcopy(records[name])
    source.current_definitions = lambda ids: {'selected': {
        'lineage_id': 'selected', 'function_blocks': [], 'program': {
            'components': [{'id': 'effect', 'kind': 'effect', 'config': deepcopy(records['lineage_components'][0]['config_json'])}],
            'wires': [{'id': 'wire', 'kind': 'route', 'source': 'timer', 'target': 'effect',
                       'config': json.loads(records['lineage_edges'][0]['config_json'])}],
        },
    }}
    selected = []
    read_rows = source.rows
    def checked_rows(schema, name, **kwargs):
        selected.extend(value for values in kwargs.get('predicates', {}).values() for value in values)
        return read_rows(schema, name, **kwargs)
    source.rows = checked_rows
    result = source.definition_rows({'selected', 'lineage_7983b047d221'})
    assert 'lineage_7983b047d221' not in selected
    component = result['lineage', 'lineage_components'][0]['config_json']
    assert component['script'] == 'GetPromptInjectionPreview resident_id="resident_fixture"'
    assert component['script_draft'] == original['lineage_components'][0]['config_json']['script_draft']
    wire_text = result['lineage', 'lineage_edges'][0]['config_json']
    assert isinstance(wire_text, str)
    wire = json.loads(wire_text)
    assert wire['arguments']['prompt'] == '先用 GetPromptInjectionPreview 查看注入内容。'
    assert wire['resource_id'] == 'PreviewPromptInjection'
    assert records == original


def test_definition_export_retains_family_context_and_duty_prompts():
    from tests.cases.test_isa_d066_definition_migration import original_summary_graph
    from management_portal.society_service import family_definition, instruction_name_migration

    graph = original_summary_graph()
    graph['function_blocks'][0]['duty'] = {'holder_id': WORK_A,
        'memory': {'level': 'turn', 'prompt': '使用 PreviewPromptInjection。'}}
    records = {
        'lineage_nodes': [{'node_id': 'effect_fixture', 'lineage_id': 'lineage_fixture', 'node_type': 'effect'},
                          {'node_id': 'fbi_fixture', 'lineage_id': 'lineage_fixture', 'node_type': 'function_block'}],
        'lineage_components': [{'node_id': 'effect_fixture', 'config_json': deepcopy(graph['program']['components'][0]['config'])}],
        'lineage_edges': [{'edge_id': 'wire_fixture', 'lineage_id': 'lineage_fixture', 'edge_type': 'route',
                          'config_json': deepcopy(graph['program']['wires'][0]['config'])}],
        'function_block_duties': [{'function_block_id': 'fbi_fixture', 'memory_prompt': '使用 PreviewPromptInjection。',
                                  'memory_level': 'turn', 'holder_id': WORK_A}],
    }
    original = deepcopy((graph, records))
    source = object.__new__(DatabaseSnapshot)
    source.tables = {('lineage', name): Table('lineage', name, {key: 'text' for key in rows[0]}, (next(iter(rows[0])),))
                     for name, rows in records.items()}
    source.rows = lambda schema, name, **kwargs: deepcopy(records[name])
    source.current_definitions = lambda ids: {'lineage_fixture': deepcopy(graph)}
    result = source.definition_rows({'lineage_fixture'})
    config = result['lineage', 'lineage_components'][0]['config_json']
    assert config['script'] == family_definition.FAMILY_SUMMARY_ROLLUP_SCRIPT
    assert config['script_draft'] == records['lineage_components'][0]['config_json']['script_draft']
    duty = result['lineage', 'function_block_duties'][0]
    assert duty == {**records['function_block_duties'][0], 'memory_prompt': '使用 GetPromptInjectionPreview。'}
    assert (graph, records) == original
    graph['function_blocks'][0]['family']['steward'] = 'resident_other'
    with pytest.raises(instruction_name_migration.DefinitionMigrationRequired):
        source.definition_rows({'lineage_fixture'})


def test_graph_snapshot_is_read_only_restores_search_path_and_keeps_facade_members(primitive_store, monkeypatch):
    from management_portal.society_service import facade_members, lineage_aggregate, lineage_lifecycle, state_store

    created = lineage_lifecycle.create_lineage({
        'title': 'Snapshot owner', 'nodes': [_worker(WORK_A), _worker(WORK_B)], 'edges': [],
        'program': {'components': [{'id': 'effect_012345abcdef', 'kind': 'effect',
                                  'config': {'script': 'output status="ready"', 'endpoints': []}}], 'wires': []},
    }, ACTOR)
    identity = created['lineage_id']
    store = lineage_aggregate.lineage_catalog_store().state_store
    store.database.write(lambda connection: facade_members.replace(connection, WORK_A, [WORK_B]))

    def forbidden(*args, **kwargs):
        raise AssertionError('export must not initialize a catalog or store')

    monkeypatch.setattr(lineage_aggregate, 'lineage_catalog_store', forbidden)
    monkeypatch.setattr(state_store.LineageDataStore, '__init__', forbidden)
    with read_snapshot() as source:
        connection = source.connection
        path = connection.execute('SHOW search_path').fetchone()['search_path']
        assert connection.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on'
        assert connection.execute('SHOW transaction_isolation').fetchone()['transaction_isolation'] == 'repeatable read'
        before = source.current_definitions({identity})[identity]
        with pytest.raises(KeyError):
            source.current_definitions({'lineage_missing'})
        assert connection.execute('SHOW search_path').fetchone()['search_path'] == path
        result = source.definition_rows({identity})
        assert result['lineage', 'facade_members'] == [{'facade_id': WORK_A, 'member_id': WORK_B}]
        assert source.current_definitions({identity})[identity] == before
        assert connection.execute('SHOW search_path').fetchone()['search_path'] == path
