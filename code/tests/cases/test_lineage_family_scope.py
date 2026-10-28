"""Lineage-wide Family creation, automatic membership and reversible scope."""
import pytest

from tests.cases.isa_test_support import ok, run
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, lineage, graph, resident
from tests.cases.test_family_familize import charters


def scoped(lineage_id):
    return next(block for block in graph(lineage_id)['function_blocks'] if block.get('family', {}).get('lineage_scope'))


def test_empty_lineage_family_auto_joins_and_can_be_removed(lineage, charters):
    """Lineage 级 Family 自动纳入后建的 Resident，重复建立返回同一 Family，可撤销"""
    made = ok('Familize', {'lineage_id': lineage, 'scope': 'lineage'}, key='scope')
    assert made['member_resident_ids'] == []
    assert graph(lineage)['leader_resident_id'] == made['facade_resident_id']
    assert scoped(lineage)['id'] == made['family_id']
    again = ok('Familize', {'lineage_id': lineage, 'scope': 'lineage'}, key='scope-again')
    assert again['family_id'] == made['family_id']
    assert len(graph(lineage)['nodes']) == 3
    assert not any(c.get('config', {}).get('event_type') == 'resident.queue_pressure_exceeded'
                   for c in graph(lineage)['program']['components'])
    diagnostics = ok('GetLineageDiagnostics', {'lineage_id': lineage})
    assert diagnostics['runnable'], diagnostics['diagnostics']
    first = resident(lineage, 'First')
    assert scoped(lineage)['family']['members'] == [first]
    view = graph(lineage)
    queue = next(c for c in view['program']['components']
                 if c.get('config', {}).get('event_type') == 'resident.queue_pressure_exceeded')
    assert {w['target'] for w in view['program']['wires']
            if w['kind'] == 'watch' and w['source'] == queue['id']} == {first}
    removed = ok('Unfamilize', {'lineage_id': lineage, 'family_id': made['family_id']})
    assert removed['leader_resident_id'] == first
    assert [n['id'] for n in graph(lineage)['nodes']] == [first]
    second = resident(lineage, 'Second')
    assert not graph(lineage)['function_blocks']
    assert second != first


def test_empty_lineage_family_unfamilizes_to_empty(lineage, charters):
    """空 Lineage 的 Family 撤销后图回到无节点、无 Leader"""
    made = ok('Familize', {'lineage_id': lineage, 'scope': 'lineage'})
    ok('Unfamilize', {'lineage_id': lineage, 'family_id': made['family_id']})
    assert graph(lineage)['nodes'] == []
    assert graph(lineage)['leader_resident_id'] == ''


def test_lineage_scope_preserves_nested_families(lineage, charters):
    """Lineage 级 Family 把已有 Family 作为成员保留，撤销后内层 Family 及其成员恢复"""
    first, second = resident(lineage, 'First'), resident(lineage, 'Second')
    inner = ok('Familize', {'lineage_id': lineage, 'resident_ids': [first]}, key='inner')
    outer = ok('Familize', {'lineage_id': lineage, 'scope': 'lineage'}, key='outer')
    assert set(outer['member_resident_ids']) == {inner['facade_resident_id'], second}
    third = resident(lineage, 'Third')
    assert third in scoped(lineage)['family']['members']
    fourth = ok('CreateResident', {'lineage_id': lineage, 'title': 'Fourth', 'family_id': inner['family_id']})['resident_id']
    assert fourth not in scoped(lineage)['family']['members']
    restored = ok('Unfamilize', {'lineage_id': lineage, 'family_id': outer['family_id']})
    assert restored['leader_resident_id'] == inner['facade_resident_id']
    blocks = graph(lineage)['function_blocks']
    assert len(blocks) == 1 and blocks[0]['id'] == inner['family_id']
    assert set(blocks[0]['family']['members']) == {first, fourth}


