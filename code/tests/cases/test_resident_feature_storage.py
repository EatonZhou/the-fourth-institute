"""Typed Family facts (decision_44cf587c1721).

:module: spec.design-governance
"""
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.isa_test_support import ok
from tests.cases.test_family_familize import charters, lineage, resident, graph, future_creation_store  # noqa: F401


def test_family_has_one_authoritative_roster(lineage, charters):
    """Familize 把标题、目标和成员、facade、Router、steward 角色写入 Family 表"""
    member = resident(lineage, 'Member')
    family = ok('Familize', {'lineage_id':lineage,'resident_ids':[member], 'title':'Typed Family','goal':'Keep facts'})
    store = get_lineage_data_store()
    with store.database.read() as c:
        row = c.execute('SELECT * FROM families WHERE family_id=%s', (family['family_id'],)).fetchone()
        assert row['title'] == 'Typed Family'
        assert row['goal'] == 'Keep facts'
        element = c.execute('SELECT element_kind,element_key FROM function_block_elements WHERE function_block_id=%s AND element_id=%s',
                            (family['family_id'], member)).fetchone()
        assert element['element_kind'] == 'resident'
        assert element['element_key'] == 'member:' + member
        roles = c.execute('SELECT resident_id,role FROM family_residents WHERE family_id=%s', (family['family_id'],)).fetchall()
    assert {(r['resident_id'],r['role']) for r in roles} == {
        (member,'member'), (family['facade_resident_id'],'facade'),
        (family['router_resident_id'],'router'), (family['steward_resident_id'],'steward')}


def test_stale_resident_save_cannot_overwrite_family(lineage, charters):
    """保存 Resident 记录时带入的过期 family 字段不覆盖 Family 成员关系

    :module: resident.identity"""
    member=resident(lineage,'Member')
    family=ok('Familize', {'lineage_id':lineage,'resident_ids':[member], 'title':'Family'}, key='authority-family')
    store=get_lineage_data_store()
    record=store.resident_record(member)
    record['family']={'role':'facade','family_id':'not-a-family'}
    store.put_resident_record(member,record)
    actual=store.resident_record(member)
    assert actual['family']['role']=='member'
    assert actual['family']['family_id']==family['family_id']


def test_nested_family_keeps_inner_facade_identity_and_outer_membership(lineage, charters):
    """嵌套 Family 中内层 facade 保留 facade 身份，同时是外层 Family 的成员"""
    member=resident(lineage,'Member')
    inner=ok('Familize', {'lineage_id':lineage,'resident_ids':[member], 'title':'Inner'}, key='inner')
    facade=inner['facade_resident_id']
    outer=ok('Familize', {'lineage_id':lineage,'resident_ids':[facade], 'title':'Outer'}, key='outer')
    store=get_lineage_data_store()
    assert store.resident_record(facade)['family']['family_id']==inner['family_id']
    assert store.resident_record(facade)['family']['role']=='facade'
    with store.database.read() as c:
        rows=c.execute('SELECT family_id,role FROM family_residents WHERE resident_id=%s',(facade,)).fetchall()
    assert {(r['family_id'],r['role']) for r in rows}=={(inner['family_id'],'facade'),(outer['family_id'],'member')}
