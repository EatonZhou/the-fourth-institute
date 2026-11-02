"""Existing copies and current Handoff locations use their registered owners.

:module: resident.fork-handoff
:design: decision_4aa13bc21e8e, decision_7ef392b5f60d
"""
from pathlib import Path
import pytest
from management_common.directory_move_store import DirectoryMoveStore
from management_portal import directory_move_owner
from management_portal.society_service import directory_relocation
from management_portal.resources.workspace_store import get_workspace_store
from management_portal.society_service import handoff_location_owner as owner
from management_portal.society_service import lineage_handoffs, lineage_state, lineage_resident_workspaces
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_handoff_directory_creation import draft
from tests.cases.test_lineage_backend import lineage_store


def existing(lineage_store, tmp_path, method):
    lid, handoff, resident, source = draft(lineage_store, tmp_path, method)
    lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    # Writer completion is persisted when the execution lease exits.
    h = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    item = h['items'][0]
    wid = item['target']['workspace_id']
    store = get_workspace_store()
    row = store.rows([wid])[wid]
    old = tmp_path / 'old-copy'
    DirectoryMoveStore().move(Path(row['path']), old, owner_id=wid, origin='created', actor='sample_admin',
        terminate=lambda _: None, database=get_lineage_data_store().database,
        update=directory_relocation.relocate_nested, dependencies=directory_relocation.reconcile_dependencies)
    DirectoryMoveStore().cleanup.run_pending()
    # Historical lineage ownership is fixture input, not the repair mechanism.
    store.database.write(lambda c: c.execute(f'UPDATE {store.table} SET owner_id=%s WHERE workspace_id=%s', (lid, wid)))
    row = store.rows([wid])[wid]
    args = dict(workspace_id=wid, expected_version=row['updated_at'], expected_owner=lid,
                expected_path=row['path'], operation_id='test-existing-move')
    return lid, h, item, resident, source, args


@pytest.mark.parametrize('method', ['filesystem_copy', 'git_worktree'])
def test_move_existing_identity_content_binding_nested_and_source(lineage_store, tmp_path, method):
    lid, h, item, resident, source, args = existing(lineage_store, tmp_path, method)
    old = Path(args['expected_path'])
    (old / 'edited').write_text('local changes survive')
    from management_portal.provider_session_relocation import kimi_home
    import json
    home = kimi_home(); home.mkdir(parents=True, exist_ok=True)
    (home / 'workspaces.json').write_text(json.dumps({'cwd': str(old)}))
    nested = old / 'nested'; nested.mkdir()
    store = get_workspace_store()
    store.ensure('workspace_abcdefabcdef', str(nested), origin='adopted')
    result = owner.relocate_copy(lid, h['id'], item['item_id'], 'sample_admin', **args)
    row = result['workspace']; new = Path(row['path'])
    assert row['workspace_id'] == args['workspace_id'] == row['owner_id']
    assert json.loads((home / 'workspaces.json').read_text())['cwd'] == str(new)
    assert row['origin'] == 'created' and (new / 'edited').read_text() == 'local changes survive'
    assert store.rows(['workspace_abcdefabcdef'])['workspace_abcdefabcdef']['path'] == str(new / 'nested')
    bindings = lineage_resident_workspaces.get_node_workspaces(lid, resident, 'sample_admin')['workspaces']
    assert any(x['workspace_id'] == row['workspace_id'] and x['path'] == str(new) for x in bindings)
    current = lineage_handoffs.get_handoff(lid, h['id'], 'sample_admin')['handoff']['items'][0]
    assert current['target']['path'] == current['result']['target_path'] == str(new)
    from management_portal.society_service.definition_collections import upsert
    get_lineage_data_store().database.write(lambda c: upsert(c, 'lineage_handoff_invocations',
        {'edge_id': h['id']}, {'cwd': source['path']}))
    moved = Path(source['path']).with_name('source-moved')
    DirectoryMoveStore().move(Path(source['path']), moved, owner_id=source['workspace_id'], origin='created',
        actor='sample_admin', terminate=lambda _: None, database=get_lineage_data_store().database,
        update=directory_relocation.relocate_nested, dependencies=directory_relocation.reconcile_dependencies)
    current = lineage_handoffs.get_handoff(lid, h['id'], 'sample_admin')['handoff']['items'][0]
    assert current['source']['path'] == str(moved)
    from management_common.instance_locations import located_path
    with get_lineage_data_store().database.read() as c:
        actual = c.execute('SELECT cwd FROM lineage_handoff_invocations WHERE edge_id=%s', (h['id'],)).fetchone()[0]
    assert located_path(actual) == str(moved)
    assert current['attempts'] == item['attempts']


