import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import check_portal_architecture as check


D = 'decision_111111111111'
P = 'plan_111111111111'
Q = 'plan_222222222222'


def fixture(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/owned.py').write_text('')
    mapping = {'schema': check.SCHEMA, 'modules': [
        {'id': 'one', 'code_paths': ['src'], 'designs': [D], 'instruction_owners': ['lineage']}],
        'protocols': []}
    docs = {D: {'links': {'source': [str(tmp_path / 'src/owned.py')]}}}
    return check.Repository(tmp_path), mapping, docs


def test_catalog_ignores_prose_fences_and_thinking():
    """Design 目录只从表格提取 ID，忽略正文和代码块中的 ID"""
    text = f'{D}\n\n| Name | ID |\n| --- | --- |\n| One | `{D}` |\n\n```\n| X | decision_222222222222 |\n```\n'
    assert check.indexed_ids(text, 'decision') == [D]


def test_catalog_reads_list_resource_links_without_indexing_examples():
    text = (f'# SPEC\n\nMention {D}\n\n'
            f'- [Design](/r/{D}) · [Copy](docs/design.md)\n'
            f'- [Thinking](/r/thinking_111111111111)\n'
            f'- [Plan](/r/{P})\n\n'
            '```md\n- [Example](/r/decision_222222222222)\n```\n')
    assert check.indexed_ids(text, 'decision') == [D]
    assert check.indexed_ids(text, 'plan') == [P]


def runtime_fixture(tmp_path):
    repo, mapping, docs = fixture(tmp_path)
    mapping['model_version'] = 2
    mapping['modules'][0].update(level='runtime', layer='service', title='Service')
    (tmp_path / 'client.py').write_text('')
    mapping['modules'].append({'id': 'client', 'level': 'runtime', 'layer': 'frontend',
                              'title': 'Browser', 'code_paths': ['client.py'], 'designs': []})
    mapping['protocols'] = [{'id': 'read', 'title': 'Read protocol', 'modules': ['one', 'client'],
                             'description': 'read by address', 'example': 'browser reads a record',
                             'designs': [D], 'related_designs': []}]
    mapping['modules'][0]['designs'] = []
    docs[D]['links']['source'].append('client.py')
    return repo, mapping, docs


def runtime_audit(repo, mapping, docs):
    return check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}])


@pytest.mark.parametrize('schema', ['tfi.portal-architecture.v1', 'tfi.portal-architecture.v1'])
def test_map_schema_accepts_current_and_legacy_namespace_without_rewriting(tmp_path, schema):
    """格式命名空间可双读，核对不重写输入地图。"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['schema'] = schema
    before = json.dumps(mapping, sort_keys=True)
    assert runtime_audit(repo, mapping, docs)['findings'] == []
    assert json.dumps(mapping, sort_keys=True) == before


@pytest.mark.parametrize('schema', [
    'tfi.portal-architecture.v2', 'tfi.portal-architecture.v2',
    'tfi.portal-architecture.v0', 'tfi.portal-architecture.v0',
    'tfi.portal-architecture.v1.extra', 'tfi.other.v1', None, {'version': 1},
])
def test_map_schema_rejects_wrong_versions_and_other_formats(tmp_path, schema):
    """双读只兼容同格式同版本，不能接受其他标签或非字符串。"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['schema'] = schema
    with pytest.raises(ValueError, match='unsupported module map schema'):
        runtime_audit(repo, mapping, docs)


