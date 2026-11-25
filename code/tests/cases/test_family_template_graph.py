"""Recipe projection, editing and installation use the executing Family owner."""
from copy import deepcopy
import pytest

from management_portal.society_service import family_tasks
from management_portal.society_service.templates import familize
from management_portal.society_service.templates.registry import TemplateError
from tests.cases.isa_test_support import ok
from tests.cases.test_family_familize import lineage, charters, future_creation_store, resident, graph  # noqa: F401


def test_family_recipe_graph_saves_installs_and_survives_reconciliation(lineage, charters):
    source = next(t for t in ok('ListTemplates')['templates'] if t.get('builtin_key') == 'family')
    assert 'graph' not in source['editor']  # The shelf does not load every full graph.
    before = graph(lineage)
    original = ok('GetTemplate', {'template_id': source['template_id']})['template']
    projection = original['editor']['graph']
    assert projection == ok('GetTemplate', {'template_id': source['template_id']})['template']['editor']['graph']
    assert graph(lineage) == before  # Reading a recipe creates no runtime objects.
    recipe = deepcopy(source['body'])
    recipe['tasks'] = {'route': {'guide': 'Only choose a qualified member.', 'prompt': 'Choose for ${facts} using ${guide_path} (${guide_version})'}}
    recipe['review_interval_seconds'] = 7200
    custom = ok('CreateTemplate', {'template': {'kind': 'familize', 'title': 'Edited Family', 'body': recipe}})['template']
    generated = custom['editor']['graph']['body']
    router = next(c for c in generated['components'] if c['key'] == 'router')
    assert router['config']['script'] == familize.FAMILY_ROUTER_SCRIPT
    keys = {row['key'] for name in ('roles', 'components', 'wires') for row in generated[name]}
    assert all(w[end] in keys for w in generated['wires'] for end in ('source', 'target'))
    applied = ok('ApplyTemplate', {'lineage_id': lineage, 'template_id': custom['template_id'],
                                 'target': {'kind': 'resident', 'resident_ids': [resident(lineage, 'Member')]}})
    familize.sync_generated_structure(lineage)
    body = graph(lineage)
    block = next(b for b in body['function_blocks'] if b['id'] == applied['family_id'])
    assert family_tasks.configured(body, block)['route']['guide'] == recipe['tasks']['route']['guide']
    assert family_tasks.configured(body, block)['route']['prompt'] == recipe['tasks']['route']['prompt']
    timer_id = next(e['id'] for e in block['elements'] if e.get('key') == 'periodic_review')
    assert next(c for c in body['program']['components'] if c['id'] == timer_id)['config']['interval_seconds'] == 7200
    assert ok('GetTemplate', {'template_id': source['template_id']})['template']['body'] == original['body']
    published = ok('PublishTemplate', {'template_id': custom['template_id']})
    assert published['item']['body']['tasks'] == recipe['tasks']


@pytest.mark.parametrize('tasks', [None, [], {'unknown': {'guide': 'x'}}, {'route': {'role': 'steward'}},
                                   {'route': {'guide': ''}}, {'route': {'prompt': 'Missing request'}},
                                   {'route': {'prompt': '${facts} ${unknown}'}}])
def test_invalid_task_edits_cannot_enter_a_recipe(tasks):
    with pytest.raises(TemplateError):
        familize.KIND.normalize({'body': {'tasks': tasks}})


def test_review_recipe_projects_its_real_owned_graph(lineage):
    source = next(t for t in ok('ListTemplates')['templates'] if t.get('builtin_key') == 'family_review')
    template = ok('GetTemplate', {'template_id': source['template_id']})['template']
    projection = template['editor']['graph']
    assert {c['key'] for c in projection['body']['components']} == {
        'periodic_review', 'notify_periodic_review_task', 'review_activity', 'member_done'}
    assert projection['controls']['notify_periodic_review'] == [{'field': 'guide'}, {'field': 'prompt'}]
    assert next(c for c in projection['body']['components'] if c['key'] == 'periodic_review')['config']['interval_seconds'] == template['body']['interval_seconds']