@pytest.mark.parametrize('method', ['filesystem_copy', 'git_worktree'])
def test_adopted_copy_rejected(lineage_store, tmp_path, method):
    lid, h, item, resident, source, args = existing(lineage_store, tmp_path, method)
    store = get_workspace_store()
    store.database.write(lambda c: c.execute(f"UPDATE {store.table} SET origin='adopted' WHERE workspace_id=%s", (args['workspace_id'],)))
    with pytest.raises(ValueError, match='created'):
        owner.relocate_copy(lid, h['id'], item['item_id'], 'sample_admin', **args)
    assert Path(args['expected_path']).exists()


def test_missing_target_preserves_relationship_and_attempts(lineage_store, tmp_path):
    lid, h, resident, source = draft(lineage_store, tmp_path)
    item = h['items'][0]
    owner.reconcile_locations(lid, h['id'], 'sample_admin')
    current = lineage_handoffs.get_handoff(lid, h['id'], 'sample_admin')['handoff']
    assert current['id'] == h['id'] and current['status'] == h['status']
    assert current['items'][0]['target'].get('path', '') == ''
    assert current['items'][0]['source']['path'] == source['path']
    assert not current['items'][0].get('attempts')


@pytest.mark.parametrize('recovery', ['finish', 'maintenance'])
def test_move_dependency_failure_recovers_same_journal(lineage_store, tmp_path, monkeypatch, recovery):
    lid, h, item, resident, source, args = existing(lineage_store, tmp_path, 'filesystem_copy')
    original = directory_move_owner.reconcile_dependencies
    monkeypatch.setattr(directory_move_owner, 'reconcile_dependencies', lambda *_: (_ for _ in ()).throw(OSError('dependency crash')))
    with pytest.raises(RuntimeError, match='dependency crash'):
        owner.relocate_copy(lid, h['id'], item['item_id'], 'sample_admin', **args)
    assert DirectoryMoveStore().get(args['operation_id'])['phase'] == 'committed'
    monkeypatch.setattr(directory_move_owner, 'reconcile_dependencies', original)
    if recovery == 'finish':
        DirectoryMoveStore().finish(args['operation_id'], dependencies=directory_relocation.reconcile_dependencies)
    else:
        receipts = directory_relocation.recover_pending_moves()
        assert any(row['operation_id'] == args['operation_id'] and row['phase'] == 'completed' for row in receipts)
    assert DirectoryMoveStore().get(args['operation_id'])['phase'] == 'completed'


def test_git_old_alias_only_repairs_proven_admin(tmp_path):
    from tests.cases.test_lineage_handoff_backend import _git
    repo = tmp_path / 'repo'; repo.mkdir()
    _git(repo, 'init', '-b', 'main'); _git(repo, 'config', 'user.name', 'Test')
    _git(repo, 'config', 'user.email', 'test@example.invalid')
    (repo / 'file').write_text('content'); _git(repo, 'add', '.'); _git(repo, 'commit', '-m', 'initial')
    old = tmp_path / 'old'; new = tmp_path / 'new'; other = tmp_path / 'other'
    _git(repo, 'worktree', 'add', '--detach', str(old)); _git(repo, 'worktree', 'add', '--detach', str(other))
    admin = Path((old / '.git').read_text().strip().removeprefix('gitdir: '))
    other_admin = Path((other / '.git').read_text().strip().removeprefix('gitdir: '))
    untouched = (other_admin / 'gitdir').read_bytes()
    (admin / 'gitdir').write_text(str(tmp_path / 'older-alias/.git'))
    old.rename(new)
    directory_move_owner.repair_git_worktrees(old, new)
    assert (admin / 'gitdir').read_text().strip() == str(new / '.git')
    assert (other_admin / 'gitdir').read_bytes() == untouched
    (admin / 'gitdir').write_text(str(other / '.git'))
    with pytest.raises(FileExistsError):
        directory_move_owner.repair_git_worktrees(old, new)
    assert (admin / 'gitdir').read_text() == str(other / '.git')