@pytest.mark.parametrize('schema', ['tfi.portal-architecture.v1', 'tfi.portal-architecture.v1'])
def test_map_schema_alias_keeps_source_dependency_checks(tmp_path, schema):
    """接受新旧标签后仍报告缺失且未映射的来源。"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['schema'] = schema
    docs[D]['links']['source'].append('missing.py')
    assert runtime_audit(repo, mapping, docs)['by_code'] == {
        'design.source_missing': 1, 'design.source_unmapped': 1}


def test_direct_cli_imports_from_outside_repository(tmp_path):
    """独立 CLI 不依赖工作目录或 PYTHONPATH 才能导入共享 helper。"""
    result = subprocess.run(
        [sys.executable, '-I', str(Path(check.__file__).resolve()), '--help'],
        cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert '--repo-root' in result.stdout


def test_protocol_can_uniquely_own_design_and_cover_its_participants(tmp_path):
    """协议可以唯一归属 Design 并覆盖其参与模块的源码"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    report = runtime_audit(repo, mapping, docs)
    assert report['findings'] == []
    assert report['design_owners'][D] == {'kind': 'protocol', 'id': 'read'}


def test_protocol_participants_do_not_cover_unrelated_implementation(tmp_path):
    """协议参与者不覆盖无关模块的实现，未映射和缺失的源码分别报出"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    (tmp_path / 'unrelated.py').write_text('')
    mapping['modules'].append({'id': 'third', 'level': 'runtime', 'layer': 'service',
                              'title': 'Other service', 'code_paths': ['unrelated.py'], 'designs': []})
    docs[D]['links']['source'].extend(['unrelated.py', 'missing.py'])
    report = runtime_audit(repo, mapping, docs)
    assert report['by_code'] == {'design.source_without_protocol': 1, 'design.source_unmapped': 1,
                                'design.source_missing': 1}


def test_related_design_is_evidence_not_another_primary_owner(tmp_path):
    """协议的 related_designs 只作证据，不构成第二个主归属"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['modules'][0]['designs'] = [D]
    mapping['protocols'][0]['designs'] = []
    mapping['protocols'][0]['related_designs'] = [D]
    report = runtime_audit(repo, mapping, docs)
    assert report['findings'] == []
    assert report['design_owners'][D] == {'kind': 'module', 'id': 'one'}
    mapping['protocols'][0]['designs'] = [D]
    assert runtime_audit(repo, mapping, docs)['by_code'] == {'design.ownership': 1}


def test_protocol_endpoint_covers_components_but_not_sibling_endpoints(tmp_path):
    """协议端点覆盖其下组件的源码，端点换成组件后不再覆盖父模块自身代码"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['modules'][0]['code_paths'] = ['src/owned.py']
    (tmp_path / 'component.py').write_text('')
    mapping['modules'].append({'id': 'one.component', 'parent': 'one', 'level': 'component',
                              'layer': 'service', 'title': 'Resolver', 'code_paths': ['component.py'], 'designs': []})
    docs[D]['links']['source'].append('component.py')
    assert runtime_audit(repo, mapping, docs)['findings'] == []
    mapping['protocols'][0]['modules'] = ['one.component', 'client']
    assert runtime_audit(repo, mapping, docs)['by_code'] == {'design.source_without_protocol': 1}


def test_map_checks_runtime_layers_component_parents_and_duplicate_protocols(tmp_path):
    """模块映射报出非法层级、组件父级错误和重复协议"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['modules'][0]['layer'] = 'magic'
    mapping['modules'][1].update(parent='client', level='component')
    mapping['protocols'].append(dict(mapping['protocols'][0], designs=[]))
    report = runtime_audit(repo, mapping, docs)
    assert report['by_code']['module.invalid_layer'] == 1
    assert report['by_code']['module.invalid_parent'] == 1
    assert report['by_code']['protocol.duplicate'] == 1


