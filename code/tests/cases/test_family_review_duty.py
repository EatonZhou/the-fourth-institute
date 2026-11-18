"""An independently owned review keeps Family runtime gates and shared events.

:module: lineage.family
"""
from copy import deepcopy
from types import SimpleNamespace
import pytest

from management_portal.society_service import family_familize, family_review_duty, family_tasks
from management_portal.society_service.templates import familize
from tests.cases.isa_test_support import ACTOR, ok, run
from tests.cases.test_family_familize import charters, future_creation_store, lineage, graph, resident
from tests.cases.test_family_activity_review import team, configured_family


def template():
    return next(t for t in ok('ListTemplates', {})['templates'] if t.get('builtin_key') == 'family_review')


def install(lid, steward, tid=None, **options):
    return ok('ApplyTemplate', {'lineage_id': lid, 'template_id': tid or template()['template_id'],
              'target': {'kind': 'resident', 'resident_ids': [steward]}, **options})


def parent(lid, fid):
    body = graph(lid)
    return body, next(b for b in body['function_blocks'] if b['id'] == fid)


def test_review_lifecycle_preserves_shared_structure_and_never_resurrects(lineage, charters):
    member = resident(lineage, 'Member')
    f = ok('Familize', {'lineage_id': lineage, 'resident_ids': [member]})
    body, family = parent(lineage, f['family_id'])
    shared = {e['key']: e['id'] for e in family['elements'] if e.get('key') in {'member_done', 'check_summary', 'router'}}
    original = deepcopy(body)
    install(lineage, f['steward_resident_id'], dry_run=True)
    assert graph(lineage) == original
    bid = install(lineage, f['steward_resident_id'])['function_block_ids'][0]
    body, family = parent(lineage, f['family_id'])
    duty = family_review_duty.find(body, family)
    assert duty['id'] == bid
    assert family['family']['independent_duties'] == ['review']
    assert {e['key']: e['id'] for e in family['elements'] if e.get('key') in shared} == shared
    assert not any(e.get('key') in family_review_duty.ELEMENT_KEYS for e in family['elements'])
    effect = next(e['id'] for e in duty['elements'] if e.get('key') == 'notify_periodic_review_task')
    assert family_familize.component_family(body, effect)['id'] == family['id']
    assert run('ApplyTemplate', {'lineage_id': lineage, 'template_id': template()['template_id'],
               'target': {'kind': 'resident', 'resident_ids': [f['steward_resident_id']]}}).error
    timer = next(e['id'] for e in duty['elements'] if e.get('key') == 'periodic_review')
    config = deepcopy(next(c['config'] for c in body['program']['components'] if c['id'] == timer))
    config['interval_seconds'] = 900
    ok('ApplyLineageChanges', {'lineage_id': lineage, 'function_block_id': bid,
        'operations': [{'op': 'update', 'kind': 'component', 'id': timer, 'changes': {'config': config}}]}, version=body['revision'])
    ok('UpdateFamily', {'lineage_id': lineage, 'family_id': f['family_id'], 'remove_member_ids': [member]})
    ok('UpdateFamily', {'lineage_id': lineage, 'family_id': f['family_id'], 'add_member_ids': [member]})
    body, family = parent(lineage, f['family_id'])
    duty = family_review_duty.find(body, family)
    assert next(c for c in body['program']['components'] if c['id'] == timer)['config']['interval_seconds'] == 900
    assert sum(e.get('key') == 'record_review_activity' for e in duty['elements']) == 1
    assert all(e['id'] in {w['id'] for w in body['program']['wires']} for e in duty['elements'] if e['kind'] == 'wire')
    removed = {e['id'] for e in duty['elements']}
    ok('DeleteFunctionBlock', {'lineage_id': lineage, 'function_block_id': bid})
    ok('UpdateFamily', {'lineage_id': lineage, 'family_id': f['family_id'], 'goal': 'Edited after removal'})
    body, family = parent(lineage, f['family_id'])
    assert family['family']['independent_duties'] == ['review']
    assert family_review_duty.find(body, family) is None
    assert not removed & {i['id'] for k in ('components', 'wires') for i in body['program'][k]}
    assert 'review' not in family_tasks.configured(body, family)
    assert ok('CheckFamilyReview', {'lineage_id': lineage, 'family_id': f['family_id']})['status'] == 'disabled'
    assert any(e.get('key') == 'check_summary' for e in family['elements'])
    assert install(lineage, f['steward_resident_id'])['function_block_ids'][0] != bid
    body, family = parent(lineage, f['family_id'])
    assert len([b for b in body['function_blocks'] if b.get('duty', {}).get('holder_id') == f['steward_resident_id']]) == 1
    ok('Unfamilize', {'lineage_id': lineage, 'family_id': f['family_id']})
    assert not any(c['kind'] == 'effect' and 'CheckFamilyReview' in c['config'].get('script', '') for c in graph(lineage)['program']['components'])