def test_stale_snapshot_and_permission_fail_before_move(lineage_store, tmp_path, monkeypatch):
    from management_portal import permission_system
    from management_portal.resources.errors import ResourceVersionConflict
    lid, h, item, resident, source, args = existing(lineage_store, tmp_path, 'filesystem_copy')
    stale = {**args, 'expected_version': 'stale'}
    with pytest.raises(ResourceVersionConflict):
        owner.relocate_copy(lid, h['id'], item['item_id'], 'sample_admin', **stale)
    monkeypatch.setattr(permission_system, 'permitted_ids', lambda *a, **k: [])
    with pytest.raises(PermissionError):
        owner.relocate_copy(lid, h['id'], item['item_id'], 'sample_admin', **args)
    assert Path(args['expected_path']).is_dir()


def test_registration_failure_restores_files_owner_and_references(lineage_store, tmp_path, monkeypatch):
    lid, h, item, resident, source, args = existing(lineage_store, tmp_path, 'filesystem_copy')
    original = directory_move_owner.relocate_nested
    def fail(*a):
        original(*a)
        raise RuntimeError('registration conflict')
    monkeypatch.setattr(directory_move_owner, 'relocate_nested', fail)
    with pytest.raises(RuntimeError, match='registration conflict'):
        owner.relocate_copy(lid, h['id'], item['item_id'], 'sample_admin', **args)
    row = get_workspace_store().rows([args['workspace_id']])[args['workspace_id']]
    assert row['owner_id'] == lid and row['path'] == args['expected_path']
    assert Path(row['path']).is_dir()
    assert DirectoryMoveStore().get(args['operation_id'])['phase'] == 'aborted'
    current = lineage_handoffs.get_handoff(lid, h['id'], 'sample_admin')['handoff']['items'][0]
    assert current['target']['path'] == current['result']['target_path'] == row['path']


def test_legacy_missing_runtime_target_does_not_acquire_readiness(lineage_store, tmp_path):
    from management_portal.society_service import relation_work_storage
    from management_common.json_columns import json_value
    from management_common.instance_locations import located_path
    lid, h, resident, source = draft(lineage_store, tmp_path)
    item = h['items'][0]
    db = get_lineage_data_store().database
    def historical(c):
        c.execute('UPDATE lineage_handoff_items SET source_path=%s,target_path=%s WHERE edge_id=%s',
                  ('/data/old-source', '/data/old-target', h['id']))
        relation_work_storage.put(c, lid, {}, {h['id']: {'handoff': {'items': [
            {'item_id': item['item_id'], 'target': {'path': '/data/older-target'}}]}}})
    db.write(historical)
    receipt = owner.reconcile_locations(lid, h['id'], 'sample_admin')
    assert receipt[0]['previous_target_path'] == '/data/old-target'
    with db.read() as c:
        stored = c.execute('SELECT * FROM lineage_handoff_items WHERE edge_id=%s', (h['id'],)).fetchone()
        progress = json_value(c.execute('SELECT progress_json FROM workspace_handoff_work WHERE edge_id=%s', (h['id'],)).fetchone()[0])
    assert located_path(stored['source_path']) == source['path']
    assert stored['target_path'] == '' and not stored['target_workspace_id']
    assert 'status' not in progress and 'attempts' not in progress['items'][0]
    assert progress['items'][0]['target']['path'] == ''


def test_git_unproven_admin_and_symlink_are_rejected(tmp_path):
    new = tmp_path / 'new'; new.mkdir()
    admin = tmp_path / 'unrelated'; admin.mkdir()
    pointer = new / '.git'
    pointer.write_text('gitdir: ' + str(admin))
    backlink = admin / 'gitdir'; backlink.write_text(str(tmp_path / 'missing/.git'))
    before = backlink.read_bytes()
    with pytest.raises(ValueError, match='Unproven'):
        directory_move_owner.repair_git_worktrees(tmp_path / 'old', new)
    assert backlink.read_bytes() == before
    pointer.unlink(); pointer.symlink_to(backlink)
    with pytest.raises(ValueError, match='symlink'):
        directory_move_owner.repair_git_worktrees(tmp_path / 'old', new)
    assert backlink.read_bytes() == before


def current_locations(handoff_id):
    """Read persisted references directly; projections must not hide stale storage."""
    import json
    from management_common.instance_locations import located_path
    with get_lineage_data_store().database.read() as connection:
        item = connection.execute('SELECT * FROM lineage_handoff_items WHERE edge_id=%s', (handoff_id,)).fetchone()
        progress = connection.execute(
            'SELECT progress_json FROM workspace_handoff_work WHERE edge_id=%s', (handoff_id,)).fetchone()[0]
        if isinstance(progress, str):
            progress = json.loads(progress)
    return located_path(item['source_path']), located_path(item['target_path']), progress


