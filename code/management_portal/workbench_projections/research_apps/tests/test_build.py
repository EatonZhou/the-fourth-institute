"""Build the actual two-entry migration package in the production isolation path."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from management_common.site_config import read_site_config
from management_portal.workbench_projections import application_build as builder
from management_portal.workbench_projections.page import validate_application, validate_page

ROOT=Path(__file__).resolve().parents[1]

def test_research_package_isolated_build(tmp_path,monkeypatch):
    repo=tmp_path/'repo';source=repo/'app'
    shutil.copytree(ROOT,source,ignore=shutil.ignore_patterns('node_modules','dist','__pycache__','.pytest_cache'))
    def git(*args):
        return subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True,text=True).stdout.strip()
    git('init','-q');git('config','user.name','Research package fixture');git('config','user.email','fixture@example.invalid')
    git('add','app');git('commit','-qm','research fixture');commit=git('rev-parse','HEAD')
    actor='resident_00238f249f5e';calls=[]
    def resolve(address,*,actor):
        calls.append((address,actor));return {'root_id':address,'resource_type':'repo','exists':True,'checkout_path':str(repo)}
    monkeypatch.setattr(builder,'get_resource_service',lambda:SimpleNamespace(resolve=resolve))
    # Supply registry observations from the existing public-package cache at
    # the network boundary. Keep production snapshot, dependency resolution,
    # integrity/license checks, npm installation and compilation in bubblewrap.
    source_cache = Path(os.environ.get('PORTAL_RESEARCH_TEST_NPM_CACHE', str(Path.home() / '.npm')))
    sandbox_command = builder._sandbox_command
    seed = r'''
const cacache = require('/usr/lib/node_modules/npm/node_modules/cacache');
(async () => {
  const source = '/source-cache/_cacache', target = '/npm-cache/_cacache';
  const index = await cacache.ls(source);
  let copied = 0;
  for (const [key, info] of Object.entries(index)) {
    if (!/registry\.npmjs\.org\/(?:esbuild(?:\/|$)|@esbuild(?:%2f|\/))/i.test(key)) continue;
    const entry = await cacache.get(source, key);
    await cacache.put(target, key, entry.data, {metadata: info.metadata});
    copied += 1;
  }
  if (!copied) throw new Error('Research build requires populated esbuild npm cache');
})().catch(error => { console.error(error.message); process.exit(1); });
'''
    network_requests = []
    def cached_network(project, command, **kwargs):
        requested = kwargs.pop('network', False)
        if requested:
            assert command[0] == '/usr/bin/npm'
            network_requests.append(command)
            if '--package-lock-only' in command:
                sandbox_command(project, ['/usr/bin/node', '-e', seed],
                                mounts=((source_cache, '/source-cache'),),
                                writable_cache=kwargs['writable_cache'])
            command = [command[0], '--offline', *command[1:]]
        return sandbox_command(project, command, network=False, **kwargs)
    monkeypatch.setattr(builder, '_sandbox_command', cached_network)
    monkeypatch.setenv('PORTAL_PROJECTION_ARTIFACT_ROOT',str(tmp_path/'artifacts'))
    config=read_site_config();config.setdefault('deployment',{})['development_projection']={'repo_id':'repo_123456789abc'}
    site=tmp_path/'site.json';site.write_text(json.dumps(config));monkeypatch.setenv('PORTAL_SITE_CONFIG',str(site))
    spec=importlib.util.spec_from_file_location('research_definition',ROOT/'definition.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    definitions=module.definitions(commit)
    for item in definitions:
        fields=item['fields'];validate_application(fields['application']);validate_page(fields['manifest'],{},fields['bindings'],fields['application'])
        assert fields['bindings']['main']['residents']['coordinator']==fields['bindings']['main']['residents']['facade']=='resident_5549c42d1317'
        assert fields['html']=='' and fields['panels']=={}
    application={**definitions[0]['fields']['application'],'directory':'app','entries':{'semantic':'semantic.html','matrix':'matrix.html'}}
    result=builder.build_application(application,actor)
    assert any('--package-lock-only' in command for command in network_requests)
    assert any('ci' in command for command in network_requests)
    assert calls==[('repo_123456789abc',actor)]
    assert result['status']=='ready' and len(result['files'])==6
    assert result['entries']==application['entries']
    for name in ('semantic','matrix'):
        script,_=builder.read_artifact(result,name+'.js')
        assert b'Projection' in script and b'SemanticMatrixData' not in script
        assert b'39.727' not in script  # historical performance is SDK-read data, not executable source
    report={'commit':commit,'build':result,'definitions':definitions,'scope':'temporary Git source resolver; real isolated npm/build/artifact path; no live resource updates'}
    target=Path(os.environ['PORTAL_RESEARCH_BUILD_REPORT']);target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
