"""Authored template geometry binds to an existing Resident without moving it."""
import json
from pathlib import Path
import subprocess

import pytest

from tests.cases.test_isa_lineage_lifecycle import lineage as lineage, future_creation_store as future_creation_store, graph, resident
from tests.cases.isa_test_support import ok


@pytest.mark.parametrize('grouped', [True, False])
def test_structure_drop_preserves_anchor_and_relative_layout(lineage, grouped):
    target = resident(lineage, 'Existing anchor')
    before = graph(lineage)
    node = next(row for row in before['nodes'] if row['id'] == target)
    template = ok('CreateTemplate', {'template': {'kind': 'structure', 'title': 'Positioned loop', 'body': {
        'roles': [{'key': 'target', 'from': 'target'}],
        'components': [{'key': 'loop', 'kind': 'loop', 'config': {'max_rounds': 3}}],
        'wires': [], 'function_block': grouped,
        'layout': {'target': {'x': 40, 'y': 40}, 'loop': {'x': 65, 'y': 55}}}}})['template']
    arguments = {'lineage_id': lineage, 'template_id': template['template_id'],
                 'target': {'kind': 'resident', 'resident_ids': [target], 'x': node['x'], 'y': node['y']}}
    ok('GetTemplateApplyPreview', arguments)
    assert graph(lineage) == before
    applied = ok('ApplyTemplate', arguments)
    after = graph(lineage)
    assert len(after['nodes']) == len(before['nodes'])
    assert next(row for row in after['nodes'] if row['id'] == target) == node
    component = next(row for row in after['program']['components'] if row['id'] in applied['created']['component_ids'])
    assert (component['x'], component['y']) == (round(node['x'] + 25, 2), round(node['y'] + 15, 2))


def test_created_role_uses_the_layout_owner_and_keeps_the_existing_anchor(lineage):
    target = resident(lineage, 'Anchor')
    before = graph(lineage)
    anchor = next(row for row in before['nodes'] if row['id'] == target)
    template = ok('CreateTemplate', {'template': {'kind': 'structure', 'title': 'New colleague', 'body': {
        'roles': [{'key': 'target', 'from': 'target'}, {'key': 'worker', 'from': 'new', 'title': 'New colleague'}],
        'components': [], 'wires': [], 'layout': {'target': {'x': 40, 'y': 40}, 'worker': {'x': 65, 'y': 70}}}}})['template']
    arguments = {'lineage_id': lineage, 'template_id': template['template_id'], 'target': {'kind': 'resident', 'resident_ids': [target]}}
    ok('GetTemplateApplyPreview', arguments)
    assert graph(lineage) == before
    result = ok('ApplyTemplate', arguments)
    after = graph(lineage)
    assert len(result['created']['resident_ids']) == 1
    created = next(row for row in after['nodes'] if row['id'] == result['created']['resident_ids'][0])
    assert (created['x'], created['y']) == (round(anchor['x'] + 25, 2), round(anchor['y'] + 30, 2))
    assert next(row for row in after['nodes'] if row['id'] == target) == anchor


def test_browser_and_server_use_the_same_template_geometry():
    from management_portal.society_service.templates.structure_layout import positions
    body = {'roles': [{'key': 'target', 'from': 'target'}, {'key': 'worker', 'from': 'new'}],
            'components': [{'key': 'event', 'kind': 'event_source'}, {'key': 'loop', 'kind': 'loop'}],
            'match': [], 'wires': [{'key': 'watch', 'kind': 'watch', 'source': 'event', 'target': 'target'},
                                   {'key': 'route', 'kind': 'route', 'source': 'event', 'target': 'loop'},
                                   {'key': 'cycle', 'kind': 'route', 'source': 'loop', 'target': 'event'}],
            'layout': {'worker': {'x': 75, 'y': 60}}}
    script = Path(__file__).resolve().parents[2] / 'management_portal/static/lineage_template_graph.js'
    source = "const vm=require('node:vm'),fs=require('node:fs');const context={window:{PortalCompatibility:{identifier:String}}};vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);console.log(JSON.stringify(context.window.LineageTemplateGraph.placement(JSON.parse(process.argv[2])).layout));"
    result = subprocess.run(['node', '-e', source, str(script), json.dumps(body)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == positions(body)


def test_family_assembly_uses_its_actual_projection_without_moving_members(lineage):
    from management_portal.society_service.templates.structure_layout import placed
    members = [resident(lineage, title) for title in ('Member A', 'Member B')]
    before = graph(lineage)
    anchor = next(row for row in before['nodes'] if row['id'] == members[0])
    template = next(row for row in ok('ListTemplates')['templates'] if row['builtin_key'] == 'family')
    projection = ok('GetTemplate', {'template_id': template['template_id']})['template']['editor']['graph']['body']
    expected = placed(projection, {axis: anchor[axis] for axis in ('x', 'y')})
    result = ok('ApplyTemplate', {'lineage_id': lineage, 'template_id': template['template_id'],
        'target': {'kind': 'selection', 'resident_ids': members, 'element_ids': members,
                   'x': anchor['x'], 'y': anchor['y']}})
    after = graph(lineage)
    for identity in members:
        assert next(row for row in after['nodes'] if row['id'] == identity)['x'] == next(row for row in before['nodes'] if row['id'] == identity)['x']
    router = next(row for row in after['nodes'] if row['id'] == result['router_resident_id'])
    assert {axis: router[axis] for axis in ('x', 'y')} == expected['router_resident']
