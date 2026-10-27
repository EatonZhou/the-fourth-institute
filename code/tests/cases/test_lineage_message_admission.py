"""Graph edits can finish during preparation, but cannot race accepted input.

:importance: P1
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from management_common.operation_errors import OperationError
from management_portal.society_service import lineage_execution_authority as locks
from management_portal.society_service import lineage_program_execution as owner
from management_portal.society_service import lineage_primitives, resident_intake_admission as intake
from management_portal.society_service import family_familize, resident_intake_domain
from tests.cases.test_resident_execution_protocol import resident_record, input_delivery  # noqa: F401


@pytest.mark.parametrize('change', ['none', 'delete', 'reconnect'])
def test_route_preparation_releases_authority_and_fences_changed_route(tmp_path, monkeypatch, change):
    """路由准备释放执行权并在投递前拒绝已变更路由"""
    lid, rid = 'lineage_111111111111', 'resident_222222222222'
    wire = {'id': 'wire_test', 'target': rid, 'source': 'source', 'kind': 'route', 'config': {}}
    graph = {'lineage_id': lid, 'nodes': [{'id': rid}], 'program': {'wires': [wire]}}
    preparing, resume, entered, release_commit = (Event() for _ in range(4))
    messages, receipts = [], []
    def authority(_):
        return locks.execution_authority(lid, root=tmp_path)
    def send(node, prompt, actor, *, message_id, **kwargs):
        assert lid not in locks._OWNERS
        preparing.set()
        assert resume.wait(5)
        with intake.scope(rid, message_id):
            entered.set()
            assert release_commit.wait(5)
            messages.append(message_id)
            intake.committed(rid, message_id)
        return {'message_id': message_id}
    def register(*args):
        assert locks._OWNERS[lid] == get_ident()
        receipts.append(args[2])
    ports = SimpleNamespace(
        state=SimpleNamespace(read_lineage=lambda *a, **k: deepcopy(graph), lineage_execution_authority=authority),
        resident=SimpleNamespace(send_node_message=send, register_program_agent_invocation=register),
    )
    monkeypatch.setattr(owner, '_compiled_program', lambda g: g['program'])
    monkeypatch.setattr(owner.wire_delivery_budget, 'reserve', lambda *a: {'status': 'reserved'})
    monkeypatch.setattr(owner.wire_delivery_budget, 'accept', lambda *a: None)
    monkeypatch.setattr(owner.wire_delivery_budget, 'release', lambda *a: None)
    monkeypatch.setattr(owner.lineage_instruction_context, 'resident_context', lambda *a, **k: None)
    monkeypatch.setattr(family_familize, 'facade_speaking_to', lambda *a: '')
    event = lineage_primitives.make_event('manual.triggered', payload={})
    invocation = {'source_wire_id': 'wire_test', 'source_id': 'source', 'prompt': 'hello',
                  'packet': {'id': 'packet_test', 'key': 'test', 'text': 'hello'}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(owner._dispatch_program_packet_to_agent, graph, rid, event, invocation, 'sample_admin', ports=ports)
        try:
            assert preparing.wait(5), sending.result() if sending.done() else 'no preparation'
            def edit():
                with authority(lid):
                    if change == 'delete':
                        graph['program']['wires'] = []
                    elif change == 'reconnect':
                        graph['program']['wires'][0]['target'] = 'resident_333333333333'
            pool.submit(edit).result(3)
            resume.set()
            if change == 'none':
                assert entered.wait(3)
                editor = pool.submit(edit)
                assert not editor.done()
                release_commit.set()
                assert sending.result(3)['status'] == 'sent'
                editor.result(3)
                assert messages == receipts and len(messages) == 1
            else:
                with pytest.raises(OperationError, match='route changed') as raised:
                    sending.result(3)
                assert raised.value.commit_state == 'not_committed'
                assert not messages and not receipts
        finally:
            resume.set()
            release_commit.set()


@pytest.mark.parametrize('outcome', ['accepted', 'cancelled', 'receipt_failed'])
def test_real_intake_commits_under_guard_and_prepares_outside_it(input_delivery, monkeypatch, tmp_path,
                                                              outcome):
    """消息在守卫外准备并在守卫内提交且区分回执失败

    :importance: P2"""
    from contextlib import contextmanager
    from management_portal.society_service import resident_execution_work
    lid, rid = 'lineage_444444444444', input_delivery['resident_id']
    message_id = 'msg_0123456789abcdef'
    valid, accepted = [True], []
    @contextmanager
    def guard():
        with locks.execution_authority(lid, root=tmp_path):
            if not valid[0]:
                raise OperationError('route changed', commit_state='not_committed')
            yield
    def prepare(*a, **kw):
        assert lid not in locks._OWNERS
        with locks.execution_authority(lid, root=tmp_path):
            valid[0] = outcome != 'cancelled'
        return {}
    def accept(identity):
        assert locks._OWNERS[lid] == get_ident()
        assert resident_execution_work.read(input_delivery)['user_message_id'] == identity
        if outcome == 'receipt_failed':
            raise OperationError('bookkeeping failed', commit_state='not_committed')
        accepted.append(identity)
    monkeypatch.setattr(resident_intake_domain, 'resolve_context_refs', prepare)
    with intake.bind(rid, message_id, guard, accept):
        if outcome == 'cancelled':
            with pytest.raises(OperationError, match='route changed'):
                resident_intake_domain.deliver_resident_message_content(rid, 'CASE-A', 'hello', message_id=message_id)
            assert resident_execution_work.read(input_delivery) == {}
            assert accepted == []
        elif outcome == 'receipt_failed':
            with pytest.raises(OperationError) as raised:
                resident_intake_domain.deliver_resident_message_content(rid, 'CASE-A', 'hello', message_id=message_id)
            assert raised.value.commit_state == 'committed'
            assert resident_execution_work.read(input_delivery)['user_message_id'] == message_id
        else:
            assert resident_intake_domain.deliver_resident_message_content(rid, 'CASE-A', 'hello', message_id=message_id)['message_id'] == message_id
            assert accepted == [message_id]
