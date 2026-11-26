"""Role recipes install actual duties without duplicating Family delivery.

:module: lineage.family
"""
from copy import deepcopy

from tests.cases.isa_test_support import ok, run
from tests.cases.test_family_familize import charters, future_creation_store, lineage, graph, resident
from management_portal.society_service import family_roles, family_tasks
from management_portal.society_service.templates import familize


def templates():
    return {t['body']['role_key']: t for t in ok('ListTemplates')['templates'] if t['kind'] == 'role'}


def current(lid, fid):
    body = graph(lid)
    return body, next(b for b in body['function_blocks'] if b['id'] == fid)


def test_roles_are_real_single_resident_compositions_and_shared_delivery_is_singular(lineage, charters):
    sources = templates()
    assert set(sources) == {'router', 'steward'}
    for key, source in sources.items():
        detail = ok('GetTemplate', {'template_id': source['template_id']})['template']
        assert detail['editor']['graph']['body']['components']
        assert source['body']['memories'][0]['level'] == 'session'
    f = ok('Familize', {'lineage_id': lineage, 'resident_ids': [resident(lineage, 'Member')]})
    body, parent = current(lineage, f['family_id'])
    roles = family_roles.role_blocks(body, parent)
    assert {r['role']['key'] for r in roles} == {'router', 'steward'}
    for role in roles:
        key = role['role']['key']
        assert [e['id'] for e in role['elements'] if e['kind'] == 'resident'] == [parent['family'][key]]
        assert role['role']['source'] == {'template_id': sources[key]['template_id'], 'version': sources[key]['version']}
        assert family_roles.duties(body, role)
    assert sum(e.get('key') == 'member_done' for b in body['function_blocks'] for e in b['elements']) == 1
    assert sum(e.get('key') == 'relay' for b in body['function_blocks'] for e in b['elements']) == 1
    assert next(e for e in parent['elements'] if e.get('key') == 'relay')['kind'] == 'wire'
    configured = family_tasks.configured(body, parent)
    assert {'route', 'recruit', 'route_review', 'review', 'recovery'} <= configured.keys()
    assert 'GetFamilyRoutingContext' in configured['route']['guide']
    assert not any('family_tasks' in p.get('arguments', {}) for w in body['program']['wires'] for p in w.get('config', {}).get('invoke_profiles', {}).values())
    assert run('DeleteFunctionBlock', {'lineage_id': lineage, 'function_block_id': roles[0]['id']}).error


def test_duty_removal_reinstall_and_roster_preserve_binding_and_unrelated_sources(lineage, charters):
    member = resident(lineage, 'Member')
    f = ok('Familize', {'lineage_id': lineage, 'resident_ids': [member, resident(lineage, 'Other member')]})
    body, parent = current(lineage, f['family_id'])
    steward = next(r for r in family_roles.role_blocks(body, parent) if r['role']['key'] == 'steward')
    duty = next(d for d in family_roles.duties(body, steward) if d['duty']['parameters'].get('family_task') == 'recruit')
    shared = {e['key']: e['id'] for e in parent['elements'] if e.get('key') in {'member_done', 'relay', 'route_relay'}}
    before = deepcopy(body)
    source = templates()['steward']
    args = {'lineage_id': lineage, 'template_id': source['template_id'], 'target': {'kind': 'resident', 'resident_ids': [member]}}
    assert run('ApplyTemplate', args).error
    assert graph(lineage) == before
    ok('DeleteFunctionBlock', {'lineage_id': lineage, 'function_block_id': duty['id']})
    ok('UpdateFamily', {'lineage_id': lineage, 'family_id': f['family_id'], 'remove_member_ids': [member]})
    ok('UpdateFamily', {'lineage_id': lineage, 'family_id': f['family_id'], 'add_member_ids': [member]})
    familize.sync_generated_structure(lineage)
    body, parent = current(lineage, f['family_id'])
    assert duty['id'] not in {b['id'] for b in body['function_blocks']}
    assert 'recruit' not in family_tasks.configured(body, parent)
    assert {e['key']: e['id'] for e in parent['elements'] if e.get('key') in shared} == shared
    args['target']['resident_ids'] = [f['steward_resident_id']]
    before = deepcopy(body)
    ok('GetTemplateApplyPreview', args)
    assert graph(lineage) == before
    ok('ApplyTemplate', args)
    body, parent = current(lineage, f['family_id'])
    assert 'recruit' in family_tasks.configured(body, parent)
    assert len(body['nodes']) == len(before['nodes'])