@pytest.mark.parametrize('enabled', [True, False])
def test_create_lineage_familize_option(lineage, charters, enabled):
    """CreateLineage 带 familize 时一并建立以描述为目标的 Family，否则得到空图"""
    created = ok('CreateLineage', {'project_id': 'project_test', 'title': 'New scope', 'description': '共同目标', 'familize': enabled})
    view = graph(created['lineage_id'])
    if enabled:
        family = scoped(created['lineage_id'])
        assert family['id'] == created['family_id']
        assert family['family']['goal'] == '共同目标'
        assert len(view['nodes']) == 3
    else:
        assert view['nodes'] == [] and view['function_blocks'] == []
        assert 'family_id' not in created


def test_selection_still_requires_members_and_scope_cannot_mix_selection(lineage):
    """Familize 未给成员时报参数错误，scope 与 resident_ids 不能同时给出"""
    for arguments in ({'resident_ids': []}, {}, {'scope': 'lineage', 'resident_ids': ['resident_000000000000']}):
        result = run('Familize', {'lineage_id': lineage, **arguments})
        assert result.error['code'] == 'instruction.invalid_arguments'
    assert graph(lineage)['nodes'] == []


def test_lineage_scope_preserves_ordinary_function_blocks(lineage, charters):
    """Lineage 级 Family 保留已有的普通功能块，撤销后功能块原样恢复"""
    member = resident(lineage, 'Grouped member')
    component = ok('CreateComponent', {'lineage_id': lineage, 'kind': 'event_source', 'config': {'event_type': 'manual.triggered'}})['component_id']
    group = ok('Functionalize', {'lineage_id': lineage, 'element_ids': [member, component], 'title': 'Existing group'})['function_block_id']
    made = ok('Familize', {'lineage_id': lineage, 'scope': 'lineage'})
    assert made['member_resident_ids'] == [member]
    assert group in {e['id'] for e in scoped(lineage)['elements']}
    later = resident(lineage, 'Later')
    assert set(scoped(lineage)['family']['members']) == {member, later}
    ok('Unfamilize', {'lineage_id': lineage, 'family_id': made['family_id']})
    view = graph(lineage)
    assert len(view['function_blocks']) == 1
    assert {e['id'] for e in view['function_blocks'][0]['elements']} == {member, component}


def test_lineage_family_cannot_be_nested(lineage, charters):
    """Lineage 级 Family 的门面 Resident 不能再被 Familize 嵌套"""
    made = ok('Familize', {'lineage_id': lineage, 'scope': 'lineage'})
    invalid = run('Familize', {'lineage_id': lineage, 'resident_ids': [made['facade_resident_id']]})
    assert invalid.error['code'] == 'instruction.invalid_arguments'
    assert len(graph(lineage)['nodes']) == 3


def test_old_scope_schema_requires_an_explicit_migration():
    """缺少 lineage_scope 列的旧 schema 初始化时报不匹配，不自动升级"""
    import pytest
    from management_common.portal_database import portal_database
    from management_portal.society_service.state_store import create_lineage_data_schema, LineageDataStore, lineage_data_schema_matches
    database = portal_database(schema='lineage_scope_v47')
    with database.transaction() as connection:
        create_lineage_data_schema(connection)
        connection.execute('ALTER TABLE families DROP COLUMN lineage_scope')
        connection.execute("UPDATE lineage_meta SET value='tfi.lineage-data.v46' WHERE key='schema'")
        with pytest.raises(RuntimeError, match="does not match"):
            LineageDataStore._initialize(connection)
        assert connection.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()["value"] == "tfi.lineage-data.v46"


def test_creation_failure_reports_the_committed_lineage(lineage, monkeypatch):
    """CreateLineage 建立 Family 失败时报告已提交的 Lineage，该 Lineage 保留"""
    from management_portal.society_service.templates import familize as family
    def fail(*args, **kwargs):
        raise ValueError('Role setup unavailable')
    monkeypatch.setattr(family, 'familize', fail)
    result = run('CreateLineage', {'project_id': 'project_test', 'title': 'Retained', 'familize': True})
    assert result.error['commit_state'] == 'committed'
    created_id = result.error['details']['lineage_id']
    assert graph(created_id)['title'] == 'Retained'
