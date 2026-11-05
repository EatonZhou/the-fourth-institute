"""TAU module leases are independent of local test process capacity.

:module: operations.test-framework
:design: decision_c953eab654cd
"""
from management_portal.test_framework import slots
from management_portal.test_framework.records import RunRecords
from management_portal.test_framework.tau_campaign import TauCampaignExecutor
from tests.cases.instance_test_support import select_instance


def test_tau_runs_while_local_test_capacity_is_full(tmp_path, monkeypatch):
    select_instance(monkeypatch, tmp_path / 'instance')
    monkeypatch.setenv('PORTAL_TEST_MAX_CONCURRENCY', '1')
    records = RunRecords(tmp_path / 'runs.json')
    group = {'group_id': 'module:example', 'module_id': 'example'}
    rid = records.create({'selected': [], 'execution': {'groups': [group]}}, 'actor')['run_id']
    executor = TauCampaignExecutor(records, tmp_path, object())
    calls = []
    monkeypatch.setattr(executor, '_execute_group', lambda *args: calls.append(args[0]) or ('reported', ''))
    lease = slots.acquire('pytest', 1, stop=lambda: False)
    try:
        assert slots.acquire('pytest', 1, stop=lambda: True) is None
        assert executor._execute_group_with_slot(rid, group, [], tmp_path, 'actor') == ('reported', '')
        assert calls == [rid]
        assert slots.acquire('pytest', 1, stop=lambda: True) is None
    finally:
        lease.release()