def test_legacy_family_recipe_is_not_implicitly_converted(lineage, charters):
    source = next(t for t in ok('ListTemplates')['templates'] if t.get('builtin_key') == 'family')
    recipe = {k: deepcopy(source[k]) for k in ('kind', 'category', 'title', 'description', 'targets', 'parameters', 'body')}
    recipe['body'].pop('role_templates', None)
    saved = ok('CreateTemplate', {'template': recipe})['template']
    f = ok('ApplyTemplate', {'lineage_id': lineage, 'template_id': saved['template_id'],
        'target': {'kind': 'resident', 'resident_ids': [resident(lineage, 'Legacy')]}})
    body, parent = current(lineage, f['family_id'])
    before = deepcopy(body)
    assert not parent['family'].get('role_bindings')
    familize.sync_generated_structure(lineage)
    after, parent = current(lineage, f['family_id'])
    assert not family_roles.role_blocks(after, parent)
    assert after['nodes'] == before['nodes']


def test_installed_guide_and_authored_structure_survive_sync_and_sources_stay_frozen(lineage, charters):
    f = ok('Familize', {'lineage_id': lineage, 'resident_ids': [resident(lineage, 'Member')]})
    body, parent = current(lineage, f['family_id'])
    role = next(r for r in family_roles.role_blocks(body, parent) if r['role']['key'] == 'router')
    duty = next(d for d in family_roles.duties(body, role) if d['duty']['parameters']['family_task'] == 'route')
    source = deepcopy(role['role']['source'])
    effect = next(e['id'] for e in duty['elements'] if e.get('key') == 'router')
    ok('UpdateDuty', {'lineage_id': lineage, 'function_block_id': duty['id'], 'prompt': 'Custom routing guide',
        'task_prompt': 'Custom routing task ${facts} ${guide_path} ${guide_version}'})
    body = graph(lineage)
    ok('ApplyLineageChanges', {'lineage_id': lineage, 'function_block_id': duty['id'],
        'operations': [{'op': 'update', 'kind': 'component', 'id': effect, 'changes': {'title': 'Authored Router Effect'}}]}, version=body['revision'])
    familize.sync_generated_structure(lineage)
    body, parent = current(lineage, f['family_id'])
    assert family_tasks.configured(body, parent)['route']['guide'] == 'Custom routing guide'
    assert family_tasks.configured(body, parent)['route']['prompt'].startswith('Custom routing task')
    assert run('UpdateDuty', {'lineage_id': lineage, 'function_block_id': duty['id'], 'task_prompt': 'No request facts'}).error
    assert next(c for c in body['program']['components'] if c['id'] == effect)['title'] == 'Authored Router Effect'
    role = next(r for r in family_roles.role_blocks(body, parent) if r['role']['key'] == 'router')
    assert role['role']['source'] == source
    from management_portal.society_service import resident_record_domain
    _, _, guide = family_tasks.guide(resident_record_domain.find_resident(f['router_resident_id']))
    assert 'Custom routing guide' in guide


def test_review_withdrawal_and_explicit_reinstall_do_not_duplicate_shared_completion(lineage, charters):
    f = ok('Familize', {'lineage_id': lineage, 'resident_ids': [resident(lineage, 'Member')]})
    body, parent = current(lineage, f['family_id'])
    role = next(r for r in family_roles.role_blocks(body, parent) if r['role']['key'] == 'steward')
    review = next(d for d in family_roles.duties(body, role) if d['duty']['parameters']['family_task'] == 'review')
    source = next(t for t in ok('ListTemplates')['templates'] if t.get('builtin_key') == 'family_review')
    args = {'lineage_id': lineage, 'template_id': source['template_id'], 'target': {'kind': 'resident', 'resident_ids': [f['steward_resident_id']]}}
    assert run('ApplyTemplate', args).error
    ok('DeleteFunctionBlock', {'lineage_id': lineage, 'function_block_id': review['id']})
    familize.sync_generated_structure(lineage)
    body, parent = current(lineage, f['family_id'])
    assert 'review' not in family_tasks.configured(body, parent)
    ok('ApplyTemplate', args)
    familize.sync_generated_structure(lineage)
    body, parent = current(lineage, f['family_id'])
    assert 'review' in family_tasks.configured(body, parent)
    for key in ('member_done', 'periodic_review', 'record_review_activity'):
        assert sum(e.get('key') == key for b in body['function_blocks'] for e in b['elements']) == 1


