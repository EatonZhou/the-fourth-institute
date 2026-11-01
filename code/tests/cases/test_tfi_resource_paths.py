import importlib
import json
from pathlib import Path
import pytest

from tests.cases.instance_test_support import select_instance

@pytest.fixture
def isolated(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    for name in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy']:
        monkeypatch.delenv(name,raising=False)
    instance=select_instance(monkeypatch,tmp_path/'instance')
    return home,instance

def put(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(data);return path

def stamp(path):return (path.read_bytes(),path.stat().st_mtime_ns)

def test_network_settings_belong_to_the_instance_secrets(isolated):
    from management_common import portal_network as n
    _,instance=isolated
    assert n.config_path()==instance.root/'secrets/network.env'
    assert n.saved_proxy_environment() is None
    saved=put(n.config_path(),'HTTPS_PROXY="http://saved.invalid:8080"\n');before=stamp(saved)
    assert n.saved_proxy_environment()['HTTPS_PROXY']=='http://saved.invalid:8080'
    assert stamp(saved)==before

def test_allowed_networks_follow_the_listening_address_and_site_settings(tmp_path,monkeypatch):
    from management_common import portal_network as n
    root=select_instance(monkeypatch,tmp_path/'local').root
    assert n.allowed_networks()==list(n.LOOPBACK_NETWORKS)
    remote=select_instance(monkeypatch,tmp_path/'remote',bind='0.0.0.0',
                           site={'deployment':{'router':{'allowed_networks':['192.0.2.0/24']}}}).root
    assert n.allowed_networks()==['192.0.2.0/24',*n.LOOPBACK_NETWORKS]
    assert root!=remote

def test_provider_egress_file_precedes_inherited_proxy_and_is_never_written(isolated,monkeypatch):
    from management_common import portal_network as n
    _,instance=isolated
    monkeypatch.setenv('HTTPS_PROXY','http://inherited.invalid:8080')
    assert n.inherited_proxy_environment()['HTTPS_PROXY']=='http://inherited.invalid:8080'
    egress=put(instance.root/'secrets/host/provider-egress.env','HTTPS_PROXY="http://egress.invalid:8080"\n')
    before=stamp(egress)
    assert n.inherited_proxy_environment()['HTTPS_PROXY']=='http://egress.invalid:8080'
    assert stamp(egress)==before

def test_invalid_network_file_is_rejected_without_rewriting_it(isolated):
    from management_common import portal_network as n
    _,instance=isolated
    bad=put(n.config_path(),'not-an-environment-file\n');before=stamp(bad)
    with pytest.raises(ValueError):n.saved_proxy_environment()
    with pytest.raises(ValueError):n.read_settings()
    assert stamp(bad)==before

def test_tool_defaults_follow_user_home(isolated,monkeypatch):
    home,_=isolated
    from management_portal import resource_identity_backend as rid,repo_workspace_network as repo,article_texlab_backend as texlab
    from management_portal import article_toolchain
    importlib.reload(rid);importlib.reload(repo);importlib.reload(article_toolchain)
    assert rid.DEFAULT_SSH_KEY_ROOT==repo._SSH_KEY_ROOT==home/'.ssh'
    assert all(str(p).startswith(str(home)) for p in article_toolchain.TEX_BIN_DIRS)
    tiny=home/'.TinyTeX/bin/x86_64-linux';tiny.mkdir(parents=True)
    importlib.reload(article_toolchain)
    assert str(tiny) in article_toolchain._tex_search_path().split(':')
    executable=put(home/'.local/bin/texlab','#!/bin/sh\n');executable.chmod(0o700)
    monkeypatch.setenv('PATH', str(home/'empty-bin'))
    assert texlab._texlab_command()==str(executable)
    executable.chmod(0o600)
    with pytest.raises(RuntimeError):texlab._texlab_command()

def test_ssh_selected_existing_key_preserves_bytes(isolated,monkeypatch):
    from management_portal import resource_identity_backend as rid,repo_workspace_checkout as checkout,repo_workspace_network as repo
    home,_=isolated
    importlib.reload(rid);importlib.reload(repo)
    private=put(home/'.ssh/id_fixture','test-only-not-a-key\n')
    public=put(home/'.ssh/id_fixture.pub','test-only-not-a-public-key\n')
    before=(stamp(private),stamp(public))
    assert repo._repo_ssh_identity({'ssh_key':'id_fixture'})==private
    assert (stamp(private),stamp(public))==before
    with pytest.raises(checkout.RepoWorkspaceConflict):repo._repo_ssh_identity({'ssh_key':'../wrong'})
