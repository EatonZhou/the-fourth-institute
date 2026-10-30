import ast
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.delenv('PORTAL_SESSION_COOKIE_NAME', raising=False)
    from management_common import portal_paths
    root=tmp_path/'install';root.mkdir()
    site=tmp_path/'site.json';site.write_text('{}')
    for key in ['PORTAL_SESSION_SIGNING_KEY_PATH','PORTAL_TWO_FACTOR_KEY_PATH','PORTAL_PUBLIC_ORIGIN','PORTAL_SMTP_PASSWORD_FILE','PORTAL_SMTP_PASSWORD','PORTAL_SMTP_SECURITY','PORTAL_SMTP_PORT']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('PORTAL_SITE_CONFIG',str(site))
    monkeypatch.setenv('PORTAL_SMTP_HOST','smtp.invalid')
    monkeypatch.setenv('PORTAL_SMTP_FROM','portal@example.invalid')
    monkeypatch.setattr(portal_paths,'PORTAL_ROOT',root)
    monkeypatch.setattr(portal_paths,'path',lambda key:root/'private' if key=='secrets_dir' else pytest.fail(key))
    original_read_bytes=Path.read_bytes
    def guarded_read_bytes(path):
        if not path.is_relative_to(tmp_path):
            pytest.fail('isolated test cannot read bytes outside temporary directory')
        return original_read_bytes(path)
    monkeypatch.setattr(Path,'read_bytes',guarded_read_bytes)
    from management_portal import portal_session_auth as auth
    monkeypatch.setattr(auth,'portal_path',portal_paths.path,raising=False)
    class Credentials:
        state=SimpleNamespace(revision=lambda:1)
        def load(self):return {'alice':'test-password-digest'}
    monkeypatch.setattr(auth,'PortalCredentialStore',Credentials)
    return root,site

def access(active=True,version=3):
    return SimpleNamespace(is_configured=lambda u:u=='alice',is_active=lambda u:active and u=='alice',session_version=lambda u:version)

def web_paths():
    # Execute only the real approved config assignments/imports. Full Web import
    # starts services and is deliberately excluded from this isolated check.
    from management_portal import portal_session_auth as auth
    source=Path(auth.__file__).with_name('web_accounts.py')
    tree=ast.parse(source.read_text());nodes=[]
    for n in tree.body:
        if isinstance(n,ast.ImportFrom) and n.module=='management_portal.portal_session_auth':nodes.append(n)
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'_SESSION_SIGNING_KEY_PATH','_TWO_FACTOR_KEY_PATH'} for t in n.targets):nodes.append(n)
    env={'Path':Path,'_env_path':lambda name,default:Path(__import__('os').getenv(name,str(default))).expanduser()}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),env)
    return env['_SESSION_SIGNING_KEY_PATH'],env['_TWO_FACTOR_KEY_PATH']

def router_key(monkeypatch,cli=()):
    # Run the actual argparse prefix of Router.main; stop at parsed args, before
    # sockets, network config initialization or authority construction.
    from management_portal import portal_session_auth as auth
    import argparse,os,sys
    source=Path(auth.__file__).with_name('portal_deployment_router.py')
    tree=ast.parse(source.read_text());nodes=[]
    for n in tree.body:
        if isinstance(n,ast.ImportFrom) and n.module=='portal_session_auth':
            n.level=0;n.module='management_portal.portal_session_auth';nodes.append(n)
        if isinstance(n,ast.FunctionDef) and n.name=='main':
            for statement in n.body:
                nodes.append(statement)
                if isinstance(statement,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='args' for t in statement.targets):break
    monkeypatch.setattr(sys,'argv',['router',*cli])
    from management_common.site_compatibility import compatibility_value
    env={'argparse':argparse,'os':os,'Path':Path,'compatibility_value':compatibility_value,'DEFAULT_DEPLOYMENT_REGISTRY_PATH':Path('/unused-registry')}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),env)
    return env['PortalSessionAuthority'](env['args'].session_signing_key_path,access_store=access()).signing_key_path

def test_defaults_share_one_key_authority_without_io(isolated,monkeypatch):
    from management_portal.portal_session_auth import PortalSessionAuthority
    root,_=isolated
    expected=root/'private/session_signing.key'
    assert PortalSessionAuthority(access_store=access()).signing_key_path==expected
    assert web_paths()==(expected,root/'private/two_factor.key')
    assert router_key(monkeypatch)==expected
    assert not (root/'private').exists()


