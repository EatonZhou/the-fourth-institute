"""Typed extra relationships, bounded reads and stable self ownership.

:module: spec.design-governance
"""
import copy
import os
import secrets
import pytest
from management_common.postgres_database import PostgresDatabase
from management_portal.society_service import resident_catalog, resident_record_domain, resource_client
from management_portal.society_service import resident_workspace_storage as storage
from management_portal.resources.workspace_store import get_workspace_store
from tests.cases.database_test_support import QueryGuard

RID='resident_111111111111'
LID='lineage_111111111111'
OWNED='workspace_111111111111'
EXTRA='workspace_222222222222'

@pytest.fixture
def relation_database():
    schema=f"{os.environ['PORTAL_DATABASE_NAMESPACE']}_workspace_{secrets.token_hex(3)}"
    db=PostgresDatabase(os.environ['PORTAL_DATABASE_URL'], schema=schema)
    with db.connect() as c:
        c.execute('CREATE TABLE residents(resident_id TEXT PRIMARY KEY, owned_workspace_id TEXT)')
        for sql in storage.WORKSPACE_SCHEMA_SQL: c.execute(sql)
        c.execute('INSERT INTO residents VALUES (%s,%s)',(RID,OWNED))
    yield db
    with db.connect() as c: c.execute(f'DROP SCHEMA "{schema}" CASCADE')
    db.close()


def test_relationships_are_extra_only_and_unchanged_save_does_no_writes(relation_database):
    """Workspace 绑定表只存额外绑定，内容不变时保存不产生写入，事务中止时绑定保留"""
    bindings=[dict(workspace_id=EXTRA,purpose='code')]
    relation_database.write(lambda c:storage.replace_bindings(c,RID,bindings))
    with relation_database.read() as c:
        before=c.execute('SELECT xmin::text AS version,* FROM resident_workspace_bindings').fetchall()
        statements=[]
        storage.replace_bindings(QueryGuard(c,forbidden=('delete','insert'),statements=statements),RID,bindings)
        assert len(statements)==1
        assert c.execute('SELECT xmin::text AS version,* FROM resident_workspace_bindings').fetchall()==before
    with pytest.raises(RuntimeError):
        with relation_database.connect() as c:
            storage.replace_bindings(c,RID,[])
            raise RuntimeError('abort')
    with relation_database.read() as c:
        assert storage.bindings_for_residents(c,[RID])[RID]==bindings
    relation_database.write(lambda c:storage.replace_bindings(c,RID,[]))
    with relation_database.read() as c:
        assert storage.bindings_for_residents(c,[RID])[RID]==[]


@pytest.mark.parametrize('field',['role','managed','default_cwd','access','scope','root_id','binding_origin','unknown'])
def test_rejects_retired_fields(field):
    """Workspace 绑定中出现已退役或未知字段时被拒绝"""
    with pytest.raises(ValueError):
        storage.normalize_bindings([dict(workspace_id=EXTRA,purpose='',**{field:True})])


def test_duplicate_ids_rejected_and_empty_extra_list_valid():
    """Workspace 绑定 ID 重复时被拒绝，空的额外绑定列表有效"""
    item=dict(workspace_id=EXTRA,purpose='')
    assert storage.normalize_bindings([])==[]
    with pytest.raises(ValueError): storage.normalize_bindings([item,item])


def test_read_projection_follows_location_without_copying_paths(tmp_path):
    """Workspace 路径随存储位置变化且位置异常时 cwd 为空"""
    folder=tmp_path/LID
    own=str(folder/'residents'/RID/'workspace')
    records=[dict(resident_id=RID,lineage_id=LID,owned_workspace_id=OWNED)]
    relations={RID:[dict(workspace_id=EXTRA,purpose='code')]}
    paths={OWNED:own,EXTRA:str(tmp_path/'code')}
    value=storage.project_records(records,relations,paths,{LID:str(folder)})[0]
    assert value['owned_workspace']==dict(workspace_id=OWNED,path=own)
    assert value['workspace_paths']==[paths[EXTRA]]
    assert resident_catalog.default_cwd_path(value)==own
    paths[OWNED]='/wrong'
    value = storage.project_records(records, relations, paths, {LID: str(folder)})[0]
    assert value['owned_workspace']['path'] == '/wrong'
    assert value['default_cwd'] == ''
    assert 'path' not in relations[RID][0]