def test_family_interval_updates_the_installed_role_duty(lineage, charters):
    f = ok('Familize', {'lineage_id': lineage, 'resident_ids': [resident(lineage, 'Member')]})
    ok('UpdateFamily', {'lineage_id': lineage, 'family_id': f['family_id'], 'review_interval_seconds': 900})
    body, parent = current(lineage, f['family_id'])
    from management_portal.society_service.family_definition import family_elements
    timer = next(e['id'] for e in family_elements(body, parent) if e.get('key') == 'periodic_review')
    assert next(c for c in body['program']['components'] if c['id'] == timer)['config']['interval_seconds'] == 900


def test_role_task_prompt_preview_and_invalid_save_are_atomic(lineage, charters):
    f = ok('Familize', {'lineage_id': lineage, 'resident_ids': [resident(lineage, 'Member')]})
    body, parent = current(lineage, f['family_id'])
    role = next(r for r in family_roles.role_blocks(body, parent) if r['role']['key'] == 'router')
    duty = next(d for d in family_roles.duties(body, role) if d['duty']['parameters']['family_task'] == 'route')
    request = {'lineage_id': lineage, 'function_block_id': duty['id'], 'operations': [],
        'duty_changes': {'task_prompt': 'Atomic task ${facts} ${guide_path}'}}
    ok('ApplyLineageChanges', {**request, 'dry_run': True}, version=body['revision'])
    assert graph(lineage) == body
    invalid = {**request, 'duty_changes': {'task_prompt': 'Missing original facts', 'description': 'Must roll back'}}
    assert run('ApplyLineageChanges', invalid, version=body['revision']).error
    assert graph(lineage) == body
    ok('ApplyLineageChanges', request, version=body['revision'])
    after, parent = current(lineage, f['family_id'])
    assert family_tasks.configured(after, parent)['route']['prompt'] == request['duty_changes']['task_prompt']


def test_personal_assistant_keeps_its_original_recipe_and_configuration(monkeypatch):
    from management_portal.society_service import templates
    from management_portal.society_service.templates import store
    source = {'template_id': familize.TEMPLATE_ID, 'body': {'goal': 'Personal configuration'}}
    def get(identity):
        assert identity == familize.TEMPLATE_ID
        return deepcopy(source)
    def assemble(*args, **kwargs):
        return {'template_id': kwargs['template_id'], 'body': kwargs['load_body']()}
    monkeypatch.setattr(store, 'get', get)
    monkeypatch.setattr(familize, 'familize', assemble)
    result = templates.create_family('lineage_000000000001', [], 'actor', personal_ama_id='resident_000000000001')
    assert result == {'template_id': familize.TEMPLATE_ID, 'body': source['body']}


def test_whole_family_uninstall_uses_existing_stop_gate_and_cleans_role_sources(lineage, charters, monkeypatch):
    import pytest
    member = resident(lineage, 'Member')
    f = ok('Familize', {'lineage_id': lineage, 'resident_ids': [member]})
    before, parent = current(lineage, f['family_id'])
    scopes = {i['id'] for role in family_roles.role_blocks(before, parent) for i in [role, *family_roles.duties(before, role)]}
    def busy(*args, **kwargs):
        raise familize.FamilyBusy('Role still running')
    monkeypatch.setattr(familize, '_stop_roles', busy)
    assert run('Unfamilize', {'lineage_id': lineage, 'family_id': f['family_id']}).error
    assert graph(lineage) == before
    monkeypatch.setattr(familize, '_stop_roles', lambda *args, **kwargs: None)
    ok('Unfamilize', {'lineage_id': lineage, 'family_id': f['family_id']})
    after = graph(lineage)
    assert not scopes & {b['id'] for b in after['function_blocks']}
    assert member in {n['id'] for n in after['nodes']}
    assert not {f['router_resident_id'], f['steward_resident_id']} & {n['id'] for n in after['nodes']}
