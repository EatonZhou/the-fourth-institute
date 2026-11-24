"""Portable current definitions use the installed ISA without rewriting history.

:module: operations.deployment
:design: decision_898b8911a2ff
:profiles: local.fixture
:risk: no-external-side-effects
"""
from copy import deepcopy
import json

from management_portal.distribution.database import DatabaseSnapshot, Table


def test_definition_export_normalizes_text_and_json_without_mutating_snapshot():
    records = {
        'lineage_nodes': [{'node_id': 'effect', 'lineage_id': 'selected', 'node_type': 'effect'}],
        'lineage_components': [{'node_id': 'effect', 'config_json': {
            'script': 'CheckFamilyProgress resident_id="resident_fixture"',
            'script_draft': 'CheckFamilyProgress resident_id="reviewed"',
        }}],
        'lineage_edges': [{'edge_id': 'wire', 'lineage_id': 'selected', 'config_json': json.dumps({
            'arguments': {'prompt': '先用 CheckFamilyProgress 查看当前进度。'},
            'resource_id': 'CheckFamilyProgress',
        })}],
    }
    original = deepcopy(records)
    source = object.__new__(DatabaseSnapshot)
    source.tables = {('lineage', name): Table('lineage', name,
        {key: 'text' for key in rows[0]}, ('edge_id' if name == 'lineage_edges' else 'node_id',))
        for name, rows in records.items()}
    source.rows = lambda schema, name, **kwargs: deepcopy(records[name])
    result = source.definition_rows({'selected'})
    component = result['lineage', 'lineage_components'][0]['config_json']
    assert component['script'] == 'MaintainFamilyProgress resident_id="resident_fixture"'
    assert component['script_draft'] == original['lineage_components'][0]['config_json']['script_draft']
    wire_text = result['lineage', 'lineage_edges'][0]['config_json']
    assert isinstance(wire_text, str)
    wire = json.loads(wire_text)
    assert wire['arguments']['prompt'] == '先用 MaintainFamilyProgress 查看当前进度。'
    assert wire['resource_id'] == 'CheckFamilyProgress'
    assert records == original