@pytest.mark.parametrize('fail_storage', [False, True])
def test_project_move_enlists_handoff_storage_atomically(lineage_store, tmp_path, monkeypatch, fail_storage):
    from management_portal import project_rename, project_registry
    from management_portal.society_service import project_domain, handoff_location_storage
    from tests.cases.registry_support import write_projects
    lid, h, resident, source = draft(lineage_store, tmp_path)
    h = lineage_handoffs.execute_handoff(lid, h['id'], 'sample_admin')['handoff']
    old = lineage_state._lineage_dir(lid)
    new = old.parent / 'New'
    write_projects({'projects': [{'id': 'Old', 'path': str(old),
        'directory': {'origin': 'created', 'owner_id': 'Old'}}]})
    project_registry.reset_cache_for_tests()
    monkeypatch.setattr(project_rename, 'PROJECTS_ROOT', old.parent)
    monkeypatch.setattr(project_rename, 'external_blockers', lambda *_: [])
    monkeypatch.setattr(project_rename, 'external_references', lambda *_: [])
    before = current_locations(h['id'])
    synchronize = handoff_location_storage.synchronize
    if fail_storage:
        def fail(*args, **kwargs):
            synchronize(*args, **kwargs)
            raise RuntimeError('handoff storage failed')
        monkeypatch.setattr(handoff_location_storage, 'synchronize', fail)
        with pytest.raises(RuntimeError, match='handoff storage failed'):
            project_domain.rename_project('Old', 'New', 'sample_admin')
        assert old.is_dir() and not new.exists()
        assert current_locations(h['id']) == before
        assert get_workspace_store().rows([source['workspace_id']])[source['workspace_id']]['path'] == source['path']
        assert project_registry.project('Old') and not project_registry.project('New')
        return
    project_domain.rename_project('Old', 'New', 'sample_admin')
    after = current_locations(h['id'])
    for previous, current in zip(before[:2], after[:2]):
        assert current == str(new / Path(previous).relative_to(old))
        assert Path(current).is_dir()
    assert after[2]['items'][0]['attempts'] == before[2]['items'][0]['attempts']
    from management_common.instance_locations import located_path
    assert located_path(after[2]['items'][0]['result']['target_path']) == after[1]


def test_area_move_keeps_handoff_current_paths_and_attempts(lineage_store, tmp_path):
    import json
    from management_common.instance_manifest import current_instance
    from management_common.instance_areas import move_area
    from management_common.instance_locations import located_path
    lid, h, resident, source = draft(lineage_store, tmp_path)
    h = lineage_handoffs.execute_handoff(lid, h['id'], 'sample_admin')['handoff']
    old = lineage_state._lineage_dir(lid).parent
    instance = current_instance()
    document = dict(instance.document)
    document['areas'] = {**document.get('areas', {}), 'lineages': str(old)}
    (instance.root / 'instance.json').write_text(json.dumps(document))
    # Normalize the fixture's absolute current paths into area references before
    # switching the manifest, just as installed registered locations are stored.
    get_lineage_data_store().database.write(lambda c: directory_relocation.relocate_nested(c, old, old))
    before = current_locations(h['id'])
    target = tmp_path / 'moved-lineages-area'
    move_area(instance.root, 'lineages', target)
    directory_relocation.relocate_area(old, target)
    after = current_locations(h['id'])
    for previous, current in zip(before[:2], after[:2]):
        assert current == str(target / Path(previous).relative_to(old))
        assert Path(current).is_dir()
    assert after[2]['items'][0]['attempts'] == before[2]['items'][0]['attempts']
    assert located_path(after[2]['items'][0]['result']['target_path']) == after[1]


def test_copy_cross_device_uses_one_admission_gate(lineage_store, tmp_path, monkeypatch):
    from tests.cases.test_directory_move_safety import cross_device
    lid, h, item, resident, source, args = existing(lineage_store, tmp_path, 'filesystem_copy')
    cross_device(monkeypatch, Path(args['expected_path']))
    receipt = owner.relocate_copy(lid, h['id'], item['item_id'], 'sample_admin', **args)
    assert DirectoryMoveStore().get(receipt['operation_id'])['phase'] == 'completed'
    assert Path(receipt['workspace']['path']).is_dir()


