import gzip
import json
from management_portal.distribution.privacy import redact, portable_text, source_users
from management_portal.distribution.engineering import copy_content
from management_portal.distribution.sanitize import scrub


def test_npm_credentials_are_excluded_from_source_and_resource_copies(tmp_path):
    import subprocess
    from management_portal.distribution.engineering import source_files
    from management_portal.distribution.file_scope import FileScope
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    (source / '.npmrc').write_text('//registry.npmjs.org/:_authToken=npm_' + 'A' * 36)
    (source / 'package.json').write_text('{}')
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    assert [str(p) for p in source_files(source)] == ['package.json']
    paths = [str(p) for p, _, directory in FileScope([{'source': str(source)}]).walk(source) if not directory]
    assert paths == ['package.json']
    target = tmp_path / 'copy'
    copy_content(source, target, selected=set(), anonymize=False)
    assert not (target / '.npmrc').exists()
    assert (target / 'package.json').read_text().strip() == '{}'


def test_release_identity_collections_do_not_grant_local_admin():
    value={'scope_ids':['example-test','source-person','source-person'], 'failure_recipients':['source-person'], 'resident_id':'resident_0123456789ab', 'goal':'Ask source-person.'}
    result=redact(value,['source-person'])
    assert result['scope_ids']==['example-test','tfi-exported-user']
    assert result['failure_recipients']==['tfi-exported-user']
    assert result['resident_id']==value['resident_id'] and result['goal']=='Ask TFI portal.'
    assert 'tfi-v07-admin' not in str(result)
    assert source_users({'config_json':json.dumps(value)})==['source-person']


def test_portable_prose_preserves_target_paths_and_copyright(monkeypatch):
    monkeypatch.setattr("management_portal.distribution.privacy.source_environment", lambda: (("/source/code", "${code_root}"), ("/source/home", "${user_home}")))
    result=portable_text('/source/code/a.py 192.168.1.2 /data/tfi-instances/v07-live\nCopyright source-person', ['source-person'])
    assert result=='${code_root}/a.py ${lan_host} /data/tfi-instances/v07-live\nCopyright source-person'
    assert portable_text('192.168.1.2', [], environment=False)=='192.168.1.2'
    assert scrub(json.dumps({'scope_ids':['source-person']}),field='principal_hierarchy_json',source_usernames=['source-person'])=='{"scope_ids":["tfi-exported-user"]}'


def test_compressed_release_fixture_and_source_schema_are_separate(tmp_path):
    src=tmp_path/'fixture.json.gz';dst=tmp_path/'export.json.gz'
    src.write_bytes(gzip.compress(json.dumps({'scope_ids':['source-person'],'goal':'source-person at 10.1.2.3'}).encode()))
    copy_content(src,dst,selected=set(),anonymize=False,source_usernames=['source-person'])
    text=gzip.decompress(dst.read_bytes()).decode()
    assert 'source-person' not in text and '10.1.2.3' not in text
    assert json.loads(text)['scope_ids']==['tfi-exported-user']
    decl=tmp_path/'user.json';out=tmp_path/'out.json'
    original={'type':'user','title':{'en':'User'},'properties':{'password':{'type':'string'}}}
    decl.write_text(json.dumps(original));copy_content(decl,out,selected=set(),anonymize=False,source_usernames=['source-person'])
    assert json.loads(out.read_text())==original


def test_release_code_identity_defaults_are_not_anonymous_authority():
    import ast
    import pytest
    from management_portal.distribution.privacy import release_code_text
    users=['source-person']
    smoke='parser.add_argument("--actor", default="source-person")\n'
    result=release_code_text(smoke,'scripts/dev_resident_lifecycle_smoke.py',users)
    assert 'required=True' in result and 'default=' not in result
    router='value = os.getenv("PORTAL_ROUTER_DEV_USERS", "source-person,other")\n'
    assert '"PORTAL_ROUTER_DEV_USERS", ""' in release_code_text(router,'management_portal/portal_deployment_router.py',users)
    comment='# source-person requested this\nx = 1\n'
    assert ast.dump(ast.parse(comment))==ast.dump(ast.parse(release_code_text(comment,'module.py',users)))
    with pytest.raises(ValueError,match='Unclassified executable'):
        release_code_text('actor = "source-person"','unreviewed.py',users)
    data=release_code_text('{"owner_ids":["source-person"],"principal":{"kind":"user","id":"source-person"}}','legacy-definitions.json',users)
    assert json.loads(data)=={'owner_ids':['tfi-exported-user'],'principal':{'kind':'user','id':'tfi-exported-user'}}


