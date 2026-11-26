import json

from management_portal import antigravity_accounts as accounts


def metadata(name='Google AI Pro'):
    return {'_meta': {'antigravity': {'tier': {'name': name, 'id': 'g1-pro-tier',
                                            'access_token': 'secret', 'upgradeUri': 'sensitive'}}}}


def test_extracts_session_metadata_with_auth_fallback_and_whitelist():
    assert accounts.subscription_metadata(metadata(), metadata('old'))['tier']['name'] == 'Google AI Pro'
    result = accounts.subscription_metadata({}, metadata())
    assert result['source'] == 'acp.authenticate'
    assert 'secret' not in json.dumps(result) and 'sensitive' not in json.dumps(result)


def test_missing_tier_does_not_claim_free_or_fresh_observation():
    for response in ({}, {'_meta': []}, {'_meta': {'antigravity': {'tier': {}}}}):
        result = accounts.subscription_metadata(response)
        assert result['status'] == 'unknown' and result['observed_at'] is None


def test_catalog_persists_passive_subscription_and_does_not_reuse_old_grade(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, 'account_runtime', lambda *args: tmp_path)
    session = {'models': {'availableModels': [{'modelId': 'm'}], 'currentModelId': 'm'}}
    accounts.save_catalog('account', session, {}, authenticated=metadata())
    stored = json.loads((tmp_path/'catalog.json').read_text())
    assert stored['subscription']['tier']['id'] == 'g1-pro-tier'
    accounts.save_catalog('account', session, {})
    assert json.loads((tmp_path/'catalog.json').read_text())['subscription']['status'] == 'unknown'


def test_daily_tier_reads_native_auth_without_session_or_usage(monkeypatch):
    from unittest.mock import MagicMock
    client = MagicMock()
    client.__enter__.return_value = client
    client.call.return_value = metadata()
    monkeypatch.setattr(accounts, 'AcpClient', lambda *args, **kwargs: client)
    monkeypatch.setattr(accounts, 'account_for_route', lambda route: {'account_id': 'one'})
    monkeypatch.setattr(accounts, 'command', lambda env: ['native'])
    monkeypatch.setattr(accounts, 'environment', lambda _: {})
    result = accounts.read_subscription('route')
    assert result['source'] == 'acp.authenticate'
    client.call.assert_called_once_with('authenticate', {'methodId': 'oauth-personal'})
    client.initialize.assert_called_once()
