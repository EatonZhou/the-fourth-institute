"""Script reader naming compatibility without restart, review writes or smoke work.

:design: decision_15aab18c0ab3
:importance: P2
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import dev_resident_lifecycle_smoke as smoke
from scripts import harness_lineage as harness


REPO = Path(__file__).resolve().parents[2]
PREFIXES = ['archive.', 'tfi.']


@pytest.mark.parametrize('prefix', PREFIXES)
@pytest.mark.parametrize('invalid', ['', 'version', 'batch', 'score'])
def test_harness_reviews_preserve_identity_and_validation(prefix, invalid):
    batch = {'batch_id': 'batch-fixture', 'samples': [{'execution_id': 'execution-fixture'}]}
    results = {'schema': prefix + 'harness-review-results.v1', 'batch_id': 'batch-fixture',
               'reviews': [{'execution_id': 'execution-fixture', 'result': 'pass',
                            'reason': 'synthetic fixture', 'score': 0.5}]}
    if invalid == 'version':
        results['schema'] = prefix + 'harness-review-results.v99'
    elif invalid == 'batch':
        results['batch_id'] = 'other-batch'
    elif invalid == 'score':
        results['reviews'][0]['score'] = 2
    original = deepcopy((batch, results))
    if invalid or prefix != "tfi.":
        with pytest.raises(ValueError):
            harness.validated_reviews(batch, results)
    else:
        assert harness.validated_reviews(batch, results) is results['reviews']
    assert (batch, results) == original
    assert harness.RESULT_SCHEMA == 'tfi.harness-review-results.v1'


@pytest.mark.parametrize('prefix', PREFIXES)
@pytest.mark.parametrize('invalid', ['', 'version', 'service'])
def test_smoke_proof_reads_retained_request_without_business_or_writes(monkeypatch, prefix, invalid):
    request = {'schema': prefix + 'execution-request.v8', 'service_type': 'agent_session',
               'provider': 'codex', 'operation': 'turn',
               'system_instructions': {'content': '<portal-system-prompt role="resident">fixture</portal-system-prompt>'},
               'metadata': {'business_type': 'resident'}}
    if invalid == 'version':
        request['schema'] = prefix + 'execution-request.v99'
    elif invalid == 'service':
        request['service_type'] = 'generation'
    row = {'execution_id': 'execution-fixture', 'status': 'completed',
           'request_json': json.dumps(request), 'checkpoint_json': '{}',
           'result_json': '{"status":"completed"}'}
    original = deepcopy(row)
    queries, closed = [], []

    def connect(schema):
        def execute(sql, parameters):
            assert sql.lstrip().startswith('SELECT ')
            queries.append((schema, parameters))
            if schema == 'lineage':
                assert 'conversation_input_states' in sql
                value = {'completed': True}
            elif schema == 'agent':
                assert 'chat_message_positions reply' in sql
                value = {'execution_id': 'execution-fixture'}
            else:
                assert schema == 'execution_runtime' and 'FROM executions' in sql
                value = row
            return SimpleNamespace(fetchone=lambda: value)
        return SimpleNamespace(execute=execute, close=lambda: closed.append(schema))

    monkeypatch.setattr(smoke, '_read_connection', connect)
    if invalid or prefix != 'tfi.':
        with pytest.raises(AssertionError, match='wrong request contract' if invalid == 'version' or prefix != 'tfi.' else 'wrong execution service'):
            smoke.execution_proof(['message-fixture'])
    else:
        proof, = smoke.execution_proof(['message-fixture'])
        assert proof['request_schema'] == request['schema']
        assert proof['storage_state'] == 'terminal_request_retained'
        assert proof['operation'] == 'turn'
        assert closed == ['lineage', 'agent', 'execution_runtime']
    assert queries == [('lineage', ('message-fixture',)), ('agent', ('message-fixture',)),
                       ('execution_runtime', ('execution-fixture',))]
    assert row == original


@pytest.mark.parametrize('script', ['dev_resident_lifecycle_smoke.py'])
def test_script_imports_outside_repository_without_pythonpath(tmp_path, script):
    command = [sys.executable, '-I', '-c',
               'import runpy, sys; runpy.run_path(sys.argv[1], run_name="read_only_import"); '
               'from management_common.format_tags import schema_matches; '
               'assert schema_matches("tfi.execution-request.v8", "tfi.execution-request.v8")',
               str(REPO / 'scripts' / script)]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
