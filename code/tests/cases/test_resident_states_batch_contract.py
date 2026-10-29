"""
:module: lineage.service
:aspects: functional, performance
:design: decision_71aa039e3211, decision_b4e5ea4a47ec
"""
from management_portal.society_service import business_fact_views
"""State refresh reuses checked records and scans Chat user facts once."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from management_portal.execution import agent_data_chat, agent_data_chat_activity, agent_data_chat_writes
from management_portal.execution.agent_data_store import AgentDataStore, finish_conversation_summary
from management_portal.society_service import (
    family_activity, resident_access, resident_catalog, resident_conversation_stream,
    resident_protocol,
)
from tests.cases.database_test_support import guard_queries


@pytest.mark.parametrize('allowed', [True, False])
def test_state_batch_includes_canonical_deterministic_resident(monkeypatch, allowed):
    """批量状态包含规范化的确定性 Resident

    同时读取仅保存后端的 portal 与 Codex Resident，使用真实后端能力解析，验证整批成功、仅一次状态读取及各自可用操作。

    :module: resident.identity
    :importance: P1
    :aspects: functional
    :design: decision_2a3ff353165c
    :design-point:
    """
    records = [
        {'resident_id': 'resident_111111111111', 'backend': 'portal'},
        {'resident_id': 'resident_222222222222', 'backend': 'codex'},
    ]
    ids = [record['resident_id'] for record in records]
    monkeypatch.setattr(resident_access, 'visible_resident_records', lambda *_args: records)
    monkeypatch.setattr(resident_access, 'actor_can_manage_residents', lambda *_args: allowed)
    reads = []
    def snapshots(wanted, *, resident_records):
        reads.append(wanted)
        assert list(resident_records) == ids
        return {rid: {'resident_id': rid, 'state': {'work_state': 'idle'}} for rid in wanted}
    monkeypatch.setattr(resident_protocol, 'read_resident_state_snapshots', snapshots)
    items = resident_protocol.get_resident_states(ids, 'test-actor')['items']
    assert reads == [ids]
    assert [item['resident_id'] for item in items] == ids
    assert all(item['controls']['can_send'] is allowed for item in items)
    assert items[0]['controls']['active_turn_input'] is False
    assert items[0]['controls']['accepts_images'] is False
    assert items[0]['controls']['accepts_files'] is False
    assert items[1]['controls']['active_turn_input'] is True


@pytest.mark.parametrize('size', [1, 106])
def test_state_batch_reuses_visible_records_for_family_and_controls(monkeypatch, size):
    """Resident 批量状态复用已授权记录

    批量读取Family与控制状态只读一次记录和权限，保留真实队列及上下文禁用原因。

    :design: decision_0f57656a2acd, decision_692a484e9bea
    :design-point:
    """
    ids = [f'resident_{index:012x}' for index in range(size)]
    hidden, staged, deleted, missing = [f'resident_{index:012x}' for index in range(size, size + 4)]
    records = {rid: {'resident_id': rid, 'lineage_id': 'lineage_111111111111', 'registration_state': 'ready'} for rid in ids}
    records[hidden] = {'resident_id': hidden, 'lineage_id': 'lineage_222222222222', 'registration_state': 'ready'}
    records[staged] = {'resident_id': staged, 'lineage_id': 'lineage_111111111111', 'registration_state': 'resource_pending'}
    records[deleted] = {'resident_id': deleted, 'deleted_at': '2026-10-08T00:00:00Z'}
    reads, state_reads, access_reads, permission_reads = [], [], [], []
    monkeypatch.setattr(resident_access, 'actor_can_manage_residents',
                        lambda actor: permission_reads.append(actor) or True)

    class Catalog:
        def get_identity_records(self, wanted):
            reads.append(list(wanted))
            return [records[rid] for rid in wanted if rid in records and not records[rid].get('deleted_at')]

    monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: Catalog())
    monkeypatch.setattr(resident_access, 'get_portal_access_store', lambda: SimpleNamespace(
        is_service_actor=lambda actor: actor == 'portal', is_active=lambda actor: actor == 'CASE-A',
    ))
    def access(actor, lineage_ids):
        access_reads.append((actor, list(lineage_ids)))
        return [{'lineage_id': 'lineage_111111111111'}]
    monkeypatch.setattr(resident_access.resident_lineage_host_port, 'actor_lineage_access_records', access)
    def snapshots(wanted):
        state_reads.append(list(wanted))
        return {rid: {'state': {'work_state': 'running', 'active_message_id': 'input',
                               'queued_count': 2, 'intake_state': 'closed'},
                      'cursor': 7, 'latest_input_state': {'message_id': 'input', 'state': 'running'}} for rid in wanted}
    monkeypatch.setattr(resident_conversation_stream, 'get_repository', lambda: SimpleNamespace(state_snapshots=snapshots))
    monkeypatch.setattr(family_activity, 'get_lineage_data_store', lambda: SimpleNamespace(
        residents_by_id=lambda ids: pytest.fail('Family projection reread already supplied records'),
    ))
    monkeypatch.setattr(resident_protocol.resident_execution_provider, 'provider_for', lambda record: SimpleNamespace(
        capabilities=SimpleNamespace(supports_active_turn_input=True, supports_media=True, local_tools=True),
    ))
    wanted = ids + [hidden, staged, deleted, missing, ids[0]]
    result = resident_protocol.get_resident_states(wanted, 'CASE-A')['items']
    assert reads == [wanted[:-1]]
    assert state_reads == [ids]
    assert permission_reads == ['CASE-A']
    assert access_reads == [('CASE-A', ['lineage_111111111111', 'lineage_222222222222'])]
    assert [item['resident_id'] for item in result] == ids
    assert all(item['cursor'] == 7 and item['latest_input_state']['state'] == 'running' for item in result)
    assert all(item['state']['queued_count'] == 2 and item['controls']['can_stop'] for item in result)
    assert all(item['controls']['can_send'] for item in result)


def test_record_reuse_does_not_bypass_service_read_access_boundary(monkeypatch):
    """非服务调用者传入已检查的记录时，批量读取可见 Resident 仍报权限错误

    :module: resident.identity
    :aspects: functional
    :design-point:
    """
    monkeypatch.setattr(resident_access, 'get_portal_access_store', lambda: SimpleNamespace(is_service_actor=lambda actor: False))
    for read in (resident_access.visible_resident_records, resident_access.filter_visible_resident_ids):
        with pytest.raises(PermissionError, match='service actor'):
            read(['resident_111111111111'], 'CASE-A', 'CASE-A')


@pytest.mark.parametrize('actor', ['CASE-A', ' @case-a', ''])
@pytest.mark.parametrize('finish', [False, True])
def test_summary_scans_user_facts_once_preserving_reset_relay_and_recipient_facts(monkeypatch, actor, finish):
    """Conversation 摘要一次扫描保留 Reset 和转发事实

    摘要不重复扫描或读取正文，用户请求、Reset边界、转发与收件人事实保持正确。

    :module: resident.conversation
    :design: decision_0f57656a2acd, decision_692a484e9bea
    :design-point:
    """
    store = AgentDataStore()
    rid, empty = 'resident_111111111111', 'resident_222222222222'
    def say(mid, at, **fields):
        store.display_chat_message(rid, {'message_id': mid, 'created_at': at,
            'role': 'user', 'author_id': 'CASE-A', 'content_parts': [{'type': 'text', 'text': 'body'}], **fields})
    say('archived', '2026-10-08T00:00:00Z')
    store.record_context_reset(rid, reset_at='2026-10-08T00:00:01Z', actor_ref='CASE-A', trace_position=store.trace_length(rid))
    say('first', '2026-10-08T00:00:02Z', author_id='@CASE-A')
    say('second', '2026-10-08T00:00:03Z', author_id='＠case-a')
    say('other', '2026-10-08T00:00:04Z', author_id='sample_admin')
    say('relay', '2026-10-08T00:00:05Z', business_ref='family-relay:copy')
    say('addressed', '2026-10-08T00:00:06Z', role='agent',
        recipients=[{'kind': 'user', 'id': 'CASE-A'}])
    monkeypatch.setattr(store.chat_activity, 'user_message_summaries', lambda *a, **kw: pytest.fail('duplicate user-message scan'))
    for module in (agent_data_chat, agent_data_chat_writes):
        monkeypatch.setattr(module, 'message_contents', lambda *a, **kw: pytest.fail('summary read message bodies'))
    statements, metadata_reads = [], []
    original_snapshot = store.database.snapshot
    @contextmanager
    def measured_snapshot():
        with original_snapshot() as connection:
            yield guard_queries(connection, statements=statements)
    monkeypatch.setattr(store.database, 'snapshot', measured_snapshot)
    for name in ('trace_ids', 'chat_floors'):
        original = getattr(agent_data_chat_activity, name)
        def measured(*a, _name=name, _original=original, **kw):
            metadata_reads.append(_name)
            return _original(*a, **kw)
        monkeypatch.setattr(agent_data_chat_activity, name, measured)
    summaries = store.chat_conversation_summaries([rid, empty, rid], actor,
        excluded_business_ref=r'^family-relay:', finish=finish)
    raw = {'user_message_count': 3, 'last_user_actor': 'sample_admin',
           'last_user_message_at': '2026-10-08T00:00:04Z',
           'actor_requests': [('2026-10-08T00:00:02Z', 'first'), ('2026-10-08T00:00:03Z', 'second')] if actor else [],
           'actor_addressed_message_id': 'addressed' if actor else '',
           'actor_addressed_at': '2026-10-08T00:00:06Z' if actor else ''}
    assert summaries[rid] == (finish_conversation_summary(raw) if finish else raw)
    assert summaries[empty]['user_message_count'] == 0
    assert metadata_reads == ['trace_ids', 'chat_floors']
    assert len([sql for sql in statements if 'as actor_requests' in sql]) == 1
    assert len([sql for sql in statements if 'as last_actor' in sql]) == 1


def test_summary_keeps_one_committed_snapshot_without_blocking_new_messages(monkeypatch):
    """Conversation 摘要读取一致快照且允许后续写入

    摘要在同一已提交快照读取，期间新消息独立提交并只出现在下次读取。

    :module: resident.conversation
    :design: decision_0f57656a2acd, decision_692a484e9bea
    :design-point:
    """
    store = AgentDataStore()
    rid = 'resident_111111111111'
    store.display_chat_message(rid, {'message_id': 'first', 'role': 'user', 'author_id': 'CASE-A',
        'created_at': '2026-10-08T00:00:00Z', 'content_parts': [{'type': 'text', 'text': 'question'}]})
    original_snapshot = store.database.snapshot
    inserted = []
    def writer():
        store.display_chat_message(rid, {'message_id': 'new', 'role': 'user', 'author_id': 'CASE-A',
            'created_at': '2026-10-08T00:00:01Z', 'content_parts': [{'type': 'text', 'text': 'next'}]})
        store.display_chat_message(rid, {'message_id': 'addressed', 'role': 'agent',
            'recipients': [{'kind': 'user', 'id': 'CASE-A'}], 'created_at': '2026-10-08T00:00:02Z',
            'content_parts': [{'type': 'text', 'text': 'notification'}]})
    class Connection:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, *args, **kwargs):
            result = self.connection.execute(sql, *args, **kwargs)
            if 'AS actor_requests' in sql and not inserted:
                inserted.append(True)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(writer).result(timeout=5)
            return result
        def __getattr__(self, name):
            return getattr(self.connection, name)
    @contextmanager
    def interleaved_snapshot():
        with original_snapshot() as connection:
            yield Connection(connection)
    monkeypatch.setattr(store.database, 'snapshot', interleaved_snapshot)
    first = store.chat_conversation_summaries([rid], 'CASE-A')[rid]
    assert first['user_message_count'] == 1
    assert first['actor_request_ids'] == ['first']
    assert first['actor_addressed_message_id'] == ''
    current = store.chat_conversation_summaries([rid], 'CASE-A')[rid]
    assert current['user_message_count'] == 2
    assert current['actor_request_ids'] == ['first', 'new']
    assert current['actor_addressed_message_id'] == 'addressed'


def test_conversation_directory_reuses_visible_identity_and_binding_batches(monkeypatch):
    """Resident 批量复用记录仍检查服务可见性

    :design-point:
    """
    from management_portal.society_service import resident_backend, resident_directory_domain
    records = [dict(resident_id=f"resident_{index:012x}", title=f"Member {index}",
                    lineage_id="lineage_111111111111") for index in range(106)]
    ids = [record["resident_id"] for record in records]
    visible_reads, binding_reads = [], []
    def visible(requested, actor, service_actor):
        visible_reads.append((requested, actor, service_actor))
        return records
    def bindings(requested):
        binding_reads.append(requested)
        return {binding: {"backend_route_id": binding} for binding in requested}
    monkeypatch.setattr(resident_access, "visible_resident_records", visible)
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: pytest.fail("directory reread full Resident records"))
    monkeypatch.setattr(business_fact_views, 'canonical_views',
                        lambda *a, **kw: pytest.fail("directory duplicated checked read access"))
    monkeypatch.setattr(resident_backend, "session_routes_for_bindings", bindings)
    monkeypatch.setattr(resident_backend, "account_id", lambda record, *, binding: binding["backend_route_id"])
    result = resident_directory_domain._engaged_conversation_records(ids + [ids[0]], "CASE-A")
    assert visible_reads == [(ids, "CASE-A", "portal")]
    assert binding_reads == [[f"resident:{rid}" for rid in ids]]
    assert [row["resident_id"] for row in result] == ids
    assert [row["account_id"] for row in result] == binding_reads[0]


def test_lineage_busy_check_reads_only_the_work_state(monkeypatch):
    """Lineage 判断成员是否忙只读工作状态

    积压投递与完成扫描在持有 Lineage 执行锁时对每条待投递连线和每个被观察成员判断一次是否忙。
    这一判断只读当前轮次与队列得出的工作状态，不读取输入历史、Family 活动、额度闸门和账号解析；
    排队与运行都算忙，已删除的 Resident 报 KeyError。

    :module: lineage.service
    :aspects: functional, performance
    :design-point:
    """
    from management_portal.society_service import lineage_domain, resident_execution_gate
    states = {"resident_111111111111": {"work_state": "running"},
              "resident_222222222222": {"work_state": "queued"},
              "resident_333333333333": {"work_state": "idle"}}
    reads = []

    class Repository:
        def states(self, resident_ids):
            reads.append(list(resident_ids))
            return {rid: states[rid] for rid in resident_ids if rid in states}

    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: Repository())
    for name in ("get_resident_state", "read_resident_state_snapshots"):
        monkeypatch.setattr(resident_protocol, name, lambda *a, **kw: pytest.fail("busy check read the Resident state view"))
    monkeypatch.setattr(resident_execution_gate, "project_states",
                        lambda *a, **kw: pytest.fail("busy check projected the quota gate"))
    busy = {rid: lineage_domain._node_has_active_execution({"id": rid}, "CASE-A")
            for rid in states}
    assert busy == {"resident_111111111111": True, "resident_222222222222": True,
                    "resident_333333333333": False}
    assert reads == [[rid] for rid in states]
    with pytest.raises(KeyError):
        lineage_domain._node_has_active_execution({"id": "resident_444444444444"}, "CASE-A")
