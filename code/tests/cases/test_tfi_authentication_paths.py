"""Session and two-factor keys live at their fixed place in the instance's ``secrets/``.

Site settings and the environment cannot point them elsewhere; the settings that
remain under ``deployment.auth`` are the cookie name and the public origin.
"""
import ast
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.cases.instance_test_support import select_instance


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for key in ['PORTAL_SESSION_COOKIE_NAME', 'PORTAL_SESSION_SIGNING_KEY_PATH', 'PORTAL_TWO_FACTOR_KEY_PATH',
                'PORTAL_PUBLIC_ORIGIN', 'PORTAL_SMTP_PASSWORD_FILE', 'PORTAL_SMTP_PASSWORD',
                'PORTAL_SMTP_SECURITY', 'PORTAL_SMTP_PORT']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('PORTAL_SMTP_HOST', 'smtp.invalid')
    monkeypatch.setenv('PORTAL_SMTP_FROM', 'portal@example.invalid')
    instance = select_instance(monkeypatch, tmp_path / 'site')
    secrets = instance.directory('secrets')
    secrets.mkdir()
    from management_portal import portal_session_auth as auth

    class Credentials:
        state = SimpleNamespace(revision=lambda: 1)

        def load(self):
            return {'alice': 'test-password-digest'}

    monkeypatch.setattr(auth, 'PortalCredentialStore', Credentials)
    return instance.root, secrets


def write_site(root, auth_settings):
    (Path(root) / 'site.json').write_text(json.dumps({'deployment': {'auth': auth_settings}}))


def access(active=True, version=3):
    return SimpleNamespace(is_configured=lambda u: u == 'alice', is_active=lambda u: active and u == 'alice',
                           session_version=lambda u: version)


def web_paths():
    # Execute only the real approved config assignments/imports. Full Web import
    # starts services and is deliberately excluded from this isolated check.
    from management_portal import portal_session_auth as auth
    source = Path(auth.__file__).with_name('web_accounts.py')
    tree = ast.parse(source.read_text())
    nodes = []
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.module == 'management_portal.portal_session_auth':
            nodes.append(n)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {'_SESSION_SIGNING_KEY_PATH', '_TWO_FACTOR_KEY_PATH'} for t in n.targets):
            nodes.append(n)
    env = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), env)
    return env['_SESSION_SIGNING_KEY_PATH'], env['_TWO_FACTOR_KEY_PATH']


def test_keys_have_fixed_places_in_secrets_without_io(isolated):
    from management_portal.portal_session_auth import PortalSessionAuthority
    _, secrets = isolated
    expected = secrets / 'session_signing.key'
    assert PortalSessionAuthority(access_store=access()).signing_key_path == expected
    assert web_paths() == (expected, secrets / 'two_factor.key')
    assert list(secrets.iterdir()) == []


@pytest.mark.parametrize('settings', [
    {'session_signing_key_path': 'keys/session', 'two_factor_key_path': 'keys/factor'},
    {'session_signing_key_path': []},
])
def test_site_settings_and_environment_cannot_move_the_keys(isolated, monkeypatch, settings):
    from management_portal.portal_session_auth import PortalSessionAuthority
    root, secrets = isolated
    write_site(root, settings)
    monkeypatch.setenv('PORTAL_SESSION_SIGNING_KEY_PATH', str(root / 'selected-session'))
    monkeypatch.setenv('PORTAL_TWO_FACTOR_KEY_PATH', str(root / 'selected-factor'))
    assert PortalSessionAuthority(access_store=access()).signing_key_path == secrets / 'session_signing.key'
    assert web_paths() == (secrets / 'session_signing.key', secrets / 'two_factor.key')
    assert set(root.iterdir()) == {root / 'instance.json', root / 'secrets', root / 'site.json'}


def test_independent_site_cookie_ignores_other_site_session(isolated, monkeypatch):
    from management_portal import portal_session_auth as auth
    root, secrets = isolated
    (secrets / 'session_signing.key').write_bytes(b'K' * 32)
    write_site(root, {'session_cookie_name': 'db_session_tfi-v07'})
    verifier = auth.PortalSessionAuthority(access_store=access())
    token = verifier.issue('alice', max_age=600, now=100)
    assert verifier.verify_cookie('db_session=other-site; db_session_tfi-v07=' + token, now=200) == 'alice'
    assert verifier.verify_cookie('db_session=' + token, now=200) == ''
    assert auth.login_challenge_cookie_name() == 'db_session_tfi-v07_login_challenge'
    monkeypatch.setenv('PORTAL_SESSION_COOKIE_NAME', 'db_session_other')
    assert auth.session_cookie_name() == 'db_session_other'
    assert auth.login_challenge_cookie_name() == 'db_session_other_login_challenge'