@pytest.mark.parametrize('location', ['source', 'target'])
def test_copy_rejects_active_script_without_terminating_it(lineage_store, tmp_path, location):
    from management_portal.society_service.script_runs import store as scripts
    lid, h, item, resident, source, args = existing(lineage_store, tmp_path, 'filesystem_copy')
    path = Path(args['expected_path']) if location == 'source' else (
        lineage_state._lineage_dir(lid) / 'handoff-workspaces' / h['id'] / item['item_id'])
    scripts.admit({'script_run_id': 'layout-active-script', 'status': 'running',
        'lineage_id': lid, 'caller_id': 'sample_admin', 'cwd': str(path),
        'path': str(path / 'tool.py'), 'unit': 'isolated'})
    with pytest.raises(RuntimeError, match='active execution users'):
        owner.relocate_copy(lid, h['id'], item['item_id'], 'sample_admin', **args)
    assert Path(args['expected_path']).is_dir()
    assert scripts.get('layout-active-script')['status'] == 'running'


def test_instance_prefix_rewrite_delegates_mixed_handoff_history(lineage_store, tmp_path):
    from scripts.tfi_relocate_instance import migrate_references
    lid, h, resident, source = draft(lineage_store, tmp_path)
    lineage_handoffs.execute_handoff(lid, h['id'], 'sample_admin')
    old = lineage_state._lineage_dir(lid)
    target = old.with_name('instance-prefix-moved')
    before = current_locations(h['id'])
    old.rename(target)
    migrate_references(old, target)
    after = current_locations(h['id'])
    for previous, current in zip(before[:2], after[:2]):
        assert current == str(target / Path(previous).relative_to(old))
        assert Path(current).is_dir()
    assert after[2]['items'][0]['attempts'] == before[2]['items'][0]['attempts']


def test_copy_checks_execution_owner_before_society_ack(lineage_store, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from management_portal.society_service import resident_execution_work, resident_catalog, execution_client
    lid, h, item, resident, source, args = existing(lineage_store, tmp_path, 'filesystem_copy')
    record = resident_catalog.get_catalog().get(resident)
    work = {'execution_id': 'layout-awaiting-ack', 'submitted_at': '2026-11-02T00:00:00Z',
            'actor': 'sample_admin', 'prompt_cwd': args['expected_path']}
    resident_execution_work.write(record, work)
    observed = []
    state = {'status': 'running'}
    def wait(execution_id, *, actor, timeout_seconds):
        assert execution_id == work['execution_id'] and actor == 'sample_admin'
        assert timeout_seconds == 0
        observed.append(state['status'])
        return state
    monkeypatch.setattr(execution_client, 'service', lambda: SimpleNamespace(wait=wait))
    with pytest.raises(RuntimeError, match='active execution users'):
        owner.relocate_copy(lid, h['id'], item['item_id'], 'sample_admin', **args)
    state['status'] = 'cancelled'
    receipt = owner.relocate_copy(lid, h['id'], item['item_id'], 'sample_admin', **args)
    assert DirectoryMoveStore().get(receipt['operation_id'])['phase'] == 'completed'
    assert observed[0] == 'running' and 'cancelled' in observed
    assert resident_execution_work.is_active(get_lineage_data_store().resident_execution_record(resident))


@pytest.mark.parametrize('missing_backlink', [False, True])
def test_instance_prefix_completes_git_dependencies(lineage_store, tmp_path, missing_backlink):
    """实例前缀搬迁完成时，交接 worktree 的双向 Git 指针也必须有效。"""
    import subprocess
    from scripts.tfi_relocate_instance import migrate_references
    lid, handoff, resident, source = draft(lineage_store, tmp_path, 'git_worktree')
    lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    before = current_locations(handoff['id'])
    old = lineage_state._lineage_dir(lid)
    new = old.with_name('instance-prefix-git-moved')
    admin = Path((Path(before[1]) / '.git').read_text().strip().removeprefix('gitdir: '))
    backlink = new / admin.relative_to(old) / 'gitdir'
    old.rename(new)
    if missing_backlink:
        pointer = backlink.read_text()
        backlink.unlink()
        with pytest.raises(FileNotFoundError, match='backlink is missing'):
            migrate_references(old, new)
        backlink.write_text(pointer)
    result = migrate_references(old, new)
    after = current_locations(handoff['id'])
    target = Path(after[1])
    assert not result['provider_sessions']['errors']
    assert after[2]['items'][0]['attempts'] == before[2]['items'][0]['attempts']
    git = subprocess.run(['git', '-C', str(target), 'rev-parse', '--git-dir'],
                         capture_output=True, text=True)
    assert git.returncode == 0, git.stderr
    admin = Path((target / '.git').read_text().strip().removeprefix('gitdir: '))
    assert (admin / 'gitdir').read_text().strip() == str(target / '.git')