def test_saved_review_template_rebinds_to_another_family(lineage, charters):
    families = [ok('Familize', {'lineage_id': lineage, 'resident_ids': [resident(lineage, name)]}) for name in ('First', 'Second')]
    bid = install(lineage, families[0]['steward_resident_id'])['function_block_ids'][0]
    saved = ok('CreateTemplate', {'from_selection': {'lineage_id': lineage, 'element_ids': [bid]}})['template']
    assert saved['kind'] == 'family_duty'
    assert families[0]['family_id'] not in str(saved['body'])
    item = ok('PublishTemplate', {'template_id': saved['template_id']})['item']
    assert item['group'] == 'duty'
    installed = ok('InstallStoreTemplate', {'store_id': item['store_id']})['template']
    install(lineage, families[1]['steward_resident_id'], installed['template_id'])
    body, block = parent(lineage, families[1]['family_id'])
    duty = family_review_duty.find(body, block)
    owned = {e['id'] for e in duty['elements']}
    wires = [w for w in body['program']['wires'] if w['id'] in owned]
    assert all(w['config']['invoke_profiles']['effect']['arguments']['family_id']['value'] == block['id'] for w in wires)
    # Editing an owned wire must not be undone by roster/startup reconciliation.
    activity = next(e['id'] for e in duty['elements'] if e.get('key') == 'record_review_activity')
    ok('ApplyLineageChanges', {'lineage_id': lineage, 'function_block_id': duty['id'],
       'operations': [{'op': 'delete', 'kind': 'wire', 'id': activity}]}, version=body['revision'])
    familize.sync_generated_structure(lineage)
    assert activity not in {w['id'] for w in graph(lineage)['program']['wires']}
    assert run('CreateTemplate', {'from_selection': {'lineage_id': lineage, 'element_ids': [duty['id']]}}).error


def test_independent_review_keeps_activity_idle_and_replay_gates(team, monkeypatch):
    from management_portal.society_service import family_review
    t = team
    install(t.body['lineage_id'], t.family['steward'])
    assert t.check()['status'] == 'unchanged'
    assert t.check(t.completed(1))['status'] == 'recorded'
    monkeypatch.setattr(family_review, '_idle', lambda _: False)
    assert t.check()['status'] == 'busy'
    monkeypatch.setattr(family_review, '_idle', lambda _: True)
    sent = t.check()
    assert sent['status'] == 'requested'
    assert t.repo.message(t.family['steward'], sent['message_id'])['author_id'] == t.family['facade']
    assert t.check()['status'] == 'unchanged'


def test_owned_review_effect_retains_scoped_facade_identity(team):
    from management_portal.hatg.contracts import InstructionContext
    from management_portal.society_service.lineage_program_effect_execution import _family_note_context
    t = team
    install(t.body['lineage_id'], t.family['steward'])
    body, block = parent(t.body['lineage_id'], t.block['id'])
    duty = family_review_duty.find(body, block)
    eid = next(e['id'] for e in duty['elements'] if e.get('key') == 'notify_periodic_review_task')
    context = InstructionContext(caller='resident_eeeeeeeeeeee', entry='lineage')
    args = {'lineage_id': body['lineage_id'], 'family_id': block['id']}
    assert _family_note_context(body, eid, 'CheckFamilyReview', args, context,
               lineage_id=body['lineage_id'], event=SimpleNamespace(type='time.reached')).caller == t.family['facade']
    assert _family_note_context(body, eid, 'CheckFamilyReview', {**args, 'family_id': 'fbi_000000000000'}, context,
               lineage_id=body['lineage_id'], event=SimpleNamespace(type='time.reached')).caller == context.caller


def test_additive_schema_upgrade_leaves_existing_family_in_legacy_mode(lineage, charters):
    from management_portal.society_service.state_store import get_lineage_data_store, LineageDataStore, lineage_data_schema_matches
    f = ok('Familize', {'lineage_id': lineage, 'resident_ids': [resident(lineage, 'Legacy')]})
    original, _ = parent(lineage, f['family_id'])
    database = get_lineage_data_store().database
    database.write(lambda c: c.execute('ALTER TABLE families DROP COLUMN independent_duties'))
    LineageDataStore(database=database)
    body, block = parent(lineage, f['family_id'])
    assert block['family']['independent_duties'] == []
    assert body == original
    with database.read() as connection:
        assert lineage_data_schema_matches(connection)


def test_removal_and_reinstall_resume_accepted_review_without_duplicate(team, monkeypatch):
    from management_portal.society_service import family_review
    t = team
    lid = t.body['lineage_id']
    bid = install(lid, t.family['steward'])['function_block_ids'][0]
    t.check(t.completed(1))
    dispatch = family_review.dispatch
    accepted = []
    def interrupted(*args, **kwargs):
        accepted.append(dispatch(*args, **kwargs))
        raise OSError('after admission')
    monkeypatch.setattr(family_review, 'dispatch', interrupted)
    with pytest.raises(OSError):
        family_review.check(lid, t.block['id'], ACTOR)
    mid = accepted[0]['message_id']
    original = deepcopy(t.repo.message(t.family['steward'], mid))
    ok('DeleteFunctionBlock', {'lineage_id': lid, 'function_block_id': bid})
    familize.sync_generated_structure(lid)
    assert t.check()['status'] == 'disabled'
    install(lid, t.family['steward'])
    monkeypatch.setattr(family_review, 'dispatch', lambda *a, **k: pytest.fail('must reuse accepted receipt'))
    assert t.check()['message_id'] == mid
    assert t.repo.message(t.family['steward'], mid) == original
    assert t.check()['status'] == 'unchanged'
