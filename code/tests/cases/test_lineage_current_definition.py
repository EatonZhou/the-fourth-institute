"""Current graph ownership, deletion fencing, typed edge data and invalidation.

:module: lineage.team
:importance: P1
"""
import copy
from types import SimpleNamespace
import pytest
from management_portal.society_service import graph_storage
from management_portal.society_service.lineage_data_runtime import RuntimeCASConflict
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_family_familize import lineage, future_creation_store, resident  # noqa: F401


def test_deleted_component_and_wire_keep_ids_and_reject_late_writes(lineage):
    """删除图对象后保留身份并拒绝迟到写入

    :design: decision_71aa039e3211
    :design-point:
    """
    store = get_lineage_data_store()
    base = store.lineage_definition(lineage)
    component = {'id':'event_source_001122334455','kind':'event_source','config':{'event_type':'manual.triggered'}}
    rid = resident(lineage, 'Target')
    value = store.lineage_definition(lineage)
    value['program']['components'] = [component]
    value['program']['wires'] = [{'id':'wire_001122334455','kind':'route','source':component['id'],'target':rid}]
    store.put_lineage_definition(lineage,value,{})
    runtime = {'schema':'tfi.lineage-runtime-state.v1','lineage_id':lineage,'nodes':{component['id']:{'status':'idle'}},'edges':{'wire_001122334455':{'status':'ready'}}}
    store.put_lineage_runtime(lineage,runtime)
    expected = store.lineage_snapshot(lineage)[1]
    removed = copy.deepcopy(value); removed['program']['components']=[];removed['program']['wires']=[]
    store.put_lineage_definition(lineage,removed,{})
    with store.database.read() as c:
        assert c.execute('SELECT deleted_at FROM lineage_nodes WHERE node_id=%s',(component['id'],)).fetchone()['deleted_at']
        assert c.execute("SELECT deleted_at FROM lineage_edges WHERE edge_id='wire_001122334455'").fetchone()['deleted_at']
        assert rid in graph_storage.active_objects(c,lineage)
    with pytest.raises(ValueError,match='Deleted graph IDs'):
        store.put_lineage_definition(lineage,value,{})
    for namespace, key in [('edges','wire_001122334455')]:
        stale = copy.deepcopy(expected);stale[namespace][key]={'status':'done'}
        with pytest.raises(RuntimeCASConflict,match='deleted or moved'):
            store.put_lineage_runtime_cas(lineage,expected,stale)
    store.remove_lineage(lineage,deleted_by='sample_admin')
    with pytest.raises(RuntimeCASConflict,match='Lineage has been deleted'):
        store.put_lineage_runtime_cas(lineage,{},runtime)


def test_typed_handoff_round_trip_and_noop(lineage):
    """交接项写入后原样读回，相同内容重写不改动数据行，未知来源字段被拒绝

    :module: lineage.service
    :importance: P2
    """
    store=get_lineage_data_store(); a=resident(lineage,'A');b=resident(lineage,'B')
    value=store.lineage_definition(lineage)
    item={'item_id':'source','label':'Code','source':{'workspace_id':'workspace_112233445566','path':'/share/test','scope':'.'},'transfer':{'mode':'copy','method':'git_worktree','parameters':{'branch':'task'}},'target':{'scope':'.','label':'Copy'}}
    value['edges']=[{'id':'handoff_112233445566','type':'handoff','source':a,'target':b,'items':[item],'note':'Review'}]
    store.put_lineage_definition(lineage,value,{})
    actual=store.lineage_definition(lineage)
    assert actual['edges'][0]['items']==[item]
    def tokens(c):
        return [c.execute('SELECT ctid::text token FROM '+table).fetchall() for table in ('lineage_handoff_items',)]
    with store.database.read() as c: before=tokens(c)
    store.put_lineage_definition(lineage,value,{})
    with store.database.read() as c: assert tokens(c)==before
    value['edges'][0]['items'][0]['source']['unknown']='bad'
    with pytest.raises(ValueError,match='Unknown handoff source'):
        store.put_lineage_definition(lineage,value,{})


def test_watch_coalesces_to_current_revision_and_reconnects(monkeypatch):
    """Lineage 变更订阅合并当前游标并支持重连

    :design: decision_71aa039e3211
    :design-point:
    """
    from management_portal.society_service.isa import definition
    from management_portal.hatg.contracts import not_found
    monkeypatch.setattr(definition,'lineage_state',lambda:SimpleNamespace(lineage_access_projection=lambda *_:{},_ensure_visible=lambda *_:None))
    values=iter([17,23])
    monkeypatch.setattr(definition,'lineage_revision',lambda _:next(values))
    args={'lineage_id':'lineage_001122334455','after_cursor':3}
    inv=SimpleNamespace(argument=args.get,caller='sample_admin')
    stream=definition.watch_lineage(inv)
    assert next(stream)==[{'cursor':17,'lineage_id':args['lineage_id']}]
    assert next(stream)==[{'cursor':23,'lineage_id':args['lineage_id']}]
    monkeypatch.setattr(definition,'lineage_revision',lambda _:(_ for _ in ()).throw(not_found('Lineage deleted')))
    with pytest.raises(StopIteration):next(stream)
