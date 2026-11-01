"""Directory reservations gate execution without changing persistent intake policy.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
import pytest
from management_common.directory_move_store import DirectoryMoveStore
from management_portal.society_service import resident_catalog
from management_portal.society_service import resident_lifecycle_domain as lifecycle
from tests.cases.test_directory_move_users import users, cross_device
from tests.cases.test_resident_folder_creation_contract import future_creation_store


def intake(users):
    return [resident_catalog.get_catalog().get(r['resident_id'])['intake_open'] for r in users.records]


def test_unwound_process_does_not_leave_directory_users_intake_closed(users, monkeypatch):
    """Model loss of the move scope: recovery cannot rely on its finally block."""
    from management_common import directory_move_store as module
    cross_device(monkeypatch, users.source)
    mover = DirectoryMoveStore()
    scope = lifecycle.directory_move_execution_scope([users.records[0]],actor='alice')
    stop = scope.__enter__()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(module.shutil,'copytree',lambda *a,**kw: (_ for _ in ()).throw(SystemExit('process lost')))
            patch.setattr(mover,'abort',lambda *_:None)
            with pytest.raises(SystemExit):
                mover.prepare(users.source,users.source.with_name('target'),owner_id='move-owner',origin='created',actor='alice',terminate=stop)
        DirectoryMoveStore().recover(dependencies=lambda *_:None)
        assert intake(users) == [True,True,False]
    finally:
        scope.__exit__(None,None,None)


def test_overlapping_move_users_preserve_policy_when_scopes_finish_out_of_order(users):
    first = lifecycle.directory_move_execution_scope(users.records,actor='alice')
    second = lifecycle.directory_move_execution_scope(users.records,actor='alice')
    a,b = first.__enter__(),second.__enter__()
    try:
        a(users.source)
        b(users.source)
    finally:
        first.__exit__(None,None,None)
        second.__exit__(None,None,None)
    assert intake(users) == [True,True,False]
