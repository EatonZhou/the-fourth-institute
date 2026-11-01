import json
import hashlib
from pathlib import Path
import subprocess

import pytest

from management_portal.test_framework.tools.record_tool_rtt import record


def test_local_helper_errors_keep_hashed_source_locations(tmp_path):
    module = Path(__file__).resolve().parents[2] / 'management_portal/test_framework/tau_safe_error.js'
    helper = tmp_path / 'private helper.js'
    helper.write_text(
        'const {safeError}=require(' + json.dumps(str(module)) + ');\n'
        'const original = new Error("DO-NOT-LOG-body");\n'
        'const synthetic = {stack:"Error: DO-NOT-LOG-body\\n"+'
        '"    at privateFunction (/private account/helper script.js:12:34)\\n"+'
        '"    at privateFunction (C:\\\\private account\\\\helper.js:56:78)\\n"+'
        '"    at file:///private%20account/helper.js:90:12\\n"};\n'
        'console.log(JSON.stringify([safeError(original),safeError(synthetic)]));\n'
    )
    result = subprocess.run(['node', str(helper)], capture_output=True, text=True, check=True)
    actual, synthetic = json.loads(result.stdout)
    frame = actual['stack_frames'][0]
    assert frame == {'source': '[source]', 'source_hash': hashlib.sha256(str(helper).encode()).hexdigest(),
                     'line': 2, 'column': 18}
    expected = [('/private account/helper script.js', 12, 34),
                ('C:\\private account\\helper.js', 56, 78), ('/private account/helper.js', 90, 12)]
    assert synthetic['stack_frames'] == [
        {'source': '[source]', 'source_hash': hashlib.sha256(location.encode()).hexdigest(),
         'line': line, 'column': column} for location, line, column in expected]
    assert all(value not in result.stdout for value in
               ['DO-NOT-LOG', 'privateFunction', 'private account', str(helper), 'helper.js'])


def test_original_tool_measurement_is_persisted_without_including_the_write(tmp_path):
    (tmp_path / 'run-context.json').write_text(json.dumps({
        'run_id': 'run', 'case_id': 'case', 'evidence_dir': str(tmp_path)}))
    first = record(tmp_path, 'exec_command', 1000, 1400, 'returned')
    second = record(tmp_path, 'write_stdin', 2000, 1900, 'failed')
    rows = [json.loads(line) for line in (tmp_path / 'harness-tool-rtt.jsonl').read_text().splitlines()]
    assert rows == [first, second]
    assert first['duration_ms'] == 400 and first['clock_valid']
    assert second['duration_ms'] is None and not second['clock_valid']
    assert first['call_id'] != second['call_id']
    with pytest.raises(ValueError):
        record(tmp_path, 'secret parameter content', 1000, 1400, 'returned')


@pytest.mark.parametrize('arguments', [[], ['--abort-only']])
def test_explicit_ready_and_complete_baseline(arguments):
    script = Path(__file__).with_name('javascript') / 'tau_evidence_readiness.cjs'
    result = subprocess.run(['node', str(script), *arguments], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_network_review_separates_refusal_from_unproven_cancellation(tmp_path):
    module = Path(__file__).resolve().parents[2] / 'management_portal/test_framework/tau_network_review.js'
    rows = [{'kind': 'request_failed', 'request_id': str(i), 'frame_id': 'f',
             'document_id': 'd', 'failed_reason': reason}
            for i, reason in enumerate(['net::ERR_ABORTED', 'net::ERR_CONNECTION_REFUSED',
                                        'net::ERR_TIMED_OUT', None])]
    source, output = tmp_path / 'input.jsonl', tmp_path / 'review.json'
    source.write_text('\n'.join(json.dumps(row) for row in rows))
    result = subprocess.run(['node', '-e',
                             'require(process.argv[1]).writeNetworkReview(process.argv[2],process.argv[3])',
                             str(module), str(source), str(output)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    review = json.loads(output.read_text())
    assert [row['failure_kind'] for row in review['requests']] == [
        'aborted', 'connection_refused', 'other_network_failure', 'unknown']
    assert review['failed_count'] == review['unknown_count'] == 4
    assert review['unproven_abort_count'] == review['connection_refused_count'] == 1
    assert all(row['verdict'] is None and row['proofs'] == [] for row in review['requests'])
    assert review['verdict'] is None
