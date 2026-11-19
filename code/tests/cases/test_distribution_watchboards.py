"""Installed boards retain their portable definition, not source runtime data."""
import json
from pathlib import Path
import pytest
from management_portal.distribution.selection import resolve
from management_portal.distribution.file_scope import FileScope
from management_portal.watchboards.distribution import dependencies
from tests.cases.test_distribution_lineage_options import ExportSource, KNOWLEDGE, RESIDENT, selected


def board(root, agent=None):
    root.mkdir(parents=True)
    (root / 'agent.yaml').write_text(json.dumps(agent or {'commands': {}, 'configs': []}))
    (root / 'watchboard.yaml').write_text('layout: telepus-channels\n')
    (root / 'state').mkdir()
    (root / 'state/private.json').write_text('source contacts must stay here')


@pytest.mark.parametrize('outputs', [False, True])
def test_installed_board_exports_without_source_runtime(tmp_path, outputs):
    root = tmp_path / 'watchboard'
    board(root)
    source = ExportSource()
    source.data['workspace', 'workspaces'][0]['path'] = str(tmp_path)
    source.definition['lineage', 'lineage_components'] = [
        {'client_key': f'watchboard.{RESIDENT}.view', 'node_id': 'event_source_aaaaaaaaaaaa'}]
    snapshot = selected('decision')
    snapshot['lineages'] = [{'id': KNOWLEDGE, 'history': False,
                            'workspace': {'own_outputs': outputs, 'extra_bindings': 'exclude'}}]
    result = resolve(snapshot, source)
    assert not result['unresolved']
    assert any(p.endswith('/watchboard/agent.yaml') for p in result['generated_files'])
    assert any('telepus-channels' in s for s in result['generated_files'].values())
    scope = FileScope(result['files'], result['file_scope_policy'])
    exported = [str(p) for item in result['files'] for _, p, directory in scope.walk(item['source']) if not directory]
    assert not any('private.json' in p for p in exported)
    assert (root / 'state/private.json').exists()


def test_malformed_installed_manifest_blocks_export(tmp_path):
    board(tmp_path / 'watchboard')
    (tmp_path / 'watchboard/agent.yaml').write_text('commands: [')
    source = ExportSource()
    source.data['workspace', 'workspaces'][0]['path'] = str(tmp_path)
    source.definition['lineage', 'lineage_components'] = [
        {'client_key': f'watchboard.{RESIDENT}.view'}]
    result = resolve(selected('decision'), source)
    assert any(row.get('id') == RESIDENT and 'Invalid Watchboard agent.yaml' in row['reason']
               for row in result['unresolved'])


def test_portable_code_survives_while_runtime_alias_is_excluded(tmp_path):
    root = tmp_path / 'watchboard'
    board(root, {'commands': {'run': {}}, 'entrypoint': 'src/main.py', 'configs': [],
                 'distribution': {'files': ['src/main.py']}})
    (root / 'src').mkdir()
    (root / 'src/main.py').write_text('print("ok")')
    (tmp_path / 'alias').symlink_to(root / 'state', target_is_directory=True)
    _, files = dependencies(root)
    scope = FileScope([{'source': str(tmp_path)}], {'watchboards': [
        {'root': str(root), 'files': [str(p) for p in files.values()]}]})
    exported = [str(p) for _, p, directory in scope.walk(tmp_path) if not directory]
    assert exported == [str(root / 'src/main.py')]


def test_missing_manifest_blocks_dependency_resolution(tmp_path):
    with pytest.raises(ValueError, match='requires agent.yaml'):
        dependencies(tmp_path)


def test_commands_require_explicit_portable_files(tmp_path):
    board(tmp_path / 'watchboard', {'commands': {'run': {}}, 'entrypoint': 'src/main.py', 'configs': []})
    with pytest.raises(ValueError, match='distribution.files'):
        dependencies(tmp_path / 'watchboard')
    root = tmp_path / 'watchboard'
    (root / 'src').mkdir()
    (root / 'src/main.py').write_text('print("ok")')
    agent = {'commands': {'run': {}}, 'entrypoint': 'src/main.py', 'configs': [],
             'distribution': {'files': ['src/main.py']}}
    (root / 'agent.yaml').write_text(json.dumps(agent))
    _, files = dependencies(root)
    assert list(files) == ['src/main.py']
    agent['distribution']['files'].append('state/private.json')
    (root / 'agent.yaml').write_text(json.dumps(agent))
    with pytest.raises(ValueError, match='Non-portable'):
        dependencies(root)


def test_dependency_symlinks_rejected(tmp_path):
    root = tmp_path / 'watchboard'
    board(root, {'commands': {}, 'distribution': {'files': ['linked.py']}})
    (tmp_path / 'private.py').write_text('secret')
    (root / 'linked.py').symlink_to(tmp_path / 'private.py')
    with pytest.raises(ValueError, match='local regular file'):
        dependencies(root)


def test_example_seed_keeps_telepus_view_without_source_bindings():
    seed = Path(__file__).resolve().parents[3] / 'instance/example_instance/seed'
    root = seed / 'data/projects/AI员工/agents/lineage_c625a4bee56b/residents/resident_7fdebb465cdf/workspace/watchboard'
    manifests, files = dependencies(root)
    assert json.loads(manifests['watchboard.yaml'])['layout'] == 'telepus-channels'
    assert json.loads(manifests['agent.yaml'])['commands'] == {}
    assert not files
