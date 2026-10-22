"""Protocol checks for the genuine stdio fixture, not Portal acceptance results."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tomllib

import pytest


FIXTURE = Path(__file__).parent / 'fixtures' / 'tau_mcp_forms.py'
RUN = 'testrun_0123456789abcdef'


@pytest.fixture
def server(tmp_path):
    evidence = tmp_path / 'events.jsonl'
    process = subprocess.Popen([sys.executable, str(FIXTURE), '--run-id', RUN,
        '--evidence', str(evidence)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1)
    responses = queue.Queue()
    reader = threading.Thread(target=lambda: [responses.put(line) for line in process.stdout], daemon=True)
    reader.start()

    def exchange(message, *following):
        for item in (message, *following):
            process.stdin.write(json.dumps({'jsonrpc': '2.0', **item}) + '\n')
        process.stdin.flush()
        return json.loads(responses.get(timeout=5))

    yield exchange, evidence
    process.stdin.close()
    try:
        process.wait(timeout=5)
        assert process.returncode == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('response,valid', [
    ({'action': 'accept', 'content': {'label': 'sample', 'count': 2}}, True),
    ({'action': 'accept', 'content': {'count': 2}}, False),
    ({'action': 'accept', 'content': {'label': 'sample', 'count': True}}, False),
    ({'action': 'accept', 'content': {'label': 'sample', 'count': 4}}, False),
    ({'action': 'decline'}, True),
    ({'action': 'cancel'}, True),
])
def test_stdio_elicitation_response_and_stale_response(server, response, valid):
    exchange, evidence = server
    initialized = exchange({'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-06-18', 'capabilities': {'elicitation': {}},
        'clientInfo': {'name': 'fixture-contract', 'version': '1'}}})
    assert initialized['result']['protocolVersion'] == '2025-06-18'
    listed = exchange({'id': 2, 'method': 'tools/list'})
    assert listed['result']['tools'][0]['name'] == 'request_test_form'
    form = exchange({'id': 3, 'method': 'tools/call', 'params': {
        'name': 'request_test_form', 'arguments': {}}})
    assert form['method'] == 'elicitation/create'
    assert form['params']['requestedSchema']['required'] == ['label', 'count']
    # A nested ping must work while the caller is waiting for the human response.
    assert exchange({'id': 4, 'method': 'ping'})['result'] == {}
    result = exchange({'id': form['id'], 'result': response})
    assert result['id'] == 3
    assert result['result']['isError'] is not valid
    # Duplicate response produces no second tool completion; the following ping wins.
    assert exchange({'id': form['id'], 'result': response},
                    {'id': 5, 'method': 'ping'}) == {'jsonrpc': '2.0', 'id': 5, 'result': {}}
    events = [json.loads(row) for row in evidence.read_text().splitlines()]
    assert [row['kind'] for row in events] == [
        'initialize', 'elicitation_requested', 'elicitation_response']
    assert events[-1]['valid'] is valid


def test_missing_client_capability_is_not_silently_passed(server):
    exchange, _ = server
    exchange({'id': 1, 'method': 'initialize', 'params': {'capabilities': {}}})
    result = exchange({'id': 2, 'method': 'tools/call', 'params': {'name': 'request_test_form'}})
    assert result['result']['isError'] is True
    assert 'advertise' in result['result']['content'][0]['text']


def test_install_is_project_local_and_refuses_existing_configuration(tmp_path):
    workspace = tmp_path / 'resident-workspace'
    workspace.mkdir()
    evidence = tmp_path / 'events.jsonl'
    command = [sys.executable, str(FIXTURE), '--install-workspace', str(workspace),
               '--run-id', RUN, '--evidence', str(evidence)]
    installed = subprocess.run(command, capture_output=True, text=True, check=True)
    receipt = json.loads(installed.stdout)
    config = Path(receipt['config'])
    before = config.read_bytes()
    parsed = tomllib.loads(before.decode())['mcp_servers']['tau_forms']
    assert config.parent == workspace / '.codex'
    assert Path(parsed['args'][0]).read_bytes() == FIXTURE.read_bytes()
    assert parsed['command'] == sys.executable
    refused = subprocess.run(command, capture_output=True, text=True)
    assert refused.returncode != 0
    assert config.read_bytes() == before
    assert not evidence.exists()
