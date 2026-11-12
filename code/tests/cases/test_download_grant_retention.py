import json
from datetime import timedelta

import pytest

from management_common.directory_retention import RetentionStore, utc_now
from management_portal.resources import download_grants as grants
from management_portal.hatg.contracts import InstructionError


@pytest.mark.parametrize('legacy', [False, True])
def test_download_grant_is_removed_without_being_requested_again(tmp_path, legacy):
    now = utc_now()
    token = 'A' * 32
    active = 'B' * 32
    def record(expiry):
        return {'address': 'fixture/file.txt', 'caller': 'fixture', 'expires_at': expiry.isoformat()}
    expired_record = record(now - timedelta(minutes=1))
    if legacy:
        folder = tmp_path / 'downloads'
        folder.mkdir()
        (folder / (token + '.json')).write_text(json.dumps(expired_record), encoding='utf-8')
        assert grants.reconcile(tmp_path)['errors'] == []
    else:
        grants.write(tmp_path, token, expired_record)
    grants.write(tmp_path, active, record(now + timedelta(minutes=15)))
    assert grants.reconcile(tmp_path)['errors'] == []
    store = RetentionStore()
    owner = 'resource-download:' + token
    assert store.sweep(now=now, owner_id=owner)['queued'] == 1
    # No download read triggers this removal; the ordinary queue owns it.
    for row in store.queue.status(owner_id=owner):
        store.queue.run_pending(cleanup_id=row['cleanup_id'])
    assert not (tmp_path / 'downloads' / (token + '.json')).exists()
    assert grants.read(tmp_path, active, 'fixture', now)['caller'] == 'fixture'
    with pytest.raises(InstructionError):
        grants.read(tmp_path, active, 'someone-else', now)
    with pytest.raises(InstructionError):
        grants.read(tmp_path, active, 'fixture', now + timedelta(hours=1))
