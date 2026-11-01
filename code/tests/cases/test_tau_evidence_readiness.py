import json
from pathlib import Path
import subprocess

import pytest

from management_portal.test_framework.tools.record_tool_rtt import record


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
