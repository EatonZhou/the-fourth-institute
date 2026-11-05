"""Directory cleanup preserves entry identity across symlink replacements.

:design: decision_4aa13bc21e8e
"""
from pathlib import Path
import os

import pytest

from tests.cases.test_workspace_unregistration import workspace
from tests.cases.test_workspace_resource_rename import WID, workspace as resource_workspace


def test_registered_area_symlink_deletes_only_link(workspace):
    from management_common.instance_manifest import current_instance
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    area = current_instance().area('projects')
    owned, foreign = area / 'owned', area / 'foreign'
    owned.mkdir(parents=True)
    foreign.mkdir()
    (foreign / 'keep.txt').write_text('unrelated area directory')
    workspace.store.ensure(WID, str(owned), move=True)
    owned.rmdir()
    owned.symlink_to(foreign, target_is_directory=True)
    workspace.service.unregister(WID, actor='alice')
    get_directory_cleanup_store().run_pending()
    assert (foreign / 'keep.txt').exists()
    assert not owned.is_symlink()


@pytest.mark.skipif(os.name == 'nt', reason='Windows pins parents against renaming')
def test_cleanup_parent_swap_cannot_redirect_deletion(tmp_path, monkeypatch):
    from management_common import directory_paths
    parent, outside = tmp_path / 'parent', tmp_path / 'outside'
    victim = parent / 'victim'
    victim.mkdir(parents=True)
    foreign = outside / 'victim'
    foreign.mkdir(parents=True)
    (foreign / 'keep.txt').write_text('unrelated host data')
    original = directory_paths.validate_removal
    def swapped(path):
        original(path)
        parent.rename(tmp_path / 'saved-parent')
        parent.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(directory_paths, 'validate_removal', swapped)
    directory_paths.remove_path(victim)
    assert (foreign / 'keep.txt').exists()
    assert not (tmp_path / 'saved-parent' / 'victim').exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX literal filename semantics')
@pytest.mark.parametrize('name', ['literal\\directory', 'with\nnewline'])
def test_area_cleanup_keeps_native_filename(name):
    from management_common.instance_manifest import current_instance
    from management_common.directory_paths import cleanup_location, cleanup_path, remove_path
    from management_common.instance_locations import located_path
    path = current_instance().area('projects') / name
    path.mkdir(parents=True)
    location = cleanup_location(path)
    assert cleanup_path(location) == path
    assert Path(located_path(location)) == path
    remove_path(path)
    assert not path.exists()


def test_cleanup_waits_for_move_and_does_not_starve_other_locations(workspace):
    from datetime import datetime, timedelta, timezone
    from management_common.directory_move_store import DirectoryMoveStore
    from management_common.directory_retention import RetentionStore
    from management_portal.directory_move_owner import relocate_nested
    now = datetime.now(timezone.utc)
    artifact = workspace.root / 'expired'
    artifact.write_text('owned expired output')
    retention = RetentionStore()
    retention.declare(artifact, category='background', owner_id='job', finished_at=now)
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('moved')
    operation = mover.prepare(workspace.root, target, owner_id=WID,
                              actor='alice', terminate=lambda _: None)
    retention.sweep(now=now + timedelta(days=8))
    other = workspace.root.with_name('independent-cleanup')
    other.mkdir()
    with mover.cleanup.database.transaction() as connection:
        mover.cleanup.enqueue(other, owner_id='other', operation_id='other-delete', actor='alice', connection=connection)
    completed = mover.cleanup.run_pending(limit=1)
    assert len(completed) == 1 and completed[0]['owner_id'] == 'other'
    assert not other.exists()
    mover.database.write(lambda connection: mover.commit(operation, connection=connection, update=relocate_nested))
    mover.finish(operation, dependencies=lambda *_: None)
    mover.cleanup.run_pending()
    assert not (target / 'expired').exists()
