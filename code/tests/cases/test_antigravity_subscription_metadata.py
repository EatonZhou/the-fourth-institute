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
