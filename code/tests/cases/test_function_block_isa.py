"""Persistent boundaries through the public ISA and atomic template edit entry.

:importance: P2
"""
import pytest

from management_portal.society_service import function_block_domain as blocks, state_store
from tests.cases.isa_test_support import ok
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, graph, lineage, resident  # noqa: F401


def source(lid):
    return ok('CreateComponent', {'lineage_id':lid,'kind':'event_source','title':'Timer',
                                 'config':{'event_type':'manual.triggered'}})['component_id']


def test_wrap_update_unwrap_persists_elements_and_preserves_program(lineage):
    """功能块封装更新与解除持久保存元素且保留运行图"""
    worker, timer = resident(lineage, 'Worker'), source(lineage)
    original = graph(lineage)['program']
    bid = ok('Functionalize', {'lineage_id':lineage,'element_ids':[timer],'title':'Timed'})['function_block_id']
    [block] = graph(lineage)['function_blocks']
    assert block['elements'] == [{'id':timer,'kind':'component','key':None}]
    assert graph(lineage)['program'] == original
    updated = ok('UpdateFunctionBlock', {'lineage_id':lineage,'function_block_id':bid,
                                        'title':'Updated','add_element_ids':[worker], 'x':71, 'y':32})['function_block']
    assert updated['title'] == 'Updated'
    [stored] = graph(lineage)['function_blocks']
    assert (stored['x'],stored['y']) == (71,32)
    result = ok('Unfunctionalize', {'lineage_id':lineage,'function_block_id':bid})
    assert set(result['element_ids']) == {worker,timer}
    assert graph(lineage)['function_blocks'] == []
    assert graph(lineage)['program'] == original


def test_template_edit_and_group_are_one_transaction(lineage):
    """模板编辑与功能块分组在同一事务中完成"""
    before = graph(lineage)['revision']
    timer = 'event_source_0123456789ab'
    def edit(body):
        body['program']['components'].append({'id':timer,'kind':'event_source','title':'Manual','config':{'event_type':'manual.triggered'}})
    block = blocks.functionalize(lineage, [timer], 'sample_admin', title='From template', edit=edit,
                               template_id='template_0123456789ab', element_keys={timer:'timer'})
    stored = graph(lineage)
    assert stored['revision'] == before + 1
    assert stored['function_blocks'][0]['template_id'] == 'template_0123456789ab'
    assert stored['function_blocks'][0]['elements'][0]['key'] == 'timer'
    with pytest.raises(ValueError):
        blocks.functionalize(lineage, ['event_source_ffffffffffff'], 'sample_admin', edit=edit)
    assert graph(lineage)['revision'] == stored['revision']
    ok('DeleteComponent', {'lineage_id':lineage,'component_id':timer})
    assert graph(lineage)['function_blocks'] == []
    with state_store.get_lineage_data_store().database.read() as c:
        assert c.execute('SELECT deleted_at FROM lineage_nodes WHERE node_id=%s',(block['id'],)).fetchone()['deleted_at']


def test_delete_removes_nested_elements_and_resident_identities(lineage):
    """删除功能块递归清除元素和 Resident 身份"""
    worker, timer = resident(lineage, 'Worker'), source(lineage)
    inner = ok('Functionalize', {'lineage_id':lineage,'element_ids':[worker,timer]})['function_block_id']
    outer = ok('Functionalize', {'lineage_id':lineage,'element_ids':[inner]})['function_block_id']
    deleted = ok('DeleteFunctionBlock', {'lineage_id':lineage,'function_block_id':outer})
    assert deleted['resident_ids'] == [worker]
    assert set(deleted['element_ids']) == {inner,worker,timer}
    result = graph(lineage)
    assert result['nodes'] == result['program']['components'] == result['function_blocks'] == []


def test_delete_cascades_to_observers_of_external_wires(lineage):
    """删除功能块级联清理外部连线的监听组件"""
    worker, timer = resident(lineage, 'Worker'), source(lineage)
    wire = ok('CreateWire', {'lineage_id':lineage,'kind':'route','source':timer,'target':worker})['wire_id']
    observer = ok('CreateComponent', {'lineage_id':lineage,'kind':'event_source',
                                     'config':{'event_type':'event.delivery_failed'}})['component_id']
    ok('CreateWire', {'lineage_id':lineage,'kind':'watch','source':observer,'target':wire})
    observed_block = ok('Functionalize', {'lineage_id':lineage,'element_ids':[observer]})['function_block_id']
    bid = ok('Functionalize', {'lineage_id':lineage,'element_ids':[timer]})['function_block_id']
    ok('DeleteFunctionBlock', {'lineage_id':lineage,'function_block_id':bid})
    result = graph(lineage)
    assert [n['id'] for n in result['nodes']] == [worker]
    assert result['program']['components'] == result['program']['wires'] == []
    assert observed_block not in [b['id'] for b in result['function_blocks']]


def test_delete_releases_operator_executor(lineage):
    """删除功能块释放算子执行者身份

    :importance: P1"""
    operator = ok('CreateComponent', {'lineage_id':lineage,'kind':'filter','x':0,'y':0,
                                     'config':{'operator_implementation':'resident', 'prompt':'Decide'}})['component_id']
    executor = next(n['id'] for n in graph(lineage)['nodes'] if n.get('operator_executor'))
    bid = ok('Functionalize', {'lineage_id':lineage,'element_ids':[operator]})['function_block_id']
    ok('DeleteFunctionBlock', {'lineage_id':lineage,'function_block_id':bid})
    node = next(n for n in graph(lineage)['nodes'] if n['id'] == executor)
    assert not node.get('operator_executor')
