"""Projection dependencies are declared files, not a Workspace traversal."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from management_portal.distribution.projection_dependencies import declarations, resolve_files, SCHEMA
from management_portal.distribution.engineering import generate

REPO = 'repo_111111111111'
WORKSPACE = 'workspace_222222222222'
APP = 'workbench_333333333333'


class Source:
    def __init__(self, root):
        self.root = root
        self.checkout = root / 'code'
        self.checkout.mkdir()
        self.directory = self.checkout / 'app'
        self.directory.mkdir()
        self.workspace = root / 'workspace'
        self.workspace.mkdir()
        self.file = self.workspace / 'evidence.json'
        self.file.write_text('{"fact":"portable"}')
        (self.workspace / 'unselected.bin').write_bytes(b'not selected')
        self.application = {'repo_id': REPO, 'directory': 'app', 'commit': 'a' * 40}
        self.declaration = {'schema': SCHEMA, 'code_repository': REPO, 'files': [
            {'address': WORKSPACE + '/file/evidence.json', 'sha256': hashlib.sha256(self.file.read_bytes()).hexdigest(), 'size': self.file.stat().st_size}]}
        self.save()

    def save(self):
        (self.directory / 'release-dependencies.json').write_text(json.dumps(self.declaration))

    def read_index(self, **_):
        return {REPO: {'resource_type': 'repo', 'attributes': {'canonical_checkout': str(self.checkout)}}}

    def rows(self, schema, table, **_):
        if (schema, table) == ('workbenches', 'application_packages'):
            return [{'app_id': APP, 'revision': 2, 'application': json.dumps(self.application)}]
        if (schema, table) == ('workspace', 'workspaces'):
            return [{'workspace_id': WORKSPACE, 'path': str(self.workspace)}]
        return []


def test_exact_declared_scope_binds_repo_to_code_without_copying_workspace(tmp_path):
    source = Source(tmp_path)
    frozen = declarations(source, [{'id': APP, 'revision': 2}])
    scope = resolve_files(source, frozen)
    assert scope['repositories'] == {REPO: str(source.checkout)}
    assert (str(source.checkout), '${code_root}') in scope['path_mappings']
    assert [item['source'] for item in scope['files']] == [str(source.file)]
    assert scope['workspaces'][WORKSPACE]['path'] == '${share_root}/ProjectionEvidence/' + WORKSPACE
    assert not declarations(source, [{'id': APP, 'revision': 1}])
    source.file.write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        resolve_files(source, frozen)


def test_declaration_change_and_link_escape_are_not_silently_accepted(tmp_path):
    source = Source(tmp_path)
    frozen = declarations(source, [{'id': APP, 'revision': 2}])
    source.declaration['files'] = []
    source.save()
    assert frozen != declarations(source, [{'id': APP, 'revision': 2}])
    source.file.unlink()
    source.file.symlink_to(source.workspace / 'unselected.bin')
    with pytest.raises(ValueError, match='links'):
        resolve_files(source, frozen)
    invalid = deepcopy(frozen)
    invalid[0]['files'][0]['address'] = WORKSPACE + '/file/../outside'
    with pytest.raises(ValueError, match='relative file'):
        resolve_files(source, invalid)


def test_live_json_root_freezes_current_reports_only_when_packaging(tmp_path):
    source = Source(tmp_path)
    source.declaration['files'] = []
    source.declaration['json_roots'] = [WORKSPACE + '/file/index.json']
    source.save()
    index = source.workspace / 'index.json'
    index.write_text(json.dumps({'reports': [WORKSPACE + '/file/evidence.json']}))
    first = declarations(source, [{'id': APP, 'revision': 2}])
    assert len(resolve_files(source, first)['files']) == 2
    (source.workspace / 'new.json').write_text('{"schema":"tfi.portal-stage-report.v1"}')
    index.write_text(json.dumps({'reports': [WORKSPACE + '/file/evidence.json', WORKSPACE + '/file/new.json']}))
    second = declarations(source, [{'id': APP, 'revision': 2}])
    assert first != second
    assert len(resolve_files(source, second)['files']) == 3
    assert all(not x['source'].endswith('unselected.bin') for x in resolve_files(source, second)['files'])
    with pytest.raises(ValueError, match='changed'):
        resolve_files(source, first)


def test_live_json_root_rejects_linked_dependencies(tmp_path):
    source = Source(tmp_path)
    source.declaration['files'] = []
    source.declaration['json_roots'] = [WORKSPACE + '/file/index.json']
    source.save()
    (source.workspace / 'index.json').write_text(json.dumps({'file': WORKSPACE + '/file/link.json'}))
    (source.workspace / 'link.json').symlink_to(source.file)
    with pytest.raises(ValueError, match='links'):
        declarations(source, [{'id': APP, 'revision': 2}])


@pytest.mark.parametrize('roots', [None, [None], [{}], ['bad'], [WORKSPACE + '/file/index.json'] * 2])
def test_live_json_roots_require_unique_exact_addresses(tmp_path, roots):
    source = Source(tmp_path)
    source.declaration['json_roots'] = roots
    source.save()
    with pytest.raises(ValueError, match='JSON root'):
        declarations(source, [{'id': APP, 'revision': 2}])


def test_engineering_rejects_wrong_source_before_writing_destination(tmp_path):
    source = Source(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    destination = tmp_path / 'output'
    with pytest.raises(ValueError, match='differs from the selected release source'):
        generate(source_root=other, destination=destination, snapshot={},
                 resolved={'projection_code_repositories': {REPO: str(source.checkout)}}, database_sql='')
    assert not destination.exists()


@pytest.mark.parametrize('projection', [False, True])
@pytest.mark.parametrize('json_text', [False, True])
def test_exported_repo_registration_has_only_its_materialized_source_root(tmp_path, projection, json_text):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from management_portal.resource_auto_registration import ResourceAutoRegistrar
    from management_portal.resources import ResourceService
    from management_portal.resources.providers import install_mutation_providers
    from management_portal.resources.providers.repo import install_resolver
    from management_portal.resources.registry_store import get_registry_store
    from management_portal.distribution.selection import resolve, SYSTEM
    from scripts.tfi_release_install import map_directories
    from tests.cases.registry_support import write_projects
    from tests.cases.test_distribution_lineage_options import ExportSource
    from tests.cases.test_distribution_resource_dependencies import selected

    assets = Source(tmp_path)
    source = ExportSource()
    document = {'schema': 'tfi.repo.v1', 'id': REPO, 'type': 'repo', 'name': 'Example',
                'canonical_checkout': str(assets.checkout), 'source_kind': 'git_checkout',
                'checkouts': [{'path': str(assets.checkout), 'git_common_dir': '/source/.git', 'head': 'a' * 40},
                              {'path': '/other/checkout', 'head': 'b' * 40}],
                'remotes': [{'name': 'origin', 'url': '/source/remote'}],
                'push_enabled': True, 'ssh_key': '/source/private-key'}
    source.data['resource_registry', 'registry_documents'] = [
        {'collection': 'repo', 'document_id': REPO,
         'document_json': json.dumps(document) if json_text else deepcopy(document)}]
    source.tables['resource_registry', 'registry_documents'] = SimpleNamespace(columns={})
    source.read_index = lambda **_: {SYSTEM: {'resource_type': 'lineage'},
        APP: {'resource_type': 'workbench'},
        WORKSPACE: {'resource_type': 'workspace'},
        **assets.read_index()}
    source.data['lineage', 'application_targets'].append(
        {'application': 'resource_chat:repo', 'lineage_id': SYSTEM, 'revision': 1})
    source.data['lineage', 'lineages'].append({'lineage_id': SYSTEM, 'project_id': '', 'deleted_at': None})
    snapshot = selected('repo')
    snapshot['resource_types'][0]['instances'] = [REPO]
    if projection:
        snapshot['projections'] = [{'id': APP}]
        source.data['workbenches', 'apps'] = [{'id': APP, 'published': 2, 'revision': 2}]
        source.data['workbenches', 'bindings'] = [{'app_id': APP, 'revision': 2, 'alias': 'main', 'lineage_id': SYSTEM}]
        source.data['workbenches', 'versions'] = [{'app_id': APP, 'revision': 2}]
        source.data['workbenches', 'application_packages'] = assets.rows('workbenches', 'application_packages')
        source.data['workspace', 'workspaces'] = assets.rows('workspace', 'workspaces')
        for table in ('apps', 'bindings', 'versions'):
            source.tables['workbenches', table] = SimpleNamespace(columns={'revision': object()})
    before = deepcopy(source.data)
    result = resolve(snapshot, source)
    assert not result['unresolved']
    row, = result['tables']['resource_registry', 'registry_documents']
    exported = row['document_json']
    target = tmp_path / 'target'
    installed = json.loads(map_directories(exported if isinstance(exported, str) else json.dumps(exported),
        {'${code_root}': str(target / 'code'), '${share_root}': str(target / 'share')}))
    root = str(target / ('code' if projection else 'share/Codes/' + REPO))
    assert installed['source_kind'] == 'exported_tree'
    assert installed['schema'] == 'tfi.repo.v1' and installed['id'] == REPO
    assert installed['canonical_checkout'] == root
    assert installed['checkouts'] == [{'path': root}]
    assert installed['remotes'] == []
    assert installed['push_enabled'] is False and not installed.get('ssh_key')
    assert source.data == before
    # Exercise the consumer on the actual exported record, not a handcrafted
    # exported_tree fixture: reconciliation must keep its file address alive.
    content = target / ('code' if projection else 'share/Codes/' + REPO) / 'architecture/modules.yaml'
    content.parent.mkdir(parents=True)
    content.write_text('modules: []\n')
    write_projects({'projects': []})
    with get_registry_store().writer('repo') as writer:
        writer.put('repo', REPO, installed)
    index = Mock()
    index.change_targets.return_value = ['resource_registry']
    index.queue_change.return_value = ['resource_registry']
    service = ResourceService(index_provider=index, resolver_loader=install_resolver,
                              mutation_provider_loader=install_mutation_providers)
    for _ in range(2):
        reconciled = ResourceAutoRegistrar(service=service).reconcile()
        assert not reconciled['removed'] and not reconciled['resource_errors']
        assert get_registry_store().get('repo', REPO) == installed
        body, _mime, _resolved = service.read_content(REPO + '/file/architecture/modules.yaml',
                                                     actor='resident_0123456789ab')
        assert body == b'modules: []\n'


def stage_source(source):
    """A populated source reporter; the release must never copy its history."""
    prefix = WORKSPACE + '/file/reports/'
    source.declaration['files'] = []
    source.declaration['stage_progress'] = {'index': prefix + 'stage-index.json',
        'content': prefix + 'stage-content.json', 'schema': prefix + 'stage-report-schema.json',
        'new_report_directory': 'digests/'}
    directory = source.workspace / 'reports'
    directory.mkdir()
    contract = {'x-contract-revision': 4, '$defs': {}}
    for name, collection, extra in [('stageIndex', 'report_refs', ['coverage_start_at', 'latest_report_ref']),
                                    ('stageContent', 'report_sources', [])]:
        properties = {key: {'type': 'string', 'minLength': 1} for key in ['generated_at', *extra]}
        contract['$defs'][name] = {'type': 'object', 'required': [collection, *properties],
                                 'properties': {collection: {'type': 'array'}, **properties}}
    (directory / 'stage-report-schema.json').write_text(json.dumps(contract))
    (directory / 'stage-index.json').write_text(json.dumps({'report_refs': [prefix + 'old.json']}))
    (directory / 'stage-content.json').write_text('{"old":"source achievements"}')
    (directory / 'old.json').write_text('source achievements')
    (source.workspace / 'digests').mkdir()
    (source.workspace / 'digests/old.json').write_text('source achievements')
    source.save()


def test_stage_freeze_ignores_source_history_growth_but_checks_contract(tmp_path):
    source = Source(tmp_path)
    stage_source(source)
    frozen = declarations(source, [{'id': APP, 'revision': 2}])
    (source.workspace / 'reports/stage-index.json').write_text('{"new": "source achievements"}')
    assert declarations(source, [{'id': APP, 'revision': 2}]) == frozen
    scope = resolve_files(source, frozen)
    assert scope['files'] == []
    assert scope['reset_roots'] == [str(source.workspace)]
    assert len(scope['generated_files']) == 3
    assert 'source achievements' not in json.dumps(scope['generated_files'])
    from jsonschema import Draft202012Validator
    documents = {key.rsplit('/', 1)[1]: json.loads(value) for key, value in scope['generated_files'].items()}
    schema = documents['stage-report-schema.json']
    for name, key in [('stageIndex', 'stage-index.json'), ('stageContent', 'stage-content.json')]:
        Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/' + name}).validate(documents[key])
    index = documents['stage-index.json']
    assert not index.get('latest_report_ref') and not index.get('coverage_start_at')
    index['report_refs'] = [WORKSPACE + '/file/digests/local.json']
    validator = Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/stageIndex'})
    assert not validator.is_valid(index)  # First real report requires real dates/latest identity.
    index.update(generated_at='2026-10-20T00:00:00Z', coverage_start_at='2026-10-20T00:00:00Z',
                 latest_report_ref=index['report_refs'][0])
    validator.validate(index)
    # Releasing from a previously installed distribution stays a fresh start,
    # without nesting another layer of schema adaptation on every generation.
    schema_path = source.workspace / 'reports/stage-report-schema.json'
    schema_path.write_text(json.dumps(schema))
    again = resolve_files(source, declarations(source, [{'id': APP, 'revision': 2}]))
    assert again['generated_files'] == scope['generated_files']
    (source.workspace / 'reports/stage-report-schema.json').write_text('{}')
    with pytest.raises(ValueError, match='schema changed'):
        resolve_files(source, frozen)


def test_stage_reset_cannot_be_combined_with_exported_source_evidence(tmp_path):
    source = Source(tmp_path)
    original = deepcopy(source.declaration['files'])
    stage_source(source)
    source.declaration['files'] = original
    source.save()
    with pytest.raises(ValueError, match='cannot export'):
        declarations(source, [{'id': APP, 'revision': 2}])


@pytest.mark.parametrize('empty_progress', [False, True])
def test_fresh_engineering_preserves_evidence_hash_and_installed_bindings(tmp_path, monkeypatch, empty_progress):
    import subprocess
    from management_portal.distribution import core_skills
    from management_portal.distribution.sanitize import scrub
    from scripts.tfi_release_install import map_directories

    source = Source(tmp_path)
    if empty_progress:
        stage_source(source)
    original_files = {str(path): path.read_bytes() for path in source.workspace.rglob('*') if path.is_file()}
    subprocess.run(['git', 'init', '-q', str(source.checkout)], check=True)
    for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'scripts/install_tfi_release', 'scripts/tfi_release_install.py'):
        path = source.checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# minimal source\n')
    rules = source.checkout / 'core_skills/tfi-agent-rule'
    rules.mkdir(parents=True)
    for name in ('SKILL.md', 'portal-resident.md', 'portal-development.md'):
        (rules / name).write_text('Portable execution rule\n')
    support = source.checkout / 'core_skills/tfi-unified-id'
    (support / 'scripts').mkdir(parents=True)
    (support / 'SKILL.md').write_text('Portable identity rule\n')
    (support / 'scripts/resource_id.py').write_text('print("identity")\n')
    for name in core_skills.REQUIRED_SKILLS:
        (source.checkout / 'core_skills' / name).mkdir()
        (source.checkout / 'core_skills' / name / 'SKILL.md').write_text('Portable required Skill\n')
    monkeypatch.setattr(core_skills, '__file__', str(source.checkout / 'management_portal/distribution/core_skills.py'))
    frozen = declarations(source, [{'id': APP, 'revision': 2}])
    scope = resolve_files(source, frozen)
    if empty_progress:
        scope['file_scope_policy'] = {'reset_roots': scope['reset_roots']}
        scope['files'].append({'source': str(source.workspace), 'target': 'data/share_root/ProjectionEvidence/' + WORKSPACE})
        # Another selected tree and its symlink must not reintroduce reports.
        alias = tmp_path / 'alias'
        alias.mkdir()
        (alias / 'old-reports').symlink_to(source.workspace / 'reports', target_is_directory=True)
        scope['files'].append({'source': str(alias), 'target': 'data/share_root/alias'})
    bindings = scrub({'canonical_checkout': str(source.checkout), 'path': str(source.workspace)}, mappings=scope['path_mappings'])
    output = tmp_path / 'engineering'
    resolved = {**scope, 'projection_code_repositories': scope['repositories'],
                'selected_ids': [REPO, WORKSPACE], 'features': [], 'unresolved': []}
    manifest = generate(source_root=source.checkout, destination=output, snapshot={'source': 'Dev'},
                        resolved=resolved, database_sql=json.dumps(bindings), run_id='local-projection-generation')
    if empty_progress:
        copied = output / 'data/share_root/ProjectionEvidence' / WORKSPACE
        assert sorted(str(p.relative_to(copied)) for p in copied.rglob('*') if p.is_file()) == [
            'reports/stage-content.json', 'reports/stage-index.json', 'reports/stage-report-schema.json']
        assert not (output / 'data/share_root/alias/old-reports').exists()
        assert json.loads((copied / 'reports/stage-index.json').read_text())['report_refs'] == []
        assert all(Path(name).read_bytes() == raw for name, raw in original_files.items())
    else:
        descriptor = frozen[0]['files'][0]
        copied = output / scope['files'][0]['target']
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == descriptor['sha256']
        assert not (copied.parent / 'unselected.bin').exists()
    assert manifest['resolved_objects'] == [REPO, WORKSPACE]
    installed = json.loads(map_directories((output / 'database/distribution.sql').read_text(),
                                          {'${code_root}': '/target/code', '${share_root}': '/target/share'}))
    assert installed == {'canonical_checkout': '/target/code', 'path': '/target/share/ProjectionEvidence/' + WORKSPACE}
