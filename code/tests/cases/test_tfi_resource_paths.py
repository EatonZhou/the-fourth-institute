import importlib
import json
from pathlib import Path
import pytest

@pytest.fixture
def isolated(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    for name in ['PORTAL_NETWORK_CONFIG','PORTAL_PROVIDER_EGRESS_ENV_FILE','PORTAL_SCOPE_AVATAR_ROOT','PORTAL_RESIDENT_AVATAR_ROOT','PORTAL_USER_AVATAR_ROOT','PORTAL_SSH_KEY_ROOT','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy']:
        monkeypatch.delenv(name,raising=False)
    site=tmp_path/'site.json';site.write_text('{}')
    monkeypatch.setenv('PORTAL_SITE_CONFIG',str(site))
    return home,site

def put(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(data);return path

def stamp(path):return (path.read_bytes(),path.stat().st_mtime_ns)

def test_network_new_legacy_and_explicit(isolated,monkeypatch):
    from management_common import portal_network as n
    home,site=isolated
    new=home/'.config/tfi/portal-network.env';old=home/'.config/archive/portal-network.env'
    assert n.config_path()==new
    put(old,'PORTAL_ALLOWED_NETWORKS="192.0.2.0/24"\n');before=stamp(old)
    site.write_text(json.dumps({'deployment':{'network_config':str(old)}}))
    assert n.config_path()==old
    assert '192.0.2.0/24' in n.allowed_networks()
    assert stamp(old)==before and not new.exists()
    put(new,'PORTAL_ALLOWED_NETWORKS="198.51.100.0/24"\n')
    assert n.config_path()==old
    site.write_text('{}')
    assert n.config_path()==new
    assert '198.51.100.0/24' in n.allowed_networks()
    configured=home/'network.env';site.write_text(json.dumps({'deployment':{'network_config':str(configured)}}))
    assert n.config_path()==configured
    monkeypatch.setenv('PORTAL_NETWORK_CONFIG',str(home/'override.env'))
    assert n.config_path()==home/'override.env'
    monkeypatch.setenv('PORTAL_NETWORK_CONFIG','relative.env')
    with pytest.raises(ValueError):n.config_path()

def test_proxy_legacy_precedence_and_no_write(isolated,monkeypatch):
    from management_common import portal_network as n
    home,site=isolated
    monkeypatch.setenv('HTTPS_PROXY','http://inherited.invalid:8080')
    assert n.inherited_proxy_environment()['HTTPS_PROXY']=='http://inherited.invalid:8080'
    old=put(home/'.config/archive/provider-egress-vps-us.env','HTTPS_PROXY="http://old.invalid:8080"\n')
    before=stamp(old)
    site.write_text(json.dumps({'deployment':{'provider_egress_env_file':str(old)}}))
    assert n.inherited_proxy_environment()['HTTPS_PROXY']=='http://old.invalid:8080'
    assert stamp(old)==before
    new=put(home/'.config/tfi/provider-egress.env','HTTPS_PROXY="http://new.invalid:8080"\n')
    assert n.inherited_proxy_environment()['HTTPS_PROXY']=='http://old.invalid:8080'
    site.write_text('{}')
    assert n.inherited_proxy_environment()['HTTPS_PROXY']=='http://new.invalid:8080'
    custom=put(home/'site-egress.env','HTTPS_PROXY="http://site.invalid:8080"\n')
    site.write_text(json.dumps({'deployment':{'provider_egress_env_file':str(custom)}}))
    assert n.inherited_proxy_environment()['HTTPS_PROXY']=='http://site.invalid:8080'
    custom.unlink()
    with pytest.raises(FileNotFoundError):n.inherited_proxy_environment()
    monkeypatch.setenv('PORTAL_PROVIDER_EGRESS_ENV_FILE',str(new))
    assert n.inherited_proxy_environment()['HTTPS_PROXY']=='http://new.invalid:8080'

@pytest.mark.parametrize('value',[[],None,'bad',7])
def test_network_invalid_deployment_rejected(isolated,value):
    from management_common import portal_network as n
    _,site=isolated;site.write_text(json.dumps({'deployment':value}))
    with pytest.raises(ValueError):n.config_path()

@pytest.mark.parametrize('key',['network_config','provider_egress_env_file'])
@pytest.mark.parametrize('value',[None,0,[],{},'', 'bad\npath'])
def test_network_invalid_path_rejected(isolated,key,value):
    from management_common import portal_network as n
    _,site=isolated;site.write_text(json.dumps({'deployment':{key:value}}))
    with pytest.raises(ValueError):(n.config_path if key=='network_config' else n.inherited_proxy_environment)()

@pytest.mark.parametrize('kind,subject,module_name,factory,schema,identity',[
 ('resident','resident_0123456789ab','resident_avatar','resident_avatar_store','resident-avatar','resident_id'),
 ('user','example','user_avatar','user_avatar_store','user-avatar','username'),
 ('scope','lineage:lineage_0123456789ab','avatar_store','scope_avatar_store','scope-avatar','subject')])
def test_avatar_canonical_root_and_records_only(isolated,monkeypatch,kind,subject,module_name,factory,schema,identity):
    home,site=isolated
    from management_portal import avatar_store
    importlib.reload(avatar_store)
    module=importlib.reload(importlib.import_module('management_portal.'+module_name))
    make=getattr(module,factory)
    new=home/'.tfi/portal'/f'{kind}_avatars';old=home/'.archive/portal'/f'{kind}_avatars'
    assert make().legacy.root==new and not new.exists()
    old.mkdir(parents=True)
    site.write_text(json.dumps({'deployment':{'compatibility':{'format_namespaces':['archive.'],'domain_values':{'avatar_legacy_root':str(old.parent)}}}}))
    record=put(old/'unchanged.json',json.dumps({'schema':'archive.'+schema+'.v1',identity:subject,'sprite_index':8}))
    before=stamp(record)
    assert make().legacy.root==new and not new.exists()
    override=home/'custom-avatar';monkeypatch.setenv('PORTAL_'+kind.upper()+'_AVATAR_ROOT',str(override))
    store=make();assert store.legacy.root==override and not override.exists()
    canonical=put(store.legacy._path(subject),json.dumps({'schema':'tfi.'+schema+'.v1',identity:subject,'sprite_index':8}))
    saved=stamp(canonical)
    monkeypatch.setattr(avatar_store.AvatarStore,'_write',lambda *a,**k:pytest.fail('read must not write'))
    if kind=='resident':
        monkeypatch.setattr(module,'_draw_sprite',lambda:pytest.fail('must reuse saved avatar'))
        result=store.get(subject)
    else:result=store.read(subject)
    assert result['sprite_index']==8 and stamp(canonical)==saved and stamp(record)==before and not new.exists()
    with pytest.raises(ValueError,match='Invalid avatar record'):
        store.legacy._avatar(subject,{'schema':'archive.'+schema+'.v1',identity:subject,'sprite_index':8})
    with pytest.raises(ValueError,match='Invalid avatar record'):
        store.legacy._avatar(subject,{'schema':'tfi.'+schema+'.v1',identity:'different-subject','sprite_index':8})

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
    monkeypatch.setenv('PORTAL_SSH_KEY_ROOT',str(home/'custom-ssh'))
    importlib.reload(rid);importlib.reload(repo)
    assert rid.DEFAULT_SSH_KEY_ROOT==repo._SSH_KEY_ROOT==home/'custom-ssh'

def test_site_paths_follow_installation_and_reread(isolated,monkeypatch,tmp_path):
    from management_common import portal_network as n,portal_paths
    _,site=isolated
    monkeypatch.setattr(portal_paths,'PORTAL_ROOT',tmp_path/'install')
    monkeypatch.chdir(tmp_path)
    site.write_text(json.dumps({'deployment':{'network_config':'settings/network.env','provider_egress_env_file':'settings/egress.env'}}))
    assert n.config_path()==tmp_path/'install/settings/network.env'
    proxy=put(tmp_path/'install/settings/egress.env','HTTPS_PROXY="http://relative.invalid:8080"\n')
    saved=stamp(proxy)
    assert n.inherited_proxy_environment()['HTTPS_PROXY']=='http://relative.invalid:8080'
    assert stamp(proxy)==saved
    site.write_text(json.dumps({'deployment':{'network_config':'settings/other.env'}}))
    assert n.config_path()==tmp_path/'install/settings/other.env'

def test_invalid_new_config_never_reads_legacy(isolated):
    from management_common import portal_network as n
    home,_=isolated
    old=put(home/'.config/archive/portal-network.env','PORTAL_ALLOWED_NETWORKS="192.0.2.0/24"\n')
    new=put(home/'.config/tfi/portal-network.env','not-an-environment-file\n')
    old_state,new_state=stamp(old),stamp(new)
    with pytest.raises(ValueError):n.read_settings()
    assert stamp(old)==old_state and stamp(new)==new_state

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


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