def test_release_fixture_readers_require_portable_input():
    from management_portal.distribution.privacy import release_code_text
    original="const fixturePath=process.env.CAPTURE || '/share/Projects/private/capture.json';"
    out=release_code_text(original,'app/tests/provider.fixture.mjs')
    assert '/share/Projects' not in out and 'Set CAPTURE to a local fixture' in out
    assert 'process.env.CAPTURE' in out
    original="monkeypatch.setenv('PORTAL_PROJECTION_NPM_CACHE','/home/example/.npm')"
    assert "str(tmp_path / 'npm-cache')" in release_code_text(original,'app/tests/test_build.py')


def test_exact_content_mapping_precedes_portable_prose(monkeypatch):
    monkeypatch.setattr("management_portal.distribution.privacy.source_environment", lambda: (("/source/home", "${user_home}"),))
    value={'canonical_root':'/share/Projects/source/article', 'goal':'/source/home/example'}
    result=scrub(value,mappings=[('/share/Projects/source/article','${share_root}/Articles/article_0123456789ab')],source_usernames=['source-person'])
    assert result['canonical_root']=='${share_root}/Articles/article_0123456789ab'
    assert result['goal']=='${user_home}/example'
    target='/data/tfi-instances/v07-live/data/share/Projects/local/article'
    assert portable_text(target)==target


def test_article_export_uses_owner_canonical_root(tmp_path):
    from management_portal.distribution.selection import resource_file_scope
    root=tmp_path/'article';root.mkdir();(root/'main.tex').write_text('Article')
    item=resource_file_scope('article_0123456789ab',{'resource_type':'article','source':{},'attributes':{'canonical_root':str(root)}})
    assert item['source']==str(root)
    assert item['target']=='data/share_root/Articles/article_0123456789ab'


def test_short_identity_code_and_product_are_preserved():
    import ast
    from management_portal.distribution.privacy import release_code_text
    code='import xml.etree.ElementTree as AB\nx = AB.Element("root")\nname = "Example Portal"\n# AB 10-07\n'
    output=release_code_text(code,'service.py',['AB','example'])
    assert ast.dump(ast.parse(code))==ast.dump(ast.parse(output))
    assert '# TFI portal 10-07' in output
    js='const AB = 1; const name = "Example Portal"; // AB 10-07\n'
    assert release_code_text(js,'app.js',['AB','example'])=='const AB = 1; const name = "Example Portal"; // TFI portal 10-07\n'
    assert release_code_text('const AB = "AB";','app.js',['AB'])=='const AB = "AB";'


def test_all_identity_fields_and_brand_overlap():
    users=['AB','example','CD','example-test']
    result=redact({'recipients':[{'kind':'user','id':u} for u in users]+[{'kind':'resident','id':'resident_0123456789ab'}], 'scope_ids':users,'goal':'向 AB 汇报。Example Portal; AB.Element()','profile':'live.example-test','schema':'example.example-test.v2'},users,environment=False)
    assert result['recipients']==[{'kind':'user','id':'tfi-exported-user'},{'kind':'resident','id':'resident_0123456789ab'}]
    assert result['scope_ids']==['tfi-exported-user']
    assert result['goal']=='向 TFI portal 汇报。Example Portal; AB.Element()'
    assert result['profile']=='live.example-test' and result['schema']=='example.example-test.v2'


def test_export_test_actor_requires_target_configuration(monkeypatch):
    from management_portal.distribution.privacy import release_code_text
    monkeypatch.delenv('PORTAL_TEST_ACTOR',raising=False)
    code='EXPECTED_ACTOR = "example-test"\ndef accepts(actor):\n    if actor != EXPECTED_ACTOR:\n        return False\n    return True\n'
    output=release_code_text(code,'management_portal/test_framework/dev_target.py',['example-test']);scope={};exec(output,scope)
    assert not scope['accepts']('') and not scope['accepts']('tfi-v07-admin')
    monkeypatch.setenv('PORTAL_TEST_ACTOR','local-test-user');exec(output,scope)
    assert scope['accepts']('local-test-user')
    assert not scope['accepts']('tfi-v07-admin')