def test_a_saved_record_projects_like_its_canonical_read(tmp_path):
    """保存的 Resident 记录与规范读取生成相同的资源投影

    保存时按调用方顺序给出的额外 Workspace Binding 按 workspace_id 排序，与存储读回的顺序一致；Binding、Workspace 路径和资源描述的 relations 都与规范读取完全相同，资源投影发布时不会把这次保存判为过期。

    :module: lineage.service
    :importance: P1
    :aspects: functional, behavior
    """
    # A save queues the record for ResourceService; publishing compares it with
    # the record read back from storage, which lists bindings by workspace_id.
    folder=tmp_path/LID
    own=str(folder/'residents'/RID/'workspace')
    later='workspace_333333333333'
    paths={OWNED:own,EXTRA:str(tmp_path/'code'),later:str(tmp_path/'docs')}
    saved=dict(resident_id=RID,lineage_id=LID,owned_workspace_id=OWNED,owned_workspace=dict(workspace_id=OWNED,path=own),
               workspace_bindings=[dict(workspace_id=later,purpose='',path=paths[later]),
                                   dict(workspace_id=EXTRA,purpose='code',path=paths[EXTRA])])
    storage.apply_binding_projection(saved)
    stored={RID:[{field:binding[field] for field in storage.BINDING_FIELDS} for binding in saved['workspace_bindings']]}
    read=storage.project_records([dict(resident_id=RID,lineage_id=LID,owned_workspace_id=OWNED)],stored,paths,{LID:str(folder)})[0]
    assert saved['workspace_bindings']==read['workspace_bindings']
    assert saved['workspace_paths']==read['workspace_paths']==[paths[EXTRA],paths[later]]
    relations=lambda record:resource_client._resident_resource_projection(record)['descriptor']['relations']
    assert relations(saved)==relations(read)


def test_batch_paths_read_once_without_resource_facade(relation_database,tmp_path,monkeypatch):
    """批量读取 Workspace 路径只查询一次数据库并去重，不经资源服务"""
    store=get_workspace_store()
    store.ensure(OWNED,str(tmp_path/'own'));store.ensure(EXTRA,str(tmp_path/'code'))
    monkeypatch.setattr(resource_client,'get_resource_service',lambda:pytest.fail('recursive resource lookup'))
    with relation_database.read() as c:
        statements=[]
        paths=resource_client.workspace_paths_many([OWNED,EXTRA,OWNED],connection=QueryGuard(c,statements=statements))
    assert paths=={OWNED:str(tmp_path/'own'),EXTRA:str(tmp_path/'code')}
    assert len(statements)==1


def test_fork_replaces_owned_identity_once_and_inherits_only_extras(tmp_path,monkeypatch):
    """fork 时自有 Workspace 只替换一次为新 ID，只继承额外绑定"""
    folder=tmp_path/LID
    parent='resident_222222222222'
    record=dict(resident_id=RID,lineage_id=LID,folder_schema='lineage-folder-v3',workspace_path=str(folder),
        owned_workspace_id=OWNED,owned_workspace=dict(workspace_id=OWNED,path=str(folder/'residents'/parent/'workspace')),
        workspace_bindings=[dict(workspace_id=EXTRA,path=str(tmp_path/'code'),purpose='code')],workspace_paths=[])
    monkeypatch.setattr(resident_record_domain,'_new_workspace_id',lambda:'workspace_444444444444')
    resident_record_domain._set_resident_owned_workspace_binding(record)
    assert record['owned_workspace_id']=='workspace_444444444444'
    assert [b['workspace_id'] for b in record['workspace_bindings']]==[EXTRA]
    before=copy.deepcopy(record)
    resident_record_domain._set_resident_owned_workspace_binding(record)
    assert record==before
