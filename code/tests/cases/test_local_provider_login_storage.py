"""Local-login file ownership follows actual PostgreSQL registration outcomes.

:module: execution.providers
"""
import json
import pytest

from management_portal.execution import account_hosting, account_local_login, account_registrations


@pytest.mark.parametrize('deferred', [False, True])
def test_local_login_pg_failure_rolls_back_files_and_can_retry(tmp_path, monkeypatch, deferred):
    source = tmp_path / 'user' / '.codex'
    source.mkdir(parents=True)
    original = json.dumps({'auth_mode': 'chatgpt', 'tokens': {
        'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh'}})
    source.joinpath('auth.json').write_text(original)
    monkeypatch.setattr(account_local_login, 'user_home', lambda: source.parent)
    target = tmp_path / 'accounts' / 'new'
    monkeypatch.setattr(account_hosting, 'account_home', lambda *_: target)
    db = account_registrations.database()
    with db.transaction('registrations') as c:
        c.execute("""CREATE FUNCTION reject_local_fixture() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic registration failure'; END $$""")
        c.execute(('CREATE CONSTRAINT TRIGGER reject_local_fixture AFTER INSERT ON registrations '
                   'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_local_fixture()')
                  if deferred else
                  'CREATE TRIGGER reject_local_fixture BEFORE INSERT ON registrations '
                  'FOR EACH ROW EXECUTE FUNCTION reject_local_fixture()')
    data = dict(account_id='new', provider='codex', kind='subscription_login', config={'reuse_local_login': True})
    with pytest.raises(Exception, match='synthetic registration failure'):
        account_registrations.create_registration(data, 'test')
    assert account_registrations.registration('new') is None
    assert not target.exists()
    assert source.joinpath('auth.json').read_text() == original
    with db.transaction('registrations') as c:
        c.execute('DROP TRIGGER reject_local_fixture ON registrations')
        c.execute('DROP FUNCTION reject_local_fixture()')
    row = account_registrations.create_registration(data, 'test')
    assert row['version'] == 1
    assert json.loads(target.joinpath('auth.json').read_text()) == json.loads(original)
    assert target.joinpath('auth.json').stat().st_mode & 0o777 == 0o600
    assert account_registrations.create_registration(data, 'test') is None
    assert source.joinpath('auth.json').read_text() == original