def test_fresh_engineering_article_content_after_install_expansion(tmp_path):
    """Use production selection, copy, generation and installer path expansion."""
    import ast
    import subprocess
    from pathlib import Path
    from management_portal.distribution.selection import resource_file_scope
    from management_portal.distribution.engineering import generate
    from scripts.tfi_release_install import map_directories
    source=tmp_path/'source';source.mkdir()
    subprocess.run(['git','init','-q',str(source)],check=True)
    for name in ('LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','scripts/install_tfi_release','scripts/tfi_release_install.py'):
        p=source/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_text('# minimal source\n')
    original='import xml.etree.ElementTree as AB\nvalue=AB.Element("root")\n# AB 10-07\n'
    (source/'service.py').write_text(original)
    article=tmp_path/'article';article.mkdir();(article/'main.tex').write_text('Readable article正文')
    identity='article_0123456789ab'
    record={'resource_type':'article','attributes':{'canonical_root':str(article)}}
    files=[resource_file_scope(identity,record)]
    mapping=[(str(article),'${share_root}/Articles/'+identity)]
    document={'canonical_root':str(article),'roots':[{'path':str(article)}],'primary_file':'main.tex','scope_ids':['AB','example-user']}
    clean=scrub(document,mappings=mapping,source_usernames=['AB','example-user'])
    resolved={'selected_ids':[identity],'features':[],'unresolved':[],'files':files,'source_usernames':['AB','example-user'],'path_mappings':mapping}
    output=tmp_path/'engineering'
    manifest=generate(source_root=source,destination=output,snapshot={'source':'Dev'},resolved=resolved,database_sql=json.dumps(clean),run_id='local-generator-regression')
    assert manifest['resolved_objects']==[identity]
    assert (output/'data/share_root/Articles'/identity/'main.tex').read_text()=='Readable article正文'
    assert ast.dump(ast.parse((output/'code/service.py').read_text()))==ast.dump(ast.parse(original))
    target=tmp_path/'installed-share'
    # Installer expands portable SQL only at installation, using actual target roots.
    expanded=map_directories((output/'database/distribution.sql').read_text(),{'${share_root}':str(target)})
    installed=json.loads(expanded)
    assert installed['canonical_root']==str(target/'Articles'/identity)
    assert installed['roots'][0]['path']==installed['canonical_root']
    assert installed['scope_ids']==['tfi-exported-user']


def test_identity_annotation_without_default_is_not_a_literal():
    import pytest
    from management_portal.distribution.privacy import release_code_text
    original = 'class Request:\n    actor: str\n    owner: str\n'
    assert release_code_text(original, 'service.py', ['source-person']) == original
    with pytest.raises(ValueError, match='Unclassified executable source identity'):
        release_code_text('actor: str = "source-person"\n', 'service.py', ['source-person'])


def test_python_fstring_braces_are_not_retokenized():
    from management_portal.distribution.privacy import release_code_text
    original='x = rf"{prefix}.{{0,260}}"\n# AB 10-07\n'
    out=release_code_text(original,'module.py',['AB'])
    assert out=='x = rf"{prefix}.{{0,260}}"\n# TFI portal 10-07\n'


def test_source_identity_set_uses_authority_when_index_is_incomplete():
    from management_portal.distribution.database import DatabaseSnapshot
    class Result(list):
        def fetchone(self): return self[0]
    class Connection:
        queries=[]
        def execute(self,query,params=()):
            text=query if isinstance(query,str) else query.as_string()
            self.queries.append(text)
            if 'to_regclass' in text:return Result([{'name':params[0]}])
            if 'user_accounts' in text:return Result([{'identity':'example-account'}])
            return Result([{'identity':'example-legacy'}])
    source=object.__new__(DatabaseSnapshot);source.connection=Connection();source.read_index=lambda **_:{}
    assert source.source_identity_names()==['example-account','example-legacy']
    assert all('password' not in q and 'SELECT *' not in q and 'email' not in q for q in source.connection.queries)
    count=len(source.connection.queries);source.source_identity_names();assert len(source.connection.queries)==count
    assert source_users({'recipients':[{'kind':'user','id':'example-legacy'},{'kind':'resident','id':'resident_0123456789ab'}]})==['example-legacy']