def test_external_actor_has_no_fabricated_repository_implementation(tmp_path):
    """外部模块不需要仓库代码路径，也不产生发现"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['modules'].append({'id': 'models', 'title': 'Model services', 'level': 'external',
                              'layer': 'external', 'implementation': 'external',
                              'code_paths': [], 'designs': []})
    assert runtime_audit(repo, mapping, docs)['findings'] == []


def composition_fixture(tmp_path):
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['protocols'] = []
    mapping['modules'][0]['code_paths'] = ['src/owned.py']
    for mid, parent in [('branch', 'one'), ('leaf', 'branch'), ('deep', 'leaf'),
                        ('below', 'deep'), ('sibling', 'branch'), ('cousin', 'one')]:
        (tmp_path / f'{mid}.py').write_text('')
        mapping['modules'].append({'id': mid, 'parent': parent, 'level': 'component',
                                  'layer': 'service', 'title': mid,
                                  'code_paths': [f'{mid}.py'], 'designs': [D] if mid == 'leaf' else []})
    docs[D]['links']['source'] = ['src/owned.py', 'branch.py', 'leaf.py', 'deep.py', 'below.py']
    return repo, mapping, docs


def test_module_design_covers_all_descendants_and_ancestors_own_paths(tmp_path):
    """模块的 Design 覆盖其全部后代模块和祖先自有路径的源码"""
    repo, mapping, docs = composition_fixture(tmp_path)
    assert runtime_audit(repo, mapping, docs)['findings'] == []


def test_composition_excludes_siblings_cousins_and_other_runtimes(tmp_path):
    """模块组合不覆盖兄弟、旁支和其他运行时的源码，需要显式协议列出参与者"""
    repo, mapping, docs = composition_fixture(tmp_path)
    docs[D]['links']['source'] += ['sibling.py', 'cousin.py', 'client.py']
    report = runtime_audit(repo, mapping, docs)
    assert report['by_code'] == {'design.source_without_protocol': 3}
    assert {f['path'] for f in report['findings']} == {'sibling.py', 'cousin.py', 'client.py'}
    mapping['protocols'] = [{'id': 'cross', 'title': 'Explicit participants',
                            'modules': ['leaf', 'sibling', 'cousin', 'client'],
                            'description': 'Named calls', 'example': 'leaf calls sibling',
                            'designs': [], 'related_designs': [D]}]
    assert runtime_audit(repo, mapping, docs)['findings'] == []


@pytest.mark.parametrize('parent_path', ['src', 'src/*.py'])
def test_ancestor_directory_does_not_cover_sibling_component(tmp_path, parent_path):
    """父模块目录或通配符不能将已登记的兄弟组件源码当作祖先自身代码放行"""
    repo, mapping, docs = composition_fixture(tmp_path)
    by_id = {m['id']: m for m in mapping['modules']}
    by_id['one']['code_paths'] = [parent_path]
    by_id['sibling']['code_paths'] = ['src/sibling.py']
    (tmp_path / 'src/sibling.py').write_text('')
    docs[D]['links']['source'].append('src/sibling.py')
    report = runtime_audit(repo, mapping, docs)
    assert report['by_code'] == {'design.source_without_protocol': 1}
    assert report['findings'][0]['candidate_modules'] == ['sibling']
    mapping['protocols'] = [{'id': 'cross', 'title': 'Sibling call',
                            'modules': ['leaf', 'sibling'], 'designs': [],
                            'related_designs': [D], 'description': 'Call sibling',
                            'example': 'leaf calls sibling'}]
    assert runtime_audit(repo, mapping, docs)['findings'] == []


def test_parent_graph_reports_missing_and_multi_node_cycle_without_granting_scope(tmp_path):
    """模块父级缺失和多节点环被报出，环内模块不因此获得覆盖范围"""
    repo, mapping, docs = composition_fixture(tmp_path)
    by_id = {m['id']: m for m in mapping['modules']}
    by_id['branch']['parent'] = 'deep'
    by_id['cousin']['parent'] = 'absent'
    docs[D]['links']['source'] = ['leaf.py', 'branch.py']
    report = runtime_audit(repo, mapping, docs)
    invalid = [f for f in report['findings'] if f['code'] == 'module.invalid_parent']
    assert len(invalid) == 2
    assert any(f['subject'] == 'cousin' and f['parent'] == 'absent' for f in invalid)
    assert any(set(f.get('cycle', [])) == {'branch', 'leaf', 'deep'} for f in invalid)
    assert report['by_code']['design.source_without_protocol'] == 1


def test_protocol_endpoint_covers_arbitrarily_deep_descendants(tmp_path):
    """协议端点模块覆盖任意深度后代模块的源码"""
    repo, mapping, docs = composition_fixture(tmp_path)
    next(m for m in mapping['modules'] if m['id'] == 'leaf')['designs'] = []
    mapping['protocols'] = [{'id': 'read', 'title': 'Read', 'modules': ['one', 'client'],
                            'description': 'read request', 'example': 'client reads leaf', 'designs': [D]}]
    docs[D]['links']['source'] = ['below.py', 'client.py']
    assert runtime_audit(repo, mapping, docs)['findings'] == []


def test_system_overview_has_unique_primary_owner_and_separate_report_list(tmp_path):
    """系统级 Design 唯一归属 system 并在报告中单列，被模块或协议再认领时报归属冲突"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['system'] = {'id': 'portal', 'designs': [D]}
    mapping['protocols'] = []
    report = runtime_audit(repo, mapping, docs)
    assert report['findings'] == []
    assert report['design_owners'][D] == {'kind': 'system', 'id': 'portal'}
    assert report['system_designs'] == [D]
    mapping['modules'][0]['designs'] = [D]
    assert runtime_audit(repo, mapping, docs)['by_code'] == {'design.ownership': 1}
    mapping['modules'][0]['designs'] = []
    mapping['protocols'] = [{'id': 'read', 'title': 'Read', 'modules': ['one', 'client'],
                            'description': 'read request', 'example': 'client reads one', 'designs': [D]}]
    assert runtime_audit(repo, mapping, docs)['by_code'] == {'design.ownership': 1}


