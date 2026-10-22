"""Exercise a real Windows EXE in disposable, independent installation directories.

Run only on a disposable Windows runner. No external AI account is registered and
no inference is requested. The receipt never contains passwords or invitations.
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


class LocalModelApi:
    """A loopback-only protocol fixture; never a real model or credential."""
    model = 'deepseek-v4-flash'
    reply = 'Native API round trip confirmed.'

    def __init__(self):
        self.models = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.models.append(payload.get('model'))
                body = json.dumps({'id': 'native-fixture', 'object': 'chat.completion',
                    'model': owner.model, 'choices': [{'index': 0, 'finish_reason': 'stop',
                        'message': {'role': 'assistant', 'content': owner.reply}}],
                    'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def register(self, instruction):
        instruction('CreateModelApi', {'api_id': 'native_api_only', 'provider': 'deepseek',
            'base_url': f'http://127.0.0.1:{self.server.server_port}/v1',
            'default_model': self.model, 'secret': 'disposable-native-fixture'})
        instruction('RegisterProviderAccount', {'account_id': 'native_api_only',
            'provider': 'provider_api', 'kind': 'api_profile', 'config': {'api_id': 'native_api_only'}})
        accounts = instruction('ListProviderAccounts', {})['accounts']
        check(not any(account.get('provider') == 'codex' for account in accounts),
              'API-only fixture unexpectedly has a Codex account')
        instruction('UpdateBackendRole', {'backend_role': 'default', 'account_id': 'native_api_only'})

    def chat(self, instruction, resident_id):
        sent = instruction('DeliverMessage', {'resident_id': resident_id,
            'message': {'content_parts': [{'type': 'text', 'text': 'Reply with the native test confirmation.'}]}})
        message_id = sent['message_id']
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = instruction('GetInputState', {'resident_id': resident_id, 'message_id': message_id})['input_state']
            check(state.get('state') not in ('failed', 'cancelled'), 'API chat input failed: ' + str(state.get('error', '')))
            if state.get('state') == 'completed':
                break
            time.sleep(1)
        else:
            raise RuntimeError('API chat never completed: ' + str(state.get('state')))
        conversation = instruction('GetConversation', {'resident_id': resident_id, 'input_message_id': message_id})
        check(any(self.reply in part.get('text', '') for item in conversation['messages']
                  if item.get('role') != 'user' for part in item.get('content_parts', [])),
              'API reply was not projected into Chat')
        check(self.models and set(self.models) == {self.model}, 'API received an inherited or incorrect model')

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def worker(platform, phase):
    sys.path.insert(0, str(platform / 'code'))
    from scripts.tfi_windows_launcher import DefaultStartup, stop_all
    if phase == 'initialize':
        # Discard the URL: it contains the private first-administrator invitation.
        DefaultStartup(platform, progress=lambda text: print(text, flush=True)).run()
    else:
        stop_all(platform, unregister=True)


def run_worker(platform, phase, log):
    argv = [platform / 'dependencies/python/python.exe',
            platform / 'code/scripts/windows/native_smoke.py',
            '--platform', platform, '--phase', phase]
    with log.open('a', encoding='utf-8') as output:
        subprocess.run(list(map(str, argv)), stdout=output, stderr=subprocess.STDOUT,
                       timeout=900 if phase == 'initialize' else 180, check=True)


def exercise(exe, root, included, evidence):
    check(not root.exists(), 'Native smoke requires a new directory')
    log = evidence / ('full.log' if included else 'base.log')
    argv = [str(exe), '/S', *([] if included else ['/NOEXAMPLES'])]
    # NSIS requires /D= as an unquoted final command-line tail, including spaces.
    command = subprocess.list2cmdline(argv) + ' /D=' + str(root)
    subprocess.run(command, executable=str(exe), check=True, timeout=300)
    check((root / 'windows-package.json').is_file(), 'NSIS did not install the payload')
    state_path = root / 'instance/example_instance/installation.json'
    succeeded = False
    api = None
    try:
        run_worker(root, 'initialize', log)
        state = json.loads(state_path.read_text(encoding='utf-8'))
        manifest = json.loads((root / '.build/w/engineering/manifest.json').read_text(encoding='utf-8'))
        check(manifest['include_examples'] is included, 'NSIS selected the wrong example')
        check(state['status'] == 'installed', 'Instance did not become healthy')
        target = state['target']
        base = f"http://127.0.0.1:{target['ports']['web']}"
        client = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def request(path, payload=None, form=False):
            data = None if payload is None else (urlencode(payload) if form else json.dumps(payload)).encode()
            headers = {'Origin': base}
            if data is not None:
                headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
            with client.open(Request(base + path, data=data, headers=headers), timeout=30) as response:
                body = response.read()
                return json.loads(body) if 'application/json' in response.headers.get('Content-Type', '') else body

        # The initializing parent has exited. Services must remain usable.
        for _ in range(15):
            request('/api/health')
            check(b'<html' in request('/login').lower(), 'Login page is not HTML')
            time.sleep(2)
        private_link = Path(target['data_roots']['secrets_dir']) / 'first-admin-link.txt'
        link = private_link.read_text(encoding='utf-8')
        invitation = link.split('#invite=', 1)[1].split()[0]
        password = secrets.token_urlsafe(18) + 'aA9!'
        registered = request('/api/auth/invitations/accept',
                             {'invitation_secret': invitation, 'password': password})
        ama = registered.get('personal_ama', {})
        check(registered.get('administrator', {}).get('granted'), 'First administrator was not granted')
        check(ama.get('resident_id') and not ama.get('error'), 'First registration could not provision AMA')
        request('/login', {'username': registered['username'], 'password': password}, form=True)
        me = request('/api/me')
        check(me.get('username') == registered['username'], 'Password login did not persist a session')
        def instruction(name, arguments):
            response = request('/api/instructions/execute', {'instruction': name, 'arguments': arguments})
            check(not response.get('error') and response.get('status') == 'committed', name + ' failed')
            return response['result']
        # Enrollment intentionally returns only the AMA identity. Inspect its
        # authenticated graph for the personal Family instead of expecting an
        # internal provision result in the public registration response.
        graph = instruction('GetLineage', {'lineage_id': ama['lineage_id']})['lineage']
        families = [b['family'] for b in graph.get('function_blocks', [])
                    if (b.get('family') or {}).get('personal_ama_id') == ama['resident_id']]
        check(len(families) == 1 and families[0].get('facade'), 'Personal Family was not created')
        facade_id = families[0]['facade']
        for name, arguments in [('GetProject', {'project_id': 'AI员工'}),
                                       *([('GetProject', {'project_id': '元胞自动机研究'})] if included else []),
                                       ('GetConversation', {'resident_id': ama['resident_id']}),
                                       ('GetConversation', {'resident_id': facade_id})]:
            instruction(name, arguments)
        api = LocalModelApi()
        api.register(instruction)
        instruction('UpdateResidentSettings', {'resident_id': ama['resident_id'],
                                               'changes': {'agent_harness': {}}})
        api.chat(instruction, ama['resident_id'])
        catalog = request('/api/agent-workbench/agent-runtime-catalog?' + urlencode({'resident_id': ama['resident_id']}))
        check(catalog['backend'] == 'provider_api' and catalog['models'] == [api.model],
              'Default-only API catalog differs from the executing account')
        instruction('UpdateResidentSettings', {'resident_id': ama['resident_id'], 'changes': {'model': api.model}})
        marker = root / 'instance/example_instance/data/native-restart-marker.txt'
        marker.write_text('Preserve this installation and its registered user', encoding='utf-8')
        run_worker(root, 'cleanup', log)
        run_worker(root, 'initialize', log)
        restarted = json.loads(state_path.read_text(encoding='utf-8'))
        check(restarted['target'] == target, 'Restart changed instance identity')
        check(marker.is_file(), 'Restart lost user data')
        request('/login', {'username': registered['username'], 'password': password}, form=True)
        check(request('/api/me').get('username') == registered['username'], 'Restart lost the account')
        api.chat(instruction, ama['resident_id'])
        # Exercise the installed isolated index worker with the actual Chinese
        # path. -I ignores the fixture's PYTHONUTF8 environment setting.
        change_request = {'since': 0, 'files': [str(marker)], 'paths': [str(marker)]}
        changed = subprocess.run(
            [str(root / 'dependencies/python/python.exe'), '-I',
             str(root / 'code/management_portal/resource_source_changes.py')],
            input=json.dumps(change_request, ensure_ascii=False).encode('utf-8'),
            capture_output=True, timeout=30, check=True,
        )
        check(json.loads(changed.stdout)['changed'] == [str(marker)],
              'Installed index worker did not preserve a Chinese path')
        for component in ('resource', 'society', 'web'):
            component_log = state_path.parent / 'runtime' / (component + '.log')
            check(component_log.is_file(), component + ' did not write a runtime log')
            content = component_log.read_text(encoding='utf-8', errors='replace')
            for error in ('resource index refresh failed:', 'SocietyService coordinator failed:',
                          'TooManyRemoved', 'UnicodeEncodeError'):
                check(error not in content, component + ' background error: ' + error)
        succeeded = True
        return {'include_examples': included, 'status': 'passed', 'registration_ama_chat': 'passed',
                'launcher_exit_service_survival': 'passed', 'unregister_restart_preserves_data': 'passed',
                'chinese_index_worker': 'passed', 'background_error_check': 'passed',
                'api_only_chat_restart': 'passed', 'api_default_model': api.model}
    except Exception:
        # Distinguish loader failure from initdb's restricted-token re-execution;
        # only public version output and exit codes from this disposable fixture.
        if state_path.exists():
            failed = json.loads(state_path.read_text(encoding='utf-8'))
            pg_bin = failed.get('target', {}).get('pg_bin')
            if pg_bin:
                for name in ('postgres', 'initdb', 'pg_ctl', 'psql'):
                    result = subprocess.run([str(Path(pg_bin) / (name + '.exe')), '--version'],
                                            capture_output=True, timeout=30)
                    print(name, 'version probe:', result.returncode,
                          (result.stdout + result.stderr).decode('utf-8', errors='replace'), flush=True)
        raise
    finally:
        # Never leave the test processes running, including failed installations.
        if state_path.exists():
            try:
                run_worker(root, 'cleanup', log)
            except Exception:
                if succeeded:
                    raise
                print('Native cleanup also failed; see the component log', file=sys.stderr)
        if api is not None:
            api.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path)
    parser.add_argument('--work', type=Path)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--platform', type=Path)
    parser.add_argument('--phase', choices=('initialize', 'cleanup'))
    args = parser.parse_args()
    check(sys.platform == 'win32', 'Native smoke must run on Windows')
    if args.phase:
        worker(args.platform.resolve(), args.phase)
        return
    check(all((args.installer, args.work, args.evidence)), 'Installer, work and evidence paths are required')
    import ctypes
    check(not ctypes.windll.shell32.IsUserAnAdmin(), 'Desktop smoke must run as a non-elevated user')
    args.evidence.mkdir(parents=True, exist_ok=True)
    # Deliberately unreachable proxies make an accidental dependency download fail;
    # local HTTP/RPC owners must bypass them. AI services are never called.
    os.environ.update(HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9',
                      ALL_PROXY='http://127.0.0.1:9', NO_PROXY='127.0.0.1,localhost', PIP_NO_INDEX='1', PYTHONUTF8='1')
    rows = []
    for included in (False, True):
        root = args.work / ('中文 空格 ' + ('Lenia' if included else '基础平台'))
        rows.append(exercise(args.installer.resolve(), root.resolve(), included, args.evidence))
    with args.installer.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    receipt = {'schema': 'tfi.windows-native-smoke.v1', 'status': 'passed', 'exe_sha256': digest,
               'process_elevated': False,
               'source_commit': os.environ.get('TFI_PACKAGE_SHA', os.environ.get('GITHUB_SHA', '')),
               'harness_source_commit': os.environ.get('GITHUB_SHA', ''), 'cases': rows,
               'network_check': 'invalid outbound proxies; local-only initialization',
               'ai_inference': 'not_requested'}
    (args.evidence / 'native-smoke.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
