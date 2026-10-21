"""Audit CLI actor selection without auditing a repository or calling ISA."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import check_portal_architecture as check


@pytest.fixture
def actor_probe(monkeypatch):
    for name in list(os.environ):
        if name.startswith('PORTAL_SCRIPT_') or name in (
                'PORTAL_ISA_SOCKET', 'PORTAL_RESIDENT_ID', 'PORTAL_RESIDENT_ID'):
            monkeypatch.delenv(name)
    selected = []
    audited = []

    def client(resident_id):
        selected.append(resident_id)
        return object()

    def run(repo, call):
        audited.append(True)
        return {'schema': check.AUDIT_SCHEMA, 'commit': '', 'audit_failed': False,
                'counts': {'deviations': 0}, 'by_code': {}, 'findings': []}

    monkeypatch.setattr(check, 'isa_client', client)
    monkeypatch.setattr(check, 'run', run)
    return selected, audited


@pytest.mark.parametrize('environment,explicit,expected', [
    ({}, None, ''),
    ({'ARCHIVE_RESIDENT_ID': 'resident_legacy'}, None, ''),
    ({'PORTAL_RESIDENT_ID': 'resident_canonical'}, None, 'resident_canonical'),
    ({'PORTAL_RESIDENT_ID': 'resident_same', 'ARCHIVE_RESIDENT_ID': 'resident_legacy'},
     None, 'resident_same'),
    ({'PORTAL_RESIDENT_ID': 'resident_same', 'ARCHIVE_RESIDENT_ID': 'resident_legacy'},
     'resident_explicit', 'resident_explicit'),
    ({'PORTAL_RESIDENT_ID': 'resident_default'}, '', ''),
])
def test_default_and_explicit_actor(environment, explicit, expected, actor_probe,
                                    monkeypatch, tmp_path, capsys):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    argv = ['--output-dir', str(tmp_path / 'output')]
    if explicit is not None:
        argv += ['--resident-id', explicit]
    assert check.main(argv) == 0
    assert actor_probe == ([expected], [True])
    capsys.readouterr()


@pytest.mark.parametrize('explicit', [None, 'resident_explicit'])
def test_canonical_identity_is_selected_without_alias_support(explicit, actor_probe,
                                                     monkeypatch, tmp_path):
    monkeypatch.setenv('PORTAL_RESIDENT_ID', 'resident_canonical')
    monkeypatch.setenv('ARCHIVE_RESIDENT_ID', 'resident_legacy')
    destination = tmp_path / 'output'
    argv = ['--output-dir', str(destination)]
    if explicit is not None:
        argv += ['--resident-id', explicit]
    assert check.main(argv) == 0
    assert actor_probe == ([explicit if explicit is not None else 'resident_canonical'], [True])


def test_direct_cli_import_uses_canonical_environment(tmp_path):
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('PORTAL_SCRIPT_') and name != 'PORTAL_ISA_SOCKET'}
    env.update(PORTAL_RESIDENT_ID='resident_canonical', ARCHIVE_RESIDENT_ID='resident_legacy')
    probe = "import runpy,sys; m=runpy.run_path(sys.argv[1]); assert m['read_product_env']('PORTAL_RESIDENT_ID')=='resident_canonical'; print('canonical')"
    result = subprocess.run([sys.executable, '-I', '-c', probe, str(Path(check.__file__).resolve())],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'canonical'


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