def test_independent_site_cookie_ignores_other_site_session(isolated,monkeypatch):
    from management_portal import portal_session_auth as auth
    root,site=isolated
    keyfile=root/'cookie.key';keyfile.write_bytes(b'K'*32)
    site.write_text(json.dumps({'deployment':{'auth':{
        'session_cookie_name':'db_session_tfi-v07',
        'session_signing_key_path':str(keyfile)}}}))
    verifier=auth.PortalSessionAuthority(access_store=access())
    token=verifier.issue('alice',max_age=600,now=100)
    assert verifier.verify_cookie('db_session=other-site; db_session_tfi-v07='+token,now=200)=='alice'
    assert verifier.verify_cookie('db_session='+token,now=200)==''
    assert auth.login_challenge_cookie_name()=='db_session_tfi-v07_login_challenge'
    monkeypatch.setenv('PORTAL_SESSION_COOKIE_NAME','db_session_other')
    assert auth.session_cookie_name()=='db_session_other'
    assert auth.login_challenge_cookie_name()=='db_session_other_login_challenge'


@pytest.mark.parametrize('value',['','bad; Path=/','bad\r\nSet-Cookie=x',None,42])
def test_invalid_cookie_name_fails_closed(isolated,value):
    from management_portal import portal_session_auth as auth
    _,site=isolated
    site.write_text(json.dumps({'deployment':{'auth':{'session_cookie_name':value}}}))
    with pytest.raises(ValueError):auth.session_cookie_name()

def test_site_relative_paths_and_reread(isolated,monkeypatch):
    from management_portal.portal_session_auth import PortalSessionAuthority
    root,site=isolated
    site.write_text(json.dumps({'deployment':{'auth':{'session_signing_key_path':'keys/original-session','two_factor_key_path':'keys/original-factor'}}}))
    monkeypatch.chdir(site.parent)
    assert PortalSessionAuthority(access_store=access()).signing_key_path==root/'keys/original-session'
    assert web_paths()==(root/'keys/original-session',root/'keys/original-factor')
    assert router_key(monkeypatch)==root/'keys/original-session'
    site.write_text(json.dumps({'deployment':{'auth':{'session_signing_key_path':'keys/other'}}}))
    assert PortalSessionAuthority(access_store=access()).signing_key_path==root/'keys/other'
    assert not (root/'keys').exists()

def test_env_and_explicit_override_keep_priority(isolated,monkeypatch):
    from management_portal.portal_session_auth import PortalSessionAuthority
    root,site=isolated
    site.write_text(json.dumps({'deployment':{'auth':{'session_signing_key_path':'unused/session','two_factor_key_path':'unused/factor','public_origin':'https://site.invalid'}}}))
    monkeypatch.setenv('PORTAL_SESSION_SIGNING_KEY_PATH',str(root/'selected-session'))
    monkeypatch.setenv('PORTAL_TWO_FACTOR_KEY_PATH',str(root/'selected-factor'))
    assert PortalSessionAuthority(access_store=access()).signing_key_path==root/'selected-session'
    assert web_paths()==(root/'selected-session',root/'selected-factor')
    assert router_key(monkeypatch)==root/'selected-session'
    assert router_key(monkeypatch,['--session-signing-key-path',str(root/'cli')])==root/'cli'
    assert PortalSessionAuthority(root/'caller',access_store=access()).signing_key_path==root/'caller'

@pytest.mark.parametrize('value',[None,[],0,''])
def test_invalid_auth_key_fails_without_creating_anything(isolated,value):
    from management_portal.portal_session_auth import PortalSessionAuthority
    root,site=isolated
    site.write_text(json.dumps({'deployment':{'auth':{'session_signing_key_path':value}}}))
    with pytest.raises(ValueError):PortalSessionAuthority(access_store=access())
    assert list(root.iterdir())==[]

@pytest.mark.parametrize('payload',[{'deployment':None},{'deployment':[]},{'deployment':{'auth':None}},{'deployment':{'auth':[]}}])
def test_invalid_auth_container_fails(isolated,payload):
    from management_portal.portal_session_auth import PortalSessionAuthority
    _,site=isolated;site.write_text(json.dumps(payload))
    with pytest.raises(ValueError):PortalSessionAuthority(access_store=access())

