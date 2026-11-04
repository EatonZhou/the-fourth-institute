"""An old instance's live Residents must have their computed owned Workspace."""
import json
import shutil
import pytest
from management_common.instance_manifest import load
from management_common.portal_database import portal_database, _database_schema
from scripts import instance_resident_relocation as migration
from scripts.instance_storage_migration import Move
from tests.cases.instance_test_support import write_instance

RID = 'resident_111111111111'
LID = 'lineage_222222222222'

@pytest.fixture
def instance(tmp_path, monkeypatch):
    root = write_instance(tmp_path / 'instance')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    return load(root)


def setup_paths(instance, *, placed=False):
    old = instance.area('projects') / 'P/agents/Old_name/residents' / RID
    new = instance.area('projects') / f'P/agents/{LID}/residents' / RID
    physical = new if placed else old
    (physical / 'workspace').mkdir(parents=True)
    (physical / 'resident.md').write_text('identity')
    return old, new, [(RID, LID, 'P', instance.reference(old / 'workspace'))]


def seed_owner(*, resident=RID, lineage=LID):
    from management_portal.society_service.state_store import get_lineage_data_store
    from management_portal.resources.workspace_store import get_workspace_store
    from management_common.instance_manifest import current_instance
    get_workspace_store().ensure('workspace_000000000000', str(current_instance().area('projects') / 'seed-workspace'))
    database = get_lineage_data_store().database
    with database.transaction() as connection:
        for table, key, identifier in [('residents', 'resident_id', resident), ('lineages', 'lineage_id', lineage)]:
            extra_columns = ',owned_workspace_id' if table == 'residents' else ''
            extra_values = ",'workspace_000000000000'" if table == 'residents' else ''
            connection.execute(f'INSERT INTO {table}({key},directory_json{extra_columns}) VALUES (%s,%s::jsonb{extra_values}) ON CONFLICT DO NOTHING',
                               (identifier, json.dumps({'owner_id': identifier})))
    return database


@pytest.mark.parametrize('placed', [False, True])
def test_old_named_and_already_moved_residents_are_planned(instance, placed):
    old, new, rows = setup_paths(instance, placed=placed)
    report = migration.plan(instance, rows)
    assert not report['conflicts']
    assert len(report['aliases' if placed else 'moves']) == 1


def test_membership_wins_over_a_different_lineage_directory(instance):
    old, new, rows = setup_paths(instance)
    other = old.parents[1].with_name('lineage_333333333333') / 'residents' / RID
    other.parent.mkdir(parents=True)
    old.rename(other)
    rows[0] = (*rows[0][:3], instance.reference(other / 'workspace'))
    report = migration.plan(instance, rows)
    assert not report['conflicts']
    assert report['moves'][0]['target'] == str(new)


def test_collision_or_missing_both_is_never_silently_accepted(instance):
    old, new, rows = setup_paths(instance)
    (old/'workspace').rmdir()
    assert migration.plan(instance,rows)['conflicts'][0]['reason']=='owned Workspace directory missing'
    (old/'workspace').mkdir()
    (new / 'workspace').mkdir(parents=True)
    assert migration.plan(instance, rows)['conflicts'][0]['reason'] == 'target exists'
    for path in (old, new):
        shutil.rmtree(path)
    assert migration.plan(instance, rows)['conflicts'][0]['reason'] == 'source and destination Workspace both missing'


def test_whole_upgrade_plans_using_future_lineage_prefix(instance):
    old, new, rows = setup_paths(instance)
    legacy_root = instance.area('projects') / 'P/knowledge_base/agents/Old_name'
    legacy_root.parent.mkdir(parents=True)
    old.parents[1].rename(legacy_root)
    rows[0] = (*rows[0][:3], str(legacy_root / 'residents' / RID / 'workspace'))
    report = migration.plan(instance, rows, [Move(str(legacy_root), str(old.parents[1]), 'project-lineage')])
    assert not report['conflicts']
    assert report['moves'][0]['source'] == str(old)
    assert report['moves'][0]['target'] == str(new)


def test_apply_moves_whole_folder_and_nested_current_registrations(instance, monkeypatch):
    old, new, rows = setup_paths(instance)
    seed_owner()
    from management_portal.resources.workspace_store import get_workspace_store
    workspace = get_workspace_store()
    wid = 'workspace_111111111111'
    workspace.ensure(wid, str(old / 'workspace'), owner_id=RID)
    schema = _database_schema('relocation_probe')
    database = portal_database(schema='relocation_probe')
    with database.transaction() as c:
        c.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        c.execute(f'CREATE TABLE "{schema}".registrations (id TEXT PRIMARY KEY,path TEXT,data JSONB)')
        c.execute(f'INSERT INTO "{schema}".registrations VALUES (%s,%s,%s::jsonb)',
                  ('workspace-stable-id', instance.reference(old / 'workspace'),
                   json.dumps({'repo':str(old / 'workspace/repo'),'binding':'workspace-stable-id'})))
    sessions = []
    from management_portal import provider_session_relocation
    monkeypatch.setattr(provider_session_relocation, 'relocate_provider_sessions',
                        lambda old,new: sessions.append((old,new)) or {'errors':[],'conflicts':[]})
    outcome = migration.apply(instance, migration.plan(instance, rows))
    assert not old.exists() and (new / 'resident.md').read_text() == 'identity'
    assert len(outcome['directory_operations']) == 1
    from management_common.instance_locations import located_path
    assert located_path(workspace.rows([wid])[wid]['path']) == str(new / 'workspace')
    with database.snapshot() as c:
        row=c.execute(f'SELECT id,path,data FROM "{schema}".registrations').fetchone()
        assert row[0] == 'workspace-stable-id'
        assert row[1] == instance.reference(old / 'workspace')
        assert row[2] == {'repo':str(old / 'workspace/repo'),'binding':'workspace-stable-id'}
    assert sessions == [(old,new)]
    assert not migration.plan(instance, [(RID,LID,'P',instance.reference(new/'workspace'))])['residents']


