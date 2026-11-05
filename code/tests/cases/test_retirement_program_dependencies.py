"""Instance retirement must not erase the program executing the uninstaller.

:design: decision_d88306d0bd17, decision_4aa13bc21e8e
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import directory_protocol_retire as retire
from tests.cases.test_workspace_unregistration import workspace
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace


def test_program_roots_come_from_all_manifest_deployments():
    instance=SimpleNamespace(deployments={'dev':{'code_root':'/apps/dev/code'},
                                        'release':{'code_root':'/apps/release/code'}})
    assert retire.program_roots(instance)=={Path('/apps/dev'),Path('/apps/release')}


@pytest.mark.parametrize('relative',['code','dependencies/sdk'])
def test_retirement_preserves_program_dependency_and_old_pending_cleanup(workspace,monkeypatch,relative):
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    program=workspace.root.with_name('installed-program')
    target=program/relative
    target.mkdir(parents=True)
    marker=target/'required-to-finish-uninstall'
    marker.write_text('platform-owned')
    workspace.store.ensure('workspace_abcdefabcdef',str(target))
    monkeypatch.setattr(retire,'program_roots',lambda instance:{program})
    queue=get_directory_cleanup_store()
    with queue.database.transaction() as connection:
        operation=queue.enqueue(target,owner_id='workspace_abcdefabcdef',operation_id='old-retire',
                                actor='instance-retirement',connection=connection)
    external=workspace.root.with_name('owned-project')
    external.mkdir()
    (external/'project-file').write_text('project-owned')
    workspace.store.ensure('workspace_aabbccddeeff',str(external))
    result=retire.cleanup_external_owned()
    assert marker.read_text()=='platform-owned'
    assert not external.exists()
    assert str(target) not in result['external_owned_deleted']
    assert queue.status(cleanup_id=operation)[0]['status']=='pending'