def test_origin_unconfigured_then_site_then_environment(isolated,monkeypatch):
    from management_portal.portal_password_reset import PortalSmtpMailer
    _,site=isolated
    mail=PortalSmtpMailer.from_environment()
    assert mail.public_origin=='' and not mail.configured
    site.write_text(json.dumps({'deployment':{'auth':{'public_origin':'https://site.example.invalid/'}}}))
    mail=PortalSmtpMailer.from_environment()
    assert mail.public_origin=='https://site.example.invalid' and mail.configured
    monkeypatch.setenv('PORTAL_PUBLIC_ORIGIN','https://env.example.invalid')
    assert PortalSmtpMailer.from_environment().public_origin=='https://env.example.invalid'
    monkeypatch.setenv('PORTAL_PUBLIC_ORIGIN','')
    mail=PortalSmtpMailer.from_environment()
    assert mail.public_origin=='' and not mail.configured

@pytest.mark.parametrize('origin',[None,[],4,'http://site.invalid','https://site.invalid/path'])
def test_invalid_site_origin_rejected(isolated,origin):
    from management_portal.portal_password_reset import PortalSmtpMailer
    _,site=isolated;site.write_text(json.dumps({'deployment':{'auth':{'public_origin':origin}}}))
    with pytest.raises(ValueError):PortalSmtpMailer.from_environment()

def test_existing_session_signature_cookie_and_access_remain_valid(isolated):
    from management_portal import portal_session_auth as auth
    root,site=isolated
    keyfile=root/'old.key';key=b'isolated-session-key-material-32!';keyfile.write_bytes(key)
    site.write_text(json.dumps({'deployment':{'auth':{'session_signing_key_path':str(keyfile)}}}))
    before=(keyfile.read_bytes(),keyfile.stat().st_mtime_ns)
    payload={'v':1,'u':'alice','iat':100,'exp':1000,'sv':3,'nonce':'fixed-before-upgrade'}
    encoded=auth._b64url_encode(json.dumps(payload,separators=(',',':')).encode())
    token=encoded+'.'+auth._b64url_encode(hmac.new(key,encoded.encode(),hashlib.sha256).digest())
    verifier=auth.PortalSessionAuthority(access_store=access())
    assert verifier.signing_key_path==keyfile
    assert verifier.verify_cookie('db_session='+token,now=200)=='alice'
    assert verifier.verify(token+'broken',now=200)==''
    assert auth.PortalSessionAuthority(access_store=access(False)).verify(token,now=200)==''
    assert auth.PortalSessionAuthority(access_store=access(version=4)).verify(token,now=200)==''
    assert (keyfile.read_bytes(),keyfile.stat().st_mtime_ns)==before

def test_two_factor_existing_ciphertext_and_aad_preserved(isolated,monkeypatch):
    from management_portal import portal_two_factor as tf
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    root,site=isolated
    keyfile=root/'old-factor.key';key=b'F'*32;keyfile.write_bytes(key)
    site.write_text(json.dumps({'deployment':{'auth':{'two_factor_key_path':str(keyfile)}}}))
    _,path=web_paths();assert path==keyfile
    monkeypatch.setattr(tf,'IdentityState',lambda _:None)
    store=tf.PortalTwoFactorStore(path)
    before=(keyfile.read_bytes(),keyfile.stat().st_mtime_ns)
    nonce=b'N'*12;cipher=AESGCM(key).encrypt(nonce,b'JBSWY3DPEHPK3PXP',b'alice')
    record={'nonce':tf._b64encode(nonce),'ciphertext':tf._b64encode(cipher)}
    original=dict(record)
    assert store._decrypt_secret(store._key(),'alice',record)=='JBSWY3DPEHPK3PXP'
    with pytest.raises(InvalidTag):store._decrypt_secret(store._key(),'bob',record)
    assert record==original and (keyfile.read_bytes(),keyfile.stat().st_mtime_ns)==before


def test_cli_key_bypasses_invalid_site_defaults(isolated,monkeypatch):
    root,site=isolated
    site.write_text(json.dumps({'deployment':{'auth':{'session_signing_key_path':[]}}}))
    assert router_key(monkeypatch,['--session-signing-key-path',str(root/'explicit')])==root/'explicit'
    assert list(root.iterdir())==[]
