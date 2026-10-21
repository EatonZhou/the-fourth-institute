"""Harness captured scripts use the site's binding without guessing host paths.

:design: decision_15aab18c0ab3, decision_fd760e898f9a
"""
import json
import sys
from types import SimpleNamespace

import pytest

from scripts import harness_lineage as flow

LID = 'lineage_111111111111'
BATCH = 'script_run_' + 'a' * 24


@pytest.fixture
def configure(tmp_path, monkeypatch):
    path = tmp_path / 'site.json'
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(path))
    monkeypatch.setattr(sys, 'path', list(sys.path))
    def write(**settings):
        path.write_text(json.dumps({'deployment': {'system_workflow': settings}}))
    write()
    # Restore the test database route before the database fixture's teardown.
    with pytest.MonkeyPatch.context() as environment:
        environment.delenv('PORTAL_DATABASE_URL', raising=False)
        yield write


def inputs(monkeypatch, *, lineage_id=LID):
    outputs = []
    monkeypatch.setitem(sys.modules, 'portal_script', SimpleNamespace(
        inputs=lambda: {'lineage_id': lineage_id}, set_output=outputs.append))
    monkeypatch.setattr(sys, 'argv', ['harness_lineage.py', 'collect'])
    return outputs


def test_configured_identity_and_release_drive_captured_script(configure, monkeypatch, tmp_path):
    envfile = tmp_path / 'database.env'
    envfile.write_text("export PORTAL_DATABASE_URL='configured-by-site'\n")
    configure(harness_recording_lineage_id=LID, harness_release_tree=str(tmp_path),
              harness_database_env_file=str(envfile))
    outputs = inputs(monkeypatch)
    calls = []
    monkeypatch.setattr(flow, 'maintain', lambda operation: calls.append(operation) or {'collected': 0})
    assert flow.main() == 0
    assert calls == ['collect'] and outputs == [{'operation': 'collect', 'collected': 0}]
    assert sys.path[0] == str(tmp_path)
    assert flow.os.environ['PORTAL_DATABASE_URL'] == 'configured-by-site'


def test_injected_database_environment_skips_unneeded_env_path(configure, monkeypatch, tmp_path):
    configure(harness_recording_lineage_id=LID, harness_release_tree=str(tmp_path))
    flow.os.environ['PORTAL_DATABASE_URL'] = 'already-injected'
    inputs(monkeypatch)
    monkeypatch.setattr(flow, 'maintain', lambda operation: {})
    assert flow.main() == 0
    assert flow.os.environ['PORTAL_DATABASE_URL'] == 'already-injected'


@pytest.mark.parametrize('settings,caller', [({}, LID),
    ({'harness_recording_lineage_id': LID}, 'lineage_222222222222'),
    ({'harness_recording_lineage_id': None}, LID)])
def test_missing_or_foreign_binding_rejects_before_algorithm(configure, monkeypatch, settings, caller):
    configure(**settings)
    outputs = inputs(monkeypatch, lineage_id=caller)
    monkeypatch.setattr(flow, 'maintain', lambda operation: pytest.fail('algorithm must not run'))
    assert flow.main() == 1
    assert outputs == []


def test_release_check_uses_configured_tree_and_reports_missing_path(configure, monkeypatch, tmp_path):
    with pytest.raises(ValueError, match='not configured'):
        flow.release_rules_current()
    configure(harness_release_tree=str(tmp_path))
    calls = []
    monkeypatch.setattr(flow.subprocess, 'run', lambda argv, **kwargs:
        calls.append((argv, kwargs)) or SimpleNamespace(returncode=0))
    assert flow.release_rules_current()
    assert calls[0][1]['cwd'] == tmp_path
    assert calls[0][0][:6] == ['git', 'diff', '--quiet', 'main', 'HEAD', '--']


def test_review_state_is_resolved_per_operation_without_unused_fields(configure, monkeypatch, tmp_path):
    from management_portal.execution import harness_evolution_evaluation as evaluation
    monkeypatch.setattr(evaluation, 'sample_for_review', lambda **kwargs: [])
    first, second = tmp_path / 'first', tmp_path / 'second'
    configure(harness_state_dir=str(first))
    assert flow.sample_review(BATCH, {})['samples_path'] == str(first / 'reviews' / BATCH / 'samples.json')
    configure(harness_state_dir=str(second))
    assert flow.sample_review(BATCH, {})['samples_path'] == str(second / 'reviews' / BATCH / 'samples.json')
    assert (first / 'reviews' / BATCH / 'samples.json').is_file()
    # A caller that already supplies a state directory does not need defaults.
    configure(harness_release_tree=None)
    assert flow.sample_review(BATCH, {}, state_dir=second)['sample_count'] == 0


def test_missing_database_file_is_failure_without_secret_output(configure, monkeypatch, tmp_path, capsys):
    configure(harness_recording_lineage_id=LID, harness_release_tree=str(tmp_path))
    inputs(monkeypatch)
    monkeypatch.setattr(flow, 'maintain', lambda operation: pytest.fail('missing DB configuration'))
    assert flow.main() == 1
    assert 'ValueError' in capsys.readouterr().err
