"""Every path interface projects the selected manifest, without old locators."""
import json
from pathlib import Path

import pytest

from management_common import portal_paths
from management_common.instance_manifest import load, instance_runtime_root, instance_secrets_root
from tests.cases.instance_test_support import write_instance


def test_independent_areas_are_not_derived_from_a_shared_parent(tmp_path, monkeypatch):
    root = write_instance(tmp_path / 'instance')
    document = json.loads((root / 'instance.json').read_text())
    document['areas'] = {name: str(tmp_path / disk / name) for name, disk in [('projects', 'disk-one'), ('skills', 'disk-two'), ('papers', 'disk-three')]}
    (root / 'instance.json').write_text(json.dumps(document))
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    monkeypatch.chdir(tmp_path)
    assert portal_paths.path('projects') == tmp_path / 'disk-one/projects'
    assert portal_paths.path('skills') == tmp_path / 'disk-two/skills'
    assert portal_paths.path('papers') == tmp_path / 'disk-three/papers'
    assert instance_runtime_root() == root / 'runtime'
    assert instance_secrets_root() == root / 'secrets'
    assert portal_paths.resolve_directory('skills:one/SKILL.md') == tmp_path / 'disk-two/skills/one/SKILL.md'
    with pytest.raises(KeyError):
        portal_paths.path('not-a-directory')
    with pytest.raises(ValueError, match='absolute'):
        portal_paths.resolve_directory('relative/path')


def test_the_only_directories_are_the_fixed_ones_and_the_areas(tmp_path, monkeypatch):
    root = write_instance(tmp_path / 'instance')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    assert set(portal_paths.paths()) == set(portal_paths.KEYS)
    with pytest.raises(KeyError):
        portal_paths.path('not-a-directory')


def test_fixed_directories_belong_to_the_manifest(tmp_path, monkeypatch):
    root = write_instance(tmp_path / 'instance')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    assert portal_paths.path('secrets') == root / 'secrets'
    assert portal_paths.path('runtime') == root / 'runtime'
    monkeypatch.delenv('PORTAL_INSTANCE_ROOT')
    with pytest.raises(ValueError, match='Select an instance'):
        portal_paths.paths()


def test_shared_worker_files_belong_to_runtime_not_a_web_deployment(tmp_path, monkeypatch):
    from management_portal.execution.worker_manager import ExecutionWorkerManager
    root = write_instance(tmp_path / 'instance')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    monkeypatch.setenv('PORTAL_DEPLOYMENT', 'dev')
    manager = ExecutionWorkerManager()
    assert manager.generation_env == root / 'runtime/deployments/release/generation.env'
    assert manager.health_path == root / 'runtime/worker-health.json'


def test_area_cli_uses_only_selected_manifest_from_an_unrelated_directory(tmp_path):
    import os
    import subprocess
    import sys
    root = write_instance(tmp_path / 'instance')
    code = Path(__file__).resolve().parents[2]
    environment = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    environment['PORTAL_INSTANCE_ROOT'] = str(root)
    command = [sys.executable, '-I', str(code / 'scripts/resolve_instance_path.py'), 'projects:example']
    result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == str(root / 'data/projects/example')
