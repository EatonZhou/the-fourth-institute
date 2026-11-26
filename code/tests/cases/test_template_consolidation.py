from copy import deepcopy
from management_portal.society_service.templates import store, builtins, role, familize
from management_portal.society_service.templates.consolidation import MARKER
from tests.cases.isa_test_support import ok, ACTOR
from tests.cases.test_family_familize import charters, future_creation_store, lineage, graph, resident


def earlier_definitions():
    store.ensure_builtins()
    with store.database().transaction('templates') as connection:
        rows={r['template_id']:r for r in store.read_rows(connection,'templates')}
        original=rows[familize.TEMPLATE_ID]
        original['body'].pop('role_templates',None)
        original['body']['goal']='Existing official goal'
        original['version']+=1
        fallback=deepcopy(original)
        fallback.update(template_id=builtins.template_id('family_roles'),builtin_key='family_roles',title='Family（角色组合）')
        fallback['body']['role_templates']=role.references()
        rows[fallback['template_id']]=fallback
        store._write(connection,rows.values())
        connection.execute('DELETE FROM installed_builtins WHERE builtin_key=%s',(MARKER,))
    return original['template_id'],fallback['template_id']


def test_family_is_updated_in_place_and_fallback_resource_is_removed(lineage,charters):
    original,old=earlier_definitions()
    rows=ok('ListTemplates')['templates']
    families=[row for row in rows if row.get('builtin_key') in {'family','family_roles'}]
    assert [row['template_id'] for row in families]==[original]
    assert families[0]['body']['role_templates']==role.references()
    assert families[0]['body']['goal']=='Existing official goal'
    assert store.get(old)['template_id']==original
    assert store.get(original)['body']['goal']=='Existing official goal'
    with store.database().read() as connection:
        assert connection.execute('SELECT template_id FROM templates WHERE template_id=%s',(old,)).fetchone() is None
    f=ok('Familize',{'lineage_id':lineage,'resident_ids':[resident(lineage,'Member')]})
    assert next(b for b in graph(lineage)['function_blocks'] if b['id']==f['family_id'])['template_id']==original
    first=deepcopy(store.get(original));store.ensure_builtins();assert store.get(original)==first


def test_explicit_copies_are_not_merged_and_template_references_are_rewritten(lineage,charters):
    original,old=earlier_definitions()
    # Seed a deliberate user copy before the one-time update; titles are not
    # an identity or a reason to remove independently saved content.
    with store.database().transaction('templates') as connection:
        rows={r['template_id']:r for r in store.read_rows(connection,'templates')}
        user=deepcopy(rows[original]);user.update(template_id='template_123456789abc',builtin_key=None,builtin_source=None,created_by=ACTOR)
        user['body']['goal']='Independent user recipe';rows[user['template_id']]=user
        reference=deepcopy(user);reference.update(template_id='template_abcdef123456',kind='script',body={'script':'', 'source_template_id':old});rows[reference['template_id']]=reference
        store._write(connection,rows.values())
    store.ensure_builtins()
    assert store.get(user['template_id'])['body']['goal']=='Independent user recipe'
    assert 'role_templates' not in store.get(user['template_id'])['body']
    assert store.get(reference['template_id'])['body']['source_template_id']==original