def test_database_failure_reverses_directory_move(instance, monkeypatch):
    old, new, rows = setup_paths(instance)
    seed_owner()
    def fail(*args, **kwargs):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(migration, 'relocate_nested', fail)
    with pytest.raises(RuntimeError,match='database unavailable'):
        migration.apply(instance, migration.plan(instance, rows))
    assert (old/'resident.md').read_text()=='identity' and not new.exists()


def test_lineage_remainders_use_database_project_and_preserve_collisions(instance, monkeypatch):
    old, new, rows = setup_paths(instance)
    report = migration.plan(instance, rows)
    old_root = old.parents[1]
    owner = 'lineage_444444444444'
    seed_owner(lineage=owner)
    (old_root/'lineage.json').write_text(json.dumps({'lineage_id':owner,'project_id':'STALE'}))
    (old_root/'shared').mkdir()
    (old_root/'shared'/'notes.md').write_text('keep')
    from management_portal.society_service.lineage_storage import lineage_folder_for_project
    real = lineage_folder_for_project(owner,'Other')
    real.mkdir(parents=True)
    (real/'LINEAGE.md').write_text('current')
    (old_root/'LINEAGE.md').write_text('historical')
    shutil.rmtree(old)
    remainder = migration.plan_remainders(instance,report,{owner:'Other',LID:'P'})
    assert not remainder['conflicts']
    assert remainder['owners'][0]['target']==str(real/'legacy-layout'/'Old_name')
    from management_portal import provider_session_relocation
    monkeypatch.setattr(provider_session_relocation,'relocate_provider_sessions',lambda *a:{'errors':[],'conflicts':[]})
    migration.apply(instance,remainder)
    assert not old_root.exists()
    assert (real/'LINEAGE.md').read_text()=='current'
    assert (real/'legacy-layout'/'Old_name'/'LINEAGE.md').read_text()=='historical'
    assert (real/'legacy-layout'/'Old_name'/'shared'/'notes.md').read_text()=='keep'


def test_multiple_old_folders_keep_separate_resource_directories(instance, monkeypatch):
    """多个旧目录归属同一 Lineage 时保留独立目录和内容，不合并登记身份。"""
    old,new,rows=setup_paths(instance)
    seed_owner()
    report=migration.plan(instance,rows)
    other=old.parents[1].with_name('Other_name')
    other.mkdir()
    for root in (old.parents[1],other):
        (root/'shared').mkdir()
        (root/'shared'/'same.md').write_text(root.name)
        (root/'lineage.json').write_text(json.dumps({'lineage_id':LID}))
    report['residents'].append({**report['residents'][0], 'source':str(other/'residents'/RID)})
    shutil.rmtree(old)
    from management_portal.resources.workspace_store import get_workspace_store
    store = get_workspace_store()
    paths_by_id = {'workspace_111111111111': old.parents[1], 'workspace_222222222222': other,
                   'workspace_333333333333': new.parents[1]}
    for identity, path in paths_by_id.items():
        store.ensure(identity, str(path))
    remainder=migration.plan_remainders(instance,report,{LID:'P'})
    assert not remainder['conflicts']
    from management_portal import provider_session_relocation
    monkeypatch.setattr(provider_session_relocation,'relocate_provider_sessions',lambda *a:{'errors':[],'conflicts':[]})
    migration.apply(instance,remainder)
    target=new.parents[1]
    assert (target/'legacy-layout'/'Old_name'/'shared'/'same.md').read_text()=='Old_name'
    assert (target/'legacy-layout'/'Other_name'/'shared'/'same.md').read_text()=='Other_name'
    assert not other.exists() and not old.parents[1].exists()
    assert {wid: row['path'] for wid, row in store.rows(list(paths_by_id)).items()} == {
        'workspace_111111111111': str(target/'legacy-layout'/'Old_name'),
        'workspace_222222222222': str(target/'legacy-layout'/'Other_name'),
        'workspace_333333333333': str(target)}


def test_already_moved_owner_only_updates_registration(instance):
    old, new, rows = setup_paths(instance, placed=True)
    seed_owner()
    from management_portal.resources.workspace_store import get_workspace_store
    from management_common.directory_move_store import DirectoryMoveStore
    store = get_workspace_store()
    wid = 'workspace_111111111111'
    store.ensure(wid, str(old / 'workspace'), owner_id=RID)
    result = migration.apply(instance, migration.plan(instance, rows))
    operation = DirectoryMoveStore().get(result['directory_operations'][0])
    assert operation['mode'] == 'external' and operation['phase'] == 'completed'
    assert (new / 'resident.md').read_text() == 'identity'
    assert not DirectoryMoveStore().cleanup.pending_at(old)
