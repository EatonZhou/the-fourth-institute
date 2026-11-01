"""Public Paper creation preserves explicit operation identity across restart.

:design: decision_4aa13bc21e8e
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from management_common.portal_database import portal_database
from management_portal.resources.registry_store import get_registry_store


def request(root, payload, operation, failure=''):
    result = subprocess.run(
        [sys.executable, '-m', 'tests.cases.paper_entry_restart_support'],
        input=json.dumps(dict(root=str(root), payload=payload, operation=operation, failure=failure)),
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert 'paper-entry-test-private-' not in result.stdout + result.stderr
    return json.loads(result.stdout)


def intents():
    db = portal_database(schema='directory_lifecycle')
    with db.read() as conn:
        return [dict(row) for row in conn.execute(
            f'SELECT * FROM "{db.schema}".resource_creations'
        ).fetchall()]


@pytest.mark.parametrize('failure', ['after_publish', 'before_commit'])
@pytest.mark.parametrize('uploaded_pdf', [False, True])
def test_http_retry_after_process_exit_recovers_same_paper(tmp_path, failure, uploaded_pdf):
    root = tmp_path / 'papers'
    payload = {'title': 'Restartable public paper'}
    if uploaded_pdf:
        from management_common.directory_retention import RetentionStore
        retention = RetentionStore()
        upload_id = 'paper-upload-0123456789ab'
        draft = root / '_incoming' / 'registration-drafts' / upload_id
        retention.declare(draft, category='unused_upload', owner_id=upload_id)
        draft.mkdir(parents=True)
        (draft / 'paper.pdf').write_bytes(b'%PDF-1.7\npublic retry fixture')
        (draft / 'draft.json').write_text(json.dumps({'actor': 'alice', 'metadata': {}}))
        payload['registration_upload_id'] = upload_id
    failed = request(root, payload, 'public-create', failure)
    assert failed['error_type'] == 'RuntimeError'
    intent, = intents()
    assert not intent['completed']
    assert get_registry_store().get('paper', intent['resource_id']) is None
    manifest, = root.rglob('manifest.json')
    identity = manifest.parent.stat().st_ino
    original = manifest.read_bytes()

    recovered = request(root, payload, 'public-create')
    assert recovered['paper']['id'] == intent['resource_id']
    assert manifest.parent.stat().st_ino == identity
    assert manifest.read_bytes() == original
    assert get_registry_store().get('paper', intent['resource_id']) is not None
    assert len(intents()) == 1
    assert intents()[0]['completed']
    assert request(root, payload, 'public-create')['paper']['id'] == intent['resource_id']
    if uploaded_pdf:
        from management_common.directory_paths import cleanup_location
        assert (manifest.parent / 'paper.pdf').read_bytes() == (draft / 'paper.pdf').read_bytes()
        with retention.database.read() as conn:
            ownership = conn.execute(f'SELECT owner_id, category FROM {retention.table} WHERE location=%s',
                                     (cleanup_location(draft),)).fetchone()
        assert ownership['owner_id'] == intent['resource_id']
        assert ownership['category'] == 'object_data'


@pytest.mark.parametrize('operations', [('first', 'second'), (None, None)])
def test_independent_requests_do_not_reuse_failed_creation(tmp_path, operations):
    payload = {'title': 'Independent operation'}
    for operation in operations:
        assert request(tmp_path / 'papers', payload, operation, 'after_publish')['error_type'] == 'RuntimeError'
    rows = intents()
    assert len(rows) == 2
    assert len({row['resource_id'] for row in rows}) == 2
    assert len({row['operation_id'] for row in rows}) == 2


def test_new_operation_keeps_doi_merge_and_project_association(tmp_path):
    from tests.cases.registry_support import write_projects
    write_projects({'projects': [{'id': 'FirstProject'}, {'id': 'SecondProject'}]})
    first = request(tmp_path / 'papers', {
        'title': 'Original title', 'doi': '10.1000/retry', 'project_ids': ['FirstProject'],
    }, 'first')['paper']
    second = request(tmp_path / 'papers', {
        'title': 'Other title', 'doi': '10.1000/retry', 'project_ids': ['SecondProject'],
    }, 'second')['paper']
    assert second['id'] == first['id']
    assert set(second['project_ids']) == {'FirstProject', 'SecondProject'}


def test_reusing_failed_operation_with_changed_arguments_is_rejected(tmp_path):
    root = tmp_path / 'papers'
    request(root, {'title': 'Original'}, 'same-operation', 'before_commit')
    changed = request(root, {'title': 'Changed'}, 'same-operation')
    assert changed['error_type'] == 'ValueError'
    assert 'different arguments' in changed['error']
    assert len(intents()) == 1


def test_completed_creation_key_cannot_bypass_validation_through_doi_match(tmp_path):
    root = tmp_path / 'papers'
    payload = {'title': 'Original', 'doi': '10.1000/same-operation'}
    first = request(root, payload, 'same-operation')
    assert 'paper' in first
    changed = request(root, {**payload, 'title': 'Changed'}, 'same-operation')
    assert changed['error_type'] == 'ValueError'
    assert 'different arguments' in changed['error']


@pytest.mark.parametrize('operation', ['invalid key', 'x' * 201, '中文'])
def test_invalid_header_is_rejected_without_echoing_it(tmp_path, operation):
    result = request(tmp_path / 'papers', {'title': 'Invalid header'}, operation)
    assert result['error_type'] == 'ValueError'
    assert operation not in result['error']
    assert not (tmp_path / 'papers').exists()


def test_registration_form_retry_keys():
    source = Path(__file__).with_name('javascript') / 'paper_registration_retry.cjs'
    result = subprocess.run(['node', '--test', str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
