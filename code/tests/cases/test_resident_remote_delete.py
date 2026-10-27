"""Committed deletion reaches other browser documents before refresh reads.

:module: resident.conversation
:design: decision_ce44377c3257
"""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from management_portal import serve_management_portal as portal, permission_system
from management_portal.notifications import Notification
from management_portal.society_service import (
    changes,
    invariant_locks,
    lineage_definition_scope,
    resident_workspace_domain as owner,
)
from management_portal.society_service import resident_record_domain

RID='resident_111111111111'


def test_deleted_notification_keeps_authorization_and_normal_identity_refresh():
    """删除身份通知保留权限过滤；普通身份变更不能成为删除标记。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    private='resident_222222222222'
    def event(i,target,kind='deleted',scope='identity'):
        return Notification(sequence=i,subject=f'portal.resident.changed.{target}.{scope}',payload={'kind':kind},received_at=0)
    def readable(instruction,parameter,ids,**kwargs):return set(ids)&{RID}
    with patch.object(permission_system,'readable_ids',side_effect=readable),patch.object(portal,'_forget_cached_inbox'):
        result=portal._portal_change_payload('case_actor',[event(1,RID,'changed'),event(2,RID),event(3,private)],{'resident'})
        assert result['resident_changes']=={RID:['deleted','identity']}
        assert private not in json.dumps(result)
        assert result['snapshot_required'] is True
        assert portal._portal_change_payload('case_actor',[event(1,RID,'changed')],{'resident'})['resident_changes']=={RID:['identity']}
        assert portal._portal_change_payload('case_actor',[event(1,RID,'deleted','runtime')],{'resident'})['resident_changes']=={RID:['runtime']}
        assert portal._portal_change_payload('case_actor',[event(1,RID)],{'lineage'})['resident_changes']=={}


def test_deleted_notification_waits_for_owner_commit_and_state_unlock():
    """删除事务成功返回后才发删除通知；失败与未解锁状态不可提前发布。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    callbacks=[]
    with changes.collect_changes() as deferred:
        with patch.object(invariant_locks,'lock_held',return_value=True),patch.object(invariant_locks,'defer_until_state_unlock',side_effect=callbacks.append):
            changes.resident_deleted(RID)
        assert deferred==[] and len(callbacks)==1
        with patch.object(invariant_locks,'lock_held',return_value=False):callbacks[0]()
        assert deferred==[('resident',RID,'identity',{'kind':'deleted'})]
    calls=[]
    plan={'result':{'resident_id':RID,'lineage_id':'lineage_test','workspace_original_path':'/isolated-fixture/one','deleted_at':'2026-10-25T00:00:00Z','deleted_by':'case_actor'},'records':[{'resident_id':RID}],'steps':[],'lineage_deletion_token':None}
    store=SimpleNamespace(retire_workspaces_under=Mock(return_value=[]),enqueue_resident_resource_projection=Mock(return_value=7))
    def transaction(body):calls.append('begin');body();calls.append('commit')
    store.transaction=transaction
    catalog=SimpleNamespace(state_store=store,remove=Mock(return_value=[]))
    publish=changes.resident_deleted
    def deleted(target):
        assert calls[-1]=='commit';calls.append('deletion_notification');publish(target)
    with patch.object(owner.resident_catalog,'get_catalog',return_value=catalog),patch.object(resident_record_domain,'lifecycle_operations',return_value=SimpleNamespace(enqueue_cleanup=Mock())),patch.object(lineage_definition_scope,'check_before_membership_write'),patch.object(invariant_locks,'lock_held',return_value=False),patch.object(changes,'resident_deleted',side_effect=deleted),patch.object(changes,'lineage_changed'):
        with changes.collect_changes() as batch:result=owner._commit_resident_deletions([plan])
        assert result[2]=={RID:7} and calls==['begin','commit','deletion_notification']
        assert batch==[('resident',RID,'identity',{'kind':'deleted'})]
        catalog.remove.side_effect=RuntimeError('isolated transaction failure')
        with changes.collect_changes() as rolled_back:
            try:owner._commit_resident_deletions([plan]);raise AssertionError('failure expected')
            except RuntimeError as exc:assert str(exc)=='isolated transaction failure'
        assert rolled_back==[]


def test_other_document_stops_deleted_target_reads():
    """跨文档删除停止三个原生订阅者的读取并阻止旧响应恢复目标。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result=subprocess.run(['node',str(Path(__file__).with_name('javascript')/'resident_remote_delete.cjs')],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr


def test_mailbox_delete_ready_preserves_hidden_graph_evidence():
    """删除回调核真实缓存及可见UI，保留隐藏画布物理节点事实。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result=subprocess.run(['node',str(Path(__file__).with_name('javascript')/'tau_mailbox_deleted_target.cjs')],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr


def test_deleted_index_tombstone_retires_native_readers_before_notification():
    script = Path(__file__).with_name("javascript") / "resident_index_deleted.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
