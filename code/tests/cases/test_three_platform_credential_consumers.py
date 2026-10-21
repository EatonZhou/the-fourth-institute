"""Private credential consumer boundaries; isolated files, no remote auth.

:module: security.identity-access
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from management_common import file_permissions as private
from management_common import portal_network as network
from management_portal import git_credential_portal as helper
from management_portal import git_ssh_askpass_portal as askpass
from scripts import portal_user as cli


def material(tmp_path, **updates):
    path = tmp_path / 'material.json'
    payload = {'protocol': 'https', 'host': 'git.example', 'path': 'team/repo.git',
               'username': 'tester', 'secret': 'fixture-secret', 'expires_at': time.time() + 60}
    payload.update(updates)
    path.write_text(json.dumps(payload)); path.chmod(0o600)
    return path


def invoke_helper(monkeypatch, path, *, ssh=False, host='git.example', resource='team/repo.git'):
    monkeypatch.setenv('PORTAL_GIT_CREDENTIAL_FILE', str(path))
    monkeypatch.setattr(sys, 'argv', ['helper', 'get'])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(f'protocol=https\nhost={host}\npath={resource}\n\n'))
    return (askpass if ssh else helper).main()


@pytest.mark.parametrize('ssh', [False, True])
@pytest.mark.parametrize('unsafe', ['symlink', 'permissions', 'readonly-mode', 'executable-mode', 'expired', 'bad-expiry', 'linebreak', 'malformed'])
def test_helper_never_emits_unsafe_material(tmp_path, monkeypatch, capsys, ssh, unsafe):
    path = material(tmp_path)
    if unsafe == 'symlink':
        alias = tmp_path / 'alias'; alias.symlink_to(path); path = alias
    elif unsafe == 'permissions': path.chmod(0o644)
    elif unsafe == 'readonly-mode': path.chmod(0o400)
    elif unsafe == 'executable-mode': path.chmod(0o700)
    elif unsafe == 'expired': path = material(tmp_path, expires_at=time.time() - 1)
    elif unsafe == 'bad-expiry': path = material(tmp_path, expires_at='unknown')
    elif unsafe == 'linebreak': path = material(tmp_path, secret='fixture-secret\ninjected=true')
    else: path.write_text('{"secret":"fixture-secret" invalid')
    invoke_helper(monkeypatch, path, ssh=ssh)
    captured = capsys.readouterr()
    assert 'fixture-secret' not in captured.out + captured.err
    assert 'Traceback' not in captured.err


def test_helper_matches_host_and_path(tmp_path, monkeypatch, capsys):
    path = material(tmp_path)
    invoke_helper(monkeypatch, path, resource='other/repo.git')
    assert capsys.readouterr().out == 'quit=true\n'
    invoke_helper(monkeypatch, path, host='different.example')
    assert capsys.readouterr().out == 'quit=true\n'
    invoke_helper(monkeypatch, path)
    assert capsys.readouterr().out == 'username=tester\npassword=fixture-secret\nquit=true\n'


@pytest.mark.parametrize('ssh', [False, True])
def test_helper_uses_private_reader_without_posix_identity_api(tmp_path, monkeypatch, capsys, ssh):
    path = material(tmp_path)
    # The common ACL adapter owns OS-specific identity; this consumer gets only bytes.
    monkeypatch.setattr(private, 'read_private_bytes', lambda p, **kw: path.read_bytes())
    monkeypatch.delattr(os, 'geteuid', raising=False)
    invoke_helper(monkeypatch, path, ssh=ssh)
    assert 'fixture-secret' in capsys.readouterr().out


def test_cli_private_reader_owns_identity_check(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'cli.json'
    path.write_text(json.dumps({'url':'https://portal.example', 'token':'dbu_fixture', 'status':'active'}))
    path.chmod(0o600)
    monkeypatch.setattr(private, 'read_private_bytes', lambda p, **kw: path.read_bytes())
    monkeypatch.delattr(os, 'getuid', raising=False)
    monkeypatch.setattr(sys, 'argv', ['portal_user.py','--url','https://portal.example','--credentials',str(path),'call','GetUser'])
    monkeypatch.setattr(cli, 'request', lambda *a: {'username':'tester'})
    cli.main()
    assert 'tester' in capsys.readouterr().out


def test_cli_failed_replace_keeps_original_private_token(tmp_path, monkeypatch):
    tmp_path = tmp_path / 'credential-write'
    tmp_path.mkdir()
    path = tmp_path / 'cli.json'
    path.write_text(json.dumps({'url':'https://portal.example', 'token':'dbu_fixture', 'status':'active'})); path.chmod(0o600)
    before = path.read_bytes()
    def reject(*a, **kw): raise OSError('replacement denied')
    monkeypatch.setattr(os, 'replace', reject)
    monkeypatch.setattr(sys, 'argv', ['portal_user.py','--url','https://alias.example','--credentials',str(path),'trust-url'])
    with pytest.raises(SystemExit): cli.main()
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['cli.json']


def test_cli_rejects_hardlink_before_sending_token(tmp_path, monkeypatch):
    path = tmp_path / 'cli.json'
    path.write_text(json.dumps({'url':'https://portal.example','token':'dbu_fixture','status':'active'})); path.chmod(0o600)
    os.link(path, tmp_path / 'alias')
    monkeypatch.setattr(sys, 'argv', ['portal_user.py','--url','https://portal.example','--credentials',str(path),'call','GetUser'])
    calls=[]; monkeypatch.setattr(cli, 'request', lambda *a: calls.append(a))
    with pytest.raises(SystemExit): cli.main()
    assert calls == []


def test_network_security_failure_precedes_payload_and_replace(tmp_path, monkeypatch):
    tmp_path = tmp_path / 'network-write'
    tmp_path.mkdir()
    target = tmp_path / 'network.env'; target.write_bytes(b'original')
    def reject(path, mode):
        assert path.read_bytes() == b''
        raise PermissionError('ACL cannot be verified')
    monkeypatch.setattr(private, 'set_created_file_permissions', reject)
    with pytest.raises(PermissionError): network._write(target, b'new proxy config')
    assert target.read_bytes() == b'original'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['network.env']


def test_network_consumer_needs_no_fchmod(tmp_path, monkeypatch):
    # Real atomic write with an isolated Windows-style permissions boundary.
    calls=[]
    def secure(path, mode):
        assert mode == 0o600 and path.read_bytes() == b''
        calls.append(path)
    monkeypatch.setattr(private, 'set_created_file_permissions', secure)
    monkeypatch.setattr(network, 'os', SimpleNamespace(**{n:getattr(os,n) for n in ('fdopen','fsync','replace')}))
    target = tmp_path / 'network.env'
    network._write(target, b'new config')
    assert target.read_bytes() == b'new config' and calls


def test_downloaded_cli_carries_private_owner_and_works_without_repo(tmp_path, monkeypatch):
    from management_portal import http_user_guide as guide
    sent=[]
    server=SimpleNamespace(_serve_bytes=lambda req,status,data,*a,**kw: sent.append(data))
    monkeypatch.setitem(sys.modules, 'management_portal.serve_management_portal', server)
    monkeypatch.setattr(__import__('management_portal'), 'serve_management_portal', server, raising=False)
    guide.client_script(None,None,None)
    script=tmp_path/'downloaded.py'; script.write_bytes(sent[0])
    path=tmp_path/'token.json'; path.write_text(json.dumps({'url':'https://portal.example','token':'dbu_fixture','status':'active'})); path.chmod(0o600)
    env={k:v for k,v in os.environ.items() if k != 'PYTHONPATH'}
    result=subprocess.run([sys.executable,'-I',str(script),'--url','https://alias.example','--credentials',str(path),'trust-url'],cwd=tmp_path,env=env,text=True,capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(path.read_text())['trusted_urls'] == ['https://portal.example','https://alias.example']
    assert 'dbu_fixture' not in result.stdout + result.stderr
    os.link(path,tmp_path/'hardlink')
    denied=subprocess.run([sys.executable,'-I',str(script),'--url','https://alias.example','--credentials',str(path),'untrust-url'],cwd=tmp_path,env=env,text=True,capture_output=True)
    assert denied.returncode != 0


def test_git_launches_helper_through_current_python_with_spaces(tmp_path, monkeypatch):
    from management_portal import repo_workspace_backend as repo
    alias=tmp_path/'helper with spaces.py'
    alias.write_text('import runpy\nrunpy.run_path('+repr(str(Path(helper.__file__)))+', run_name="__main__")\n')
    monkeypatch.setattr(repo, '_GIT_CREDENTIAL_HELPER', alias)
    seen=[]
    def git(checkout,*args,git_config,env_overrides,**kw):
        path=Path(env_overrides['PORTAL_GIT_CREDENTIAL_FILE']); seen.append(path)
        data=json.loads(private.read_private_bytes(path))
        assert data['path']=='team/repo.git' and data['expires_at'] > time.time()
        argv=['git']
        for value in git_config: argv += ['-c',value]
        argv += ['credential','fill']
        result=subprocess.run(argv,input='protocol=https\nhost=git.example\npath=team/repo.git\n\n',env={**os.environ,**env_overrides,'GIT_TERMINAL_PROMPT':'0'},cwd=tmp_path,text=True,capture_output=True)
        assert result.returncode == 0, result.stderr
        assert 'password=fixture-secret' in result.stdout
        return 'success'
    monkeypatch.setattr(repo, '_git', git)
    repo._network_git(tmp_path,'fetch',repo_id='repo_test',operation='fetch',remote='origin',branch='main',context={'protocol':'https','host':'git.example','path':'team/repo.git'},credential={'username':'tester','secret':'fixture-secret'})
    assert seen and all(not p.exists() for p in seen)


def test_ssh_launcher_uses_python_and_cleans_up_after_failure(tmp_path, monkeypatch):
    from management_portal import repo_workspace_backend as repo
    seen=[]
    def git(checkout,*args,env_overrides,**kw):
        path=Path(env_overrides['SSH_ASKPASS']); material_path=Path(env_overrides['PORTAL_GIT_CREDENTIAL_FILE'])
        seen.extend([path,material_path])
        assert path.suffix != '.py'
        result=subprocess.run([str(path), 'password $(must-not-run)'],cwd=tmp_path,env={**os.environ,**env_overrides},text=True,capture_output=True)
        assert result.returncode == 0 and result.stdout == 'fixture-secret\n'
        raise RuntimeError('isolated transport failure')
    monkeypatch.setattr(repo, '_git', git)
    with pytest.raises(RuntimeError,match='isolated transport'):
        repo._network_git(tmp_path,'fetch',repo_id='repo_test',operation='fetch',remote='origin',branch='main',context={'protocol':'ssh','host':'git.example','path':'team/repo.git'},credential={'username':'tester','secret':'fixture-secret'})
    assert seen and all(not p.exists() for p in seen)


def test_windows_askpass_selects_git_posix_launcher_or_rejects(tmp_path, monkeypatch):
    from management_portal import repo_workspace_backend as repo
    root=tmp_path/'Git with spaces'; (root/'cmd').mkdir(parents=True); (root/'usr/bin').mkdir(parents=True)
    git=root/'cmd/git.exe'; git.touch()
    ssh=root/'usr/bin/ssh.exe'; ssh.touch(); (root/'usr/bin/sh.exe').touch()
    monkeypatch.setattr(repo,'sys',SimpleNamespace(platform='win32',executable='C:/Python/python.exe'),raising=False)
    monkeypatch.setattr(repo.shutil,'which',lambda name:str(git) if name=='git' else None)
    command=repo._ssh_command(identity_file=None,batch_mode=False)
    import shlex
    assert shlex.split(command)[0] == ssh.as_posix()
    ssh.unlink()
    with pytest.raises(repo.GitOperationError): repo._ssh_command(identity_file=None,batch_mode=False)
