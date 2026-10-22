"""Real, project-local MCP form server for isolated TAU Residents.

Uses the 2025-06-18 stdio/elicitation protocol; never injects Portal events.
No network, credentials, or persistent daemon. EOF terminates the server.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time


FORM = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "title": "Test label", "minLength": 3},
        "count": {"type": "integer", "title": "Test count", "minimum": 1, "maximum": 3},
    },
    "required": ["label", "count"],
}


def install(workspace: Path, run_id: str, evidence: Path) -> dict:
    """Install only into a newly created test Resident, before its first turn."""
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir() or workspace in (Path.home(), Path('/')):
        raise ValueError("A dedicated temporary Resident workspace is required")
    directory = workspace / '.codex'
    if directory.is_symlink():
        raise ValueError("Refusing a linked project configuration directory")
    directory.mkdir(exist_ok=True)
    config = directory / 'config.toml'
    script = directory / 'tau_mcp_forms.py'
    if config.exists() or config.is_symlink() or script.exists() or script.is_symlink():
        raise FileExistsError("Existing project configuration must not be replaced")
    # Reserve both paths exclusively; a concurrent installer cannot overwrite them.
    with script.open('xb') as target:
        target.write(Path(__file__).read_bytes())
    args = [str(script), '--run-id', run_id, '--evidence', str(evidence.resolve())]
    text = (
        '[mcp_servers.tau_forms]\n'
        f'command = {json.dumps(sys.executable)}\n'
        f'args = {json.dumps(args)}\n'
        'startup_timeout_sec = 15\n'
        'tool_timeout_sec = 300\n'
        'enabled_tools = ["request_test_form"]\n'
    )
    try:
        with config.open('x', encoding='utf-8') as target:
            target.write(text)
    except Exception:
        script.unlink()
        raise
    return {'run_id': run_id, 'workspace': str(workspace), 'config': str(config),
            'server': str(script), 'evidence': str(evidence.resolve()),
            'scope': 'temporary_project_only', 'protocol': '2025-06-18'}


class Server:
    def __init__(self, run_id, evidence, output):
        self.run_id, self.evidence, self.output = run_id, evidence, output
        self.pending = {}
        self.counter = 0
        self.supports_forms = False

    def emit(self, message):
        self.output.write(json.dumps({'jsonrpc': '2.0', **message}) + '\n')
        self.output.flush()

    def audit(self, kind, **fields):
        with self.evidence.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'at': time.time(), 'run_id': self.run_id,
                                     'kind': kind, **fields}) + '\n')

    def tool_result(self, request_id, data, error=False):
        self.emit({'id': request_id, 'result': {'content': [
            {'type': 'text', 'text': json.dumps(data)}], 'isError': error}})

    def handle(self, message):
        method, request_id = message.get('method'), message.get('id')
        params = message.get('params') or {}
        if method == 'initialize':
            capabilities = params.get('capabilities') or {}
            forms = capabilities.get('elicitation')
            self.supports_forms = isinstance(forms, dict) and (not forms or 'form' in forms)
            self.audit('initialize', client_supports_forms=self.supports_forms)
            self.emit({'id': request_id, 'result': {'protocolVersion': '2025-06-18',
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'tau_forms', 'version': '1.0'}}})
        elif method == 'ping':
            self.emit({'id': request_id, 'result': {}})
        elif method == 'tools/list':
            self.emit({'id': request_id, 'result': {'tools': [{
                'name': 'request_test_form',
                'description': 'Ask the user for harmless test label/count fields; no side effects.',
                'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            }]}})
        elif method == 'tools/call':
            if params.get('name') != 'request_test_form' or params.get('arguments'):
                self.tool_result(request_id, {'error': 'Unknown tool or nonempty arguments'}, True)
            elif not self.supports_forms:
                self.tool_result(request_id, {'error': 'Client did not advertise form elicitation'}, True)
            else:
                self.counter += 1
                elicitation_id = f'tau-form-{self.counter}'
                self.pending[elicitation_id] = request_id
                self.audit('elicitation_requested', request_id=request_id, elicitation_id=elicitation_id)
                self.emit({'id': elicitation_id, 'method': 'elicitation/create', 'params': {
                    'message': f'TAU test form ({self.run_id}); use test values only.',
                    'requestedSchema': FORM}})
        elif method == 'notifications/cancelled':
            for key, value in list(self.pending.items()):
                if value == params.get('requestId'):
                    del self.pending[key]
                    self.audit('tool_cancelled', request_id=value, elicitation_id=key)
        elif method is not None:
            if request_id is not None:
                self.emit({'id': request_id, 'error': {'code': -32601, 'message': 'Method not found'}})
        elif request_id in self.pending:
            original_id = self.pending.pop(request_id)
            result = message.get('result') or {}
            action, content = result.get('action'), result.get('content')
            valid = action in ('accept', 'decline', 'cancel')
            if action == 'accept':
                valid = (isinstance(content, dict) and isinstance(content.get('label'), str)
                         and len(content['label']) >= 3 and type(content.get('count')) is int
                         and 1 <= content['count'] <= 3)
            self.audit('elicitation_response', request_id=original_id,
                       elicitation_id=request_id, action=action, valid=valid)
            self.tool_result(original_id, {'run_id': self.run_id, 'action': action,
                             'valid': valid}, error=not valid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--install-workspace', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'testrun_[0-9a-f]{16}', args.run_id):
        parser.error('Expected an exact TAU run ID')
    if args.install_workspace:
        print(json.dumps(install(args.install_workspace, args.run_id, args.evidence)))
        return
    server = Server(args.run_id, args.evidence, sys.stdout)
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            server.emit({'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}})
            continue
        if not isinstance(message, dict):
            server.emit({'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}})
            continue
        server.handle(message)


if __name__ == '__main__':
    main()
