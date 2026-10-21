"""Search updates preserve independent records and immutable invocation identity."""
import pytest


def test_single_search_update_isolated_and_global_id_cannot_change_surface():
    """更新一条检索不改动其他行，检索 ID 不能换到另一个检索面"""
    from management_portal import semantic_search_jobs as jobs, semantic_search_store as store
    first = jobs.set_search('skill', 'search_123456789abc', {'query': 'one', 'results': []})
    jobs.set_search('repo', 'search_223456789abc', {'query': 'two', 'results': []})
    with store.database().read() as connection:
        before = connection.execute("SELECT xmin::text FROM jobs WHERE search_id='search_223456789abc'").fetchone()[0]
    jobs.set_search('skill', first['search_id'], {'status': 'done'})
    with store.database().read() as connection:
        assert connection.execute("SELECT xmin::text FROM jobs WHERE search_id='search_223456789abc'").fetchone()[0] == before
    with pytest.raises(ValueError, match='another surface'):
        jobs.set_search('repo', first['search_id'], {'query': 'wrong'})
    assert jobs.search_by_id('skill', first['search_id'])['query'] == 'one'


def test_invocation_identity_is_immutable():
    """服务调用记录写入后，创建者等身份字段不能修改"""
    from management_portal.society_service.service_invocation_storage import write
    from unittest.mock import Mock
    connection = Mock()
    plan = {'created_by': 'alice', 'surface': 'skill', 'search_id': 'search_123456789abc',
            'run_generation': 1, 'message_id': 'message-fixture', 'status': 'running',
            'batches': [{'batch_id': 'batch-0', 'message_id': 'message-fixture', 'status': 'running'}]}
    changed = {**plan, 'created_by': 'bob', 'batches': [{**batch, 'created_by': 'bob'} for batch in plan['batches']]}
    with pytest.raises(ValueError, match='identity is immutable'):
        write(connection, ('alice', 'skill', plan['search_id'], 1), changed)
    connection.execute.assert_not_called()
