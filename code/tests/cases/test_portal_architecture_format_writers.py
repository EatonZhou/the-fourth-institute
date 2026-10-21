"""TFI writers preserve exact legacy reads and the audit's evidence semantics."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import check_portal_architecture as check


@pytest.fixture(params=['tfi', 'tfi'])
def input_repo(tmp_path, request):
    (tmp_path / 'architecture').mkdir()
    inputs = {
        'SPEC.md': '# Empty design index\n',
        'IMPROVE.md': '# Empty plan index\n',
        'architecture/modules.yaml': (
            f'schema: {request.param}.portal-architecture.v1\n'
            'model_version: 2\nmodules: []\nprotocols: []\n'),
        'architecture/evolution.yaml': (
            f'schema: {request.param}.portal-evolution.v2\nplans: []\n'),
    }
    for name, text in inputs.items():
        (tmp_path / name).write_bytes(text.encode())
    return check.Repository(tmp_path), inputs


def empty_catalog(name, arguments):
    assert name == 'ListInstructions'
    return {'instructions': []}


def test_new_reports_use_tfi_without_rewriting_inputs_or_hashes(input_repo):
    repo, inputs = input_repo
    report = check.run(repo, empty_catalog)
    assert report['schema'] == 'tfi.portal-architecture-audit.v1'
    assert not report['audit_failed'] and report['findings'] == []
    assert report['sources']['files'] == {
        name: hashlib.sha256(text.encode()).hexdigest() for name, text in inputs.items()}
    for name, text in inputs.items():
        assert (repo.root / name).read_bytes() == text.encode()


def test_cli_success_artifacts_use_tfi(input_repo, tmp_path, monkeypatch, capsys):
    repo, _ = input_repo
    for name in list(os.environ):
        if name.startswith('PORTAL_SCRIPT_'):
            monkeypatch.delenv(name)
    monkeypatch.setattr(check, 'isa_client', lambda resident_id: empty_catalog)
    destination = tmp_path / 'output'
    assert check.main(['--repo-root', str(repo.root), '--output-dir', str(destination)]) == 0
    report = json.loads((destination / 'architecture-audit.json').read_text())
    routing = json.loads(capsys.readouterr().out)
    assert report['schema'] == 'tfi.portal-architecture-audit.v1'
    assert not routing['audit_failed'] and not routing['has_deviations']
    assert routing['report'] == 'architecture-audit.json'
    assert not (destination / 'architecture-audit.md').exists()


def test_cli_default_artifacts_do_not_land_in_repository_root(input_repo, monkeypatch):
    repo, _ = input_repo
    for name in list(os.environ):
        if name.startswith('PORTAL_SCRIPT_'):
            monkeypatch.delenv(name)
    monkeypatch.setattr(check, 'isa_client', lambda resident_id: empty_catalog)
    monkeypatch.chdir(repo.root)
    assert check.main(['--repo-root', str(repo.root)]) == 0
    assert (repo.root / 'test-results/architecture-audit/architecture-audit.json').is_file()
    assert not (repo.root / 'architecture-audit.json').exists()
    assert not (repo.root / 'architecture-audit.md').exists()


def test_direct_cli_failure_artifact_uses_tfi(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('PORTAL_SCRIPT_') and key != 'PORTAL_ISA_SOCKET'}
    result = subprocess.run([
        sys.executable, '-I', str(Path(check.__file__).resolve()),
        '--repo-root', str(tmp_path), '--resident-id', '', '--output-dir', str(tmp_path),
    ], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2, result.stderr
    report = json.loads((tmp_path / 'architecture-audit.json').read_text())
    assert report['schema'] == 'tfi.portal-architecture-audit.v1'
    assert report['audit_failed'] and report['by_code'] == {'audit.failed': 1}
    assert json.loads(result.stdout)['audit_failed']