@pytest.mark.parametrize('value', ['', 'bad; Path=/', 'bad\r\nSet-Cookie=x', None, 42])
def test_invalid_cookie_name_fails_closed(isolated, value):
    from management_portal import portal_session_auth as auth
    root, _ = isolated
    write_site(root, {'session_cookie_name': value})
    with pytest.raises(ValueError):
        auth.session_cookie_name()


@pytest.mark.parametrize('payload', [{'deployment': None}, {'deployment': []}, {'deployment': {'auth': None}}, {'deployment': {'auth': []}}])
def test_invalid_auth_container_fails(isolated, payload):
    from management_portal import portal_session_auth as auth
    root, _ = isolated
    (root / 'site.json').write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        auth.session_cookie_name()


def test_origin_unconfigured_then_site_then_environment(isolated, monkeypatch):
    from management_portal.portal_password_reset import PortalSmtpMailer
    root, _ = isolated
    mail = PortalSmtpMailer.from_environment()
    assert mail.public_origin == '' and not mail.configured
    write_site(root, {'public_origin': 'https://site.example.invalid/'})
    mail = PortalSmtpMailer.from_environment()
    assert mail.public_origin == 'https://site.example.invalid' and mail.configured
    monkeypatch.setenv('PORTAL_PUBLIC_ORIGIN', 'https://env.example.invalid')
    assert PortalSmtpMailer.from_environment().public_origin == 'https://env.example.invalid'
    monkeypatch.setenv('PORTAL_PUBLIC_ORIGIN', '')
    mail = PortalSmtpMailer.from_environment()
    assert mail.public_origin == '' and not mail.configured


@pytest.mark.parametrize('origin', [None, [], 4, 'http://site.invalid', 'https://site.invalid/path'])
def test_invalid_site_origin_rejected(isolated, origin):
    from management_portal.portal_password_reset import PortalSmtpMailer
    root, _ = isolated
    write_site(root, {'public_origin': origin})
    with pytest.raises(ValueError):
        PortalSmtpMailer.from_environment()


def test_existing_session_signature_cookie_and_access_remain_valid(isolated):
    from management_portal import portal_session_auth as auth
    _, secrets = isolated
    keyfile = secrets / 'session_signing.key'
    key = b'isolated-session-key-material-32!'
    keyfile.write_bytes(key)
    before = (keyfile.read_bytes(), keyfile.stat().st_mtime_ns)
    payload = {'v': 1, 'u': 'alice', 'iat': 100, 'exp': 1000, 'sv': 3, 'nonce': 'fixed-before-upgrade'}
    encoded = auth._b64url_encode(json.dumps(payload, separators=(',', ':')).encode())
    token = encoded + '.' + auth._b64url_encode(hmac.new(key, encoded.encode(), hashlib.sha256).digest())
    verifier = auth.PortalSessionAuthority(access_store=access())
    assert verifier.signing_key_path == keyfile
    assert verifier.verify_cookie('db_session=' + token, now=200) == 'alice'
    assert verifier.verify(token + 'broken', now=200) == ''
    assert auth.PortalSessionAuthority(access_store=access(False)).verify(token, now=200) == ''
    assert auth.PortalSessionAuthority(access_store=access(version=4)).verify(token, now=200) == ''
    assert (keyfile.read_bytes(), keyfile.stat().st_mtime_ns) == before


def test_two_factor_existing_ciphertext_and_aad_preserved(isolated, monkeypatch):
    from management_portal import portal_two_factor as tf
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _, secrets = isolated
    keyfile = secrets / 'two_factor.key'
    key = b'F' * 32
    keyfile.write_bytes(key)
    _, path = web_paths()
    assert path == keyfile
    monkeypatch.setattr(tf, 'IdentityState', lambda _: None)
    store = tf.PortalTwoFactorStore(path)
    before = (keyfile.read_bytes(), keyfile.stat().st_mtime_ns)
    nonce = b'N' * 12
    cipher = AESGCM(key).encrypt(nonce, b'JBSWY3DPEHPK3PXP', b'alice')
    record = {'nonce': tf._b64encode(nonce), 'ciphertext': tf._b64encode(cipher)}
    original = dict(record)
    assert store._decrypt_secret(store._key(), 'alice', record) == 'JBSWY3DPEHPK3PXP'
    with pytest.raises(InvalidTag):
        store._decrypt_secret(store._key(), 'bob', record)
    assert record == original and (keyfile.read_bytes(), keyfile.stat().st_mtime_ns) == before