def test_system_overview_sources_still_require_existing_mapped_implementation(tmp_path):
    """系统级 Design 引用的源码仍须存在并已映射到模块"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['system'] = {'id': 'portal', 'designs': [D]}
    mapping['protocols'] = []
    (tmp_path / 'unmapped.py').write_text('')
    docs[D]['links']['source'] += ['unmapped.py', 'src/missing.py']
    report = runtime_audit(repo, mapping, docs)
    assert report['by_code'] == {'design.source_unmapped': 1, 'design.source_missing': 1}


def test_system_is_not_an_ancestor_and_legacy_singular_design_is_not_primary(tmp_path):
    """system 不能作为模块父级，旧的单数 design 字段不构成主归属"""
    repo, mapping, docs = runtime_fixture(tmp_path)
    mapping['system'] = {'id': 'portal', 'design': D}
    assert runtime_audit(repo, mapping, docs)['findings'] == []
    mapping['protocols'] = []
    mapping['modules'][0].update(parent='portal', level='component', designs=[D])
    report = runtime_audit(repo, mapping, docs)
    assert report['by_code'] == {'module.invalid_parent': 1, 'design.source_without_protocol': 1}


def test_legacy_protocol_designs_remain_non_owning_for_historical_audits(tmp_path):
    """旧格式协议里的 Design 不计入主归属，历史审计不产生发现"""
    repo, mapping, docs = fixture(tmp_path)
    mapping['protocols'] = [{'id': 'old', 'modules': ['one', 'one'], 'designs': [D],
                            'description': 'old evidence contract', 'example': 'read'}]
    assert runtime_audit(repo, mapping, docs)['findings'] == []


def test_clean_map_and_exact_directory_boundary(tmp_path):
    """完整的模块映射没有发现，目录边界按精确路径判断，相似前缀目录的源码报未映射"""
    repo, mapping, docs = fixture(tmp_path)
    clean = check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}])
    assert clean['findings'] == []
    docs[D]['links']['source'] += [str(tmp_path / 'src-other/unowned.py')]
    result = check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}])
    assert result['by_code'] == {'design.source_unmapped': 1}


def test_tests_are_excluded_and_cross_module_source_requires_design_protocol(tmp_path):
    """Design 引用的测试文件不参与检查，跨模块源码需要协议声明"""
    repo, mapping, docs = fixture(tmp_path)
    (tmp_path / 'other').mkdir()
    (tmp_path / 'other/api.py').write_text('')
    mapping['modules'].append({'id': 'other', 'code_paths': ['other'], 'designs': [], 'instruction_owners': []})
    docs[D]['links']['source'] += ['tests/untracked.py', 'other/api.py']
    result = check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}])
    assert result['by_code'] == {'design.source_without_protocol': 1}
    mapping['protocols'] = [{'id': 'cross', 'modules': ['one', 'other'], 'designs': [D],
                             'description': 'call other', 'example': 'one calls api'}]
    assert check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}])['findings'] == []


def test_safe_error_code_keeps_diagnostic_without_payload():
    """错误码提取只保留 code，不带错误消息内容"""
    assert check.error_code(RuntimeError('{"code":"instruction.forbidden","message":"private"}')) == 'instruction.forbidden'


def test_duplicate_owners_designs_missing_paths_and_inaccessible_resources(tmp_path):
    """Design 重复归属、缺演进文件、指令归属不符、路径缺失和资源不可读各自报出并判审计失败"""
    repo, mapping, docs = fixture(tmp_path)
    mapping['modules'].append({'id': 'two', 'designs': [D], 'code_paths': ['absent*'],
                               'instruction_owners': ['lineage']})
    result = check.audit(repo, mapping, [D], [], None, {}, [{'owner': 'lineage'}, {'owner': 'web'}],
                         {D: 'InstructionFailed'})
    assert result['audit_failed']
    assert result['by_code'] == {'design.ownership': 1, 'evolution.missing': 1,
        'instruction.owner_mapping': 2, 'module.path_missing': 1, 'resource.read_failed': 1}


def test_plan_missing_index_resource_dependency_and_cycle(tmp_path):
    """演进计划的未索引、不可读、依赖缺失、未知模块和循环依赖各报一项"""
    repo, mapping, docs = fixture(tmp_path)
    evolution = {'plans': [{'id': P, 'modules': ['one'], 'depends_on': [Q]},
                           {'id': Q, 'modules': ['unknown'], 'depends_on': [P, 'plan_333333333333']}]}
    docs[P] = {}
    result = check.audit(repo, mapping, [D], [P], evolution, docs, [{'owner': 'lineage'}])
    assert result['by_code'] == {'plan.cycle': 1, 'plan.dependency_missing': 1,
                                'plan.not_indexed': 1, 'plan.unknown_module': 1, 'plan.unreadable': 1}


@pytest.mark.parametrize('subdirectory', ['', 'code'])
def test_commit_reads_do_not_follow_working_tree(tmp_path, subdirectory):
    """架构审计按指定提交读取文件，不受工作区并发修改影响"""
    import subprocess
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()
    git('init', '-q')
    root = tmp_path / subdirectory
    root.mkdir(exist_ok=True)
    (root / 'source.txt').write_text('published')
    if subdirectory:
        (tmp_path / 'source.txt').write_text('wrong repository-level file')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
    repo = check.Repository(root, git('rev-parse', 'HEAD'))
    (root / 'source.txt').write_text('concurrent editing')
    (root / 'extra.txt').write_text('not published')
    assert repo.read('source.txt') == 'published'
    assert repo.exists('source*') and not repo.exists('extra.txt')


def test_missing_plan_index_does_not_abort_other_architecture_checks(tmp_path):
    repo, mapping, docs = fixture(tmp_path)
    (tmp_path / 'architecture').mkdir()
    (tmp_path / 'architecture/modules.yaml').write_text(json.dumps(mapping))
    (tmp_path / 'architecture/evolution.yaml').write_text(json.dumps({'plans': [
        {'id': P, 'modules': ['one'], 'depends_on': []}]}))
    (tmp_path / 'SPEC.md').write_text(f'- [Design](/r/{D})\n')

    def call(name, args):
        if name == 'ListInstructions':
            return {'instructions': [{'owner': 'lineage'}]}
        address = args['address']
        return {'version': '1', 'text': '---\n' + json.dumps({
            'kb_id': address, **docs.get(address, {})}) + '\n---\n'}

    report = check.run(repo, call)
    assert report['audit_failed']
    assert report['by_code'] == {'input.missing': 1}
    assert report['findings'] == [{'code': 'input.missing', 'subject': 'IMPROVE.md'}]
    assert report['counts']['designs'] == 1
    assert report['counts']['plans'] == 1
    assert 'IMPROVE.md' not in report['sources']['files']


def test_failed_resource_read_is_reported_not_silently_omitted(tmp_path):
    """资源读取失败记为 resource.read_failed 并判审计失败，报告不含错误详情"""
    repo, mapping, docs = fixture(tmp_path)
    (tmp_path / 'architecture').mkdir()
    (tmp_path / 'architecture/modules.yaml').write_text(json.dumps(mapping))
    (tmp_path / 'IMPROVE.md').write_text('')
    (tmp_path / 'SPEC.md').write_text(f'| Design | ID |\n| --- | --- |\n| One | `{D}` |\n')
    def call(name, args):
        if name == 'ListInstructions':
            return {'instructions': [{'owner': 'lineage'}]}
        raise PermissionError('private detail must not be logged')
    report = check.run(repo, call)
    assert report['audit_failed']
    assert 'private detail' not in json.dumps(report)
    assert {'code': 'resource.read_failed', 'subject': D, 'error': 'PermissionError'} in report['findings']


def test_report_uses_content_version_when_resource_index_is_stale(tmp_path):
    """资源索引版本滞后时，审计报告采用文档内容中的版本号"""
    repo, mapping, docs = fixture(tmp_path)
    (tmp_path / 'architecture').mkdir()
    (tmp_path / 'architecture/modules.yaml').write_text(json.dumps(mapping))
    (tmp_path / 'architecture/evolution.yaml').write_text('plans: []')
    (tmp_path / 'IMPROVE.md').write_text('')
    (tmp_path / 'SPEC.md').write_text(f'| Design | ID |\n| --- | --- |\n| One | `{D}` |\n')
    document = {'kb_id': D, 'version': {'number': 2}, **docs[D]}
    def call(name, args):
        if name == 'ListInstructions':
            return {'instructions': [{'owner': 'lineage'}]}
        return {'version': '1', 'text': '---\n' + json.dumps(document) + '\n---\n# Design'}
    report = check.run(repo, call)
    assert not report['audit_failed'] and not report['findings']
    assert report['sources']['resources'][D]['version'] == '2'
    assert report['sources']['resources'][D]['response_version'] == '1'


def test_nonimplementation_sources_are_listed_separately(tmp_path):
    """外部路径、仓库文档、URL 和资源地址等非实现来源单独列入外部引用清单"""
    repo, mapping, docs = fixture(tmp_path)
    external = tmp_path.parent / (tmp_path.name + '-external.md')
    external.write_text('not code')
    (tmp_path / 'guide.md').write_text('documentation')
    address = 'repo_111111111111/file/src/owned.py'
    docs[D]['links']['source'] += [str(external), 'guide.md', 'https://example.invalid/reference#part', address]
    resolved = []
    def resolve(value):
        resolved.append(value)
        return {'target': {'exists': True}, 'operations': ['read']}
    report = check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}],
                         resolve_resource=resolve)
    assert report['findings'] == [] and not report['audit_failed']
    assert resolved == [address]
    refs = report['external_references']
    assert len(refs) == 4
    assert {r['kind'] for r in refs} == {'external_path', 'repository_document', 'url', 'resource'}
    assert next(r for r in refs if r['kind'] == 'url')['status'] == 'valid_format'
    assert any(ref['reference'] == address for ref in refs)


def test_invalid_references_do_not_become_protocol_deviations(tmp_path):
    """无效源码引用报为 design.source_invalid_reference，不算协议偏差也不判审计失败"""
    repo, mapping, docs = fixture(tmp_path)
    docs[D]['links']['source'] = [str(tmp_path.parent / 'absent' / 'rules.md'),
                                'missing.md', 'https://', 'https://host.invalid:bad',
                                'resident_111111111111']
    def resolve(address):
        raise PermissionError('private response must not appear')
    report = check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}],
                         resolve_resource=resolve)
    assert report['by_code'] == {'design.source_invalid_reference': 5}
    assert not report['audit_failed']  # Checking completed and found broken references.
    assert 'private response' not in json.dumps(report)


def test_unreadable_external_path_is_a_reference_finding(tmp_path, monkeypatch):
    """无权读取的仓库外路径报为无效引用，只记录错误类型"""
    repo, mapping, docs = fixture(tmp_path)
    external = tmp_path.parent / (tmp_path.name + '-denied.py')
    external.write_text('implementation outside the repository')
    docs[D]['links']['source'] = [str(external)]
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path == external:
            raise PermissionError('private detail')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    result = check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}])
    assert result['by_code'] == {'design.source_invalid_reference': 1}
    assert result['external_references'][0]['error'] == 'PermissionError'


def test_resource_transport_failure_keeps_audit_incomplete(tmp_path):
    """资源解析超时使审计判为未完成，引用标为 check_failed 且不泄露连接细节"""
    repo, mapping, docs = fixture(tmp_path)
    docs[D]['links']['source'] = ['decision_222222222222/file/nested.md']
    def resolve(address):
        raise TimeoutError('private connection details')
    result = check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}],
                         resolve_resource=resolve)
    assert result['audit_failed']
    assert result['external_references'][0]['status'] == 'check_failed'
    assert 'private connection' not in json.dumps(result)


def test_resource_references_resolve_through_run_callers_isa(tmp_path):
    """资源引用经运行者的 ISA 解析，相同地址只调用一次 ResolveResource"""
    repo, mapping, docs = fixture(tmp_path)
    address = 'resident_111111111111/file/resident.md'
    docs[D]['links']['source'] = [address, address]
    (tmp_path / 'architecture').mkdir()
    (tmp_path / 'architecture/modules.yaml').write_text(json.dumps(mapping))
    (tmp_path / 'architecture/evolution.yaml').write_text('plans: []')
    (tmp_path / 'IMPROVE.md').write_text('')
    (tmp_path / 'SPEC.md').write_text(f'| Design | ID |\n| --- | --- |\n| One | `{D}` |\n')
    calls = []
    def call(name, args):
        calls.append((name, args))
        if name == 'ListInstructions':
            return {'instructions': [{'owner': 'lineage'}]}
        if name == 'ResolveResource':
            assert args == {'address': address}
            return {'target': {'exists': True}, 'operations': ['read']}
        assert name == 'ReadResource' and args == {'address': D}
        return {'version': '1', 'text': '---\n' + json.dumps({'kb_id': D, **docs[D]}) + '\n---\n'}
    report = check.run(repo, call)
    assert not report['audit_failed'] and not report['findings']
    assert sum(name == 'ResolveResource' for name, _ in calls) == 1
    assert report['counts']['external_references'] == 2


@pytest.mark.parametrize('subdirectory', ['', 'code'])
def test_repository_documents_follow_publication_snapshot(tmp_path, subdirectory):
    """仓库文档引用按发布快照判断可读，工作区删除文件不影响结果"""
    import subprocess
    root = tmp_path / subdirectory
    root.mkdir(exist_ok=True)
    repo, mapping, docs = fixture(root)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()
    (root / 'guide.md').write_text('published guide')
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
    repo = check.Repository(root, git('rev-parse', 'HEAD'))
    (root / 'guide.md').unlink()
    docs[D]['links']['source'] = ['guide.md']
    report = check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}])
    assert not report['findings']
    assert report['external_references'][0]['status'] == 'readable'


def test_core_module_importing_a_feature_at_the_top_level_is_a_finding(tmp_path):
    """核心模块在顶层导入功能模块记为 feature.core_import；函数内按需导入和功能之间的导入不算

    :module: spec.design-governance
    :design: decision_15aab18c0ab3
    """
    import subprocess
    repo, mapping, docs = fixture(tmp_path)
    package = tmp_path / 'management_portal'
    (package / 'sub').mkdir(parents=True)
    (package / 'portal_features.py').write_text(
        (check.ROOT / check.FEATURE_DECLARATIONS).read_text().split('\n_MP = ', 1)[0]
        + '\nFEATURES = (PortalFeature("paper", "Paper", modules=("management_portal.paper_*",)),)\n')
    (package / 'paper_backend.py').write_text('from management_portal import shell\n')
    (package / 'shell.py').write_text(
        'from management_portal import paper_backend\n'
        'def lazy():\n    from management_portal import paper_store\n')
    (package / 'sub' / 'view.py').write_text('class View:\n    from ..paper_backend import Store\n')
    subprocess.run(['git', '-C', str(tmp_path), 'init', '-q'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    assert check.feature_imports(repo) == [
        ('management_portal/shell.py', 1, 'paper', 'management_portal.paper_backend'),
        ('management_portal/sub/view.py', 2, 'paper', 'management_portal.paper_backend'),
    ]
    report = check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}])
    assert report['by_code']['feature.core_import'] == 2


def test_module_map_lists_each_feature_with_the_modules_holding_its_code(tmp_path):
    """modules.yaml 的 features 与功能声明一一对应，功能的模块文件都在所列模块的 code_paths 中

    :module: spec.design-governance
    :design: decision_15aab18c0ab3
    """
    import subprocess
    repo, mapping, docs = fixture(tmp_path)
    package = tmp_path / 'management_portal'
    package.mkdir()
    (package / 'portal_features.py').write_text(
        (check.ROOT / check.FEATURE_DECLARATIONS).read_text()
        + '\nFEATURES = (PortalFeature("paper", "Paper", modules=("management_portal.paper_*",)),'
          ' PortalFeature("wall", "WALL", modules=("management_portal.wall_*",)))\n')
    for name in ('paper_backend.py', 'paper_store.py', 'wall_backend.py'):
        (package / name).write_text('')
    subprocess.run(['git', '-C', str(tmp_path), 'init', '-q'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    mapping['modules'].append({'id': 'workflows', 'code_paths': ['management_portal/paper_backend.py',
                                                                 'management_portal/wall_*.py'], 'designs': []})
    mapping['features'] = [{'id': 'paper', 'modules': ['workflows', 'missing']},
                           {'id': 'pulse', 'modules': ['workflows']}]
    findings = [f for f in check.audit(repo, mapping, [D], [], {'plans': []}, docs, [{'owner': 'lineage'}])['findings']
                if f['code'].startswith('feature.')]
    assert sorted((f['code'], f['subject'], f.get('module') or f.get('path')) for f in findings) == [
        ('feature.code_outside_modules', 'paper', 'management_portal/paper_store.py'),
        ('feature.unknown', 'pulse', None),
        ('feature.unknown_module', 'paper', 'missing'),
        ('feature.unmapped', 'wall', None),
    ]
    mapping['features'] = [{'id': 'paper', 'modules': ['workflows', 'one']}, {'id': 'wall', 'modules': ['workflows']}]
    (package / 'paper_store.py').unlink()
    subprocess.run(['git', '-C', str(tmp_path), 'add', '-A'], check=True)
    assert check.feature_mapping(repo, mapping, {m['id']: m for m in mapping['modules']}) == []
