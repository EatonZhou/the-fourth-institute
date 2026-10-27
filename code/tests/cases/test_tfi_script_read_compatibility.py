"""Passive script readers only: no migration, provider call or control action."""
import json
from datetime import datetime, timezone

import pytest
from scripts import codex_weekly_quota_usage as quota


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_quota_reader_retains_observation_health_account_and_file(prefix, tmp_path):
    """额度文件仅接受规范版本，账户与过期观察校验保留，拒绝读取也不写回"""
    path = tmp_path/'2026-08-01.json'
    record = {'schema': f'{prefix}.codex-daily-limit-snapshot.v2', 'snapshot_date': '2026-08-01',
              'observed_at': '2026-07-31T21:00:00Z', 'accounts': [{'account_id': 'test', 'status': 'ok', 'stale': False,
              'updated_at': '2026-07-31T21:00:00Z', 'rate_limits': [{'remaining_percent': 27, 'window_duration_mins': 10080,
              'resets_at': 1785903031, 'observed_at': '2026-07-31T21:00:00Z', 'stale': False}]}]}
    path.write_text(json.dumps(record)); before,mtime=path.read_bytes(),path.stat().st_mtime_ns
    load = lambda account='test': quota.load_daily_limit_snapshots(tmp_path, account_id=account,
        since_utc=datetime(2026,7,31,16,tzinfo=timezone.utc), until_utc=datetime(2026,8,2,16,tzinfo=timezone.utc),
        timezone_name='Asia/Hong_Kong', day_start_hour=5)
    samples, audit=load()
    if prefix == 'archive':
        assert samples == {}
        assert audit['warnings'] == ['unsupported_snapshot_schema:2026-08-01.json']
        assert audit['snapshots_rejected'] == 1
        assert (path.read_bytes(),path.stat().st_mtime_ns)==(before,mtime)
        return
    assert samples and not audit['warnings'] and path.read_bytes()==before
    assert not load('absent')[0]
    assert (path.read_bytes(),path.stat().st_mtime_ns)==(before,mtime)
    record['accounts'][0]['stale']=True;path.write_text(json.dumps(record))
    before,mtime=path.read_bytes(),path.stat().st_mtime_ns
    assert not load()[0]
    assert (path.read_bytes(),path.stat().st_mtime_ns)==(before,mtime)
    record['accounts'][0]['stale']=False;record['schema']=f'{prefix}.codex-daily-limit-snapshot.v3'
    path.write_text(json.dumps(record));before,mtime=path.read_bytes(),path.stat().st_mtime_ns
    assert not load()[0]
    assert (path.read_bytes(),path.stat().st_mtime_ns)==(before,mtime)
