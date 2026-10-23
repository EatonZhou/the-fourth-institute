"""Authenticated acceptance against a disposable, installed macOS instance."""
import http.cookiejar
import json
from pathlib import Path
import secrets
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

from scripts.windows.native_smoke import LocalModelApi


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


class InstalledSession:
    def __init__(self, target, included):
        self.target = target
        self.included = included
        self.base = f"http://127.0.0.1:{target['ports']['web']}"
        self.client = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.model_api = None

    def request(self, path, payload=None, *, form=False):
        data = None if payload is None else (urlencode(payload) if form else json.dumps(payload)).encode('utf-8')
        headers = {'Origin': self.base}
        if data is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        with self.client.open(Request(self.base + path, data=data, headers=headers), timeout=30) as response:
            body = response.read()
            return json.loads(body) if 'application/json' in response.headers.get('Content-Type', '') else body

    def instruction(self, name, arguments):
        response = self.request('/api/instructions/execute', {'instruction': name, 'arguments': arguments})
        check(not response.get('error') and response.get('status') == 'committed', name + ' failed')
        return response['result']

    def register(self):
        # The initializing process has exited; launchd services must survive it.
        for _ in range(5):
            self.request('/api/health')
            check(b'<html' in self.request('/login').lower(), 'Login page is not HTML')
            time.sleep(2)
        link = (Path(self.target['data_roots']['secrets_dir']) / 'first-admin-link.txt').read_text(encoding='utf-8')
        invitation = link.split('#invite=', 1)[1].split()[0]
        self.password = secrets.token_urlsafe(18) + 'aA9!'
        registered = self.request('/api/auth/invitations/accept',
                                  {'invitation_secret': invitation, 'password': self.password})
        self.ama = registered.get('personal_ama', {})
        check(registered.get('administrator', {}).get('granted'), 'First administrator was not granted')
        check(self.ama.get('resident_id') and not self.ama.get('error'), 'Registration could not provision AMA')
        self.username = registered['username']
        self.login_and_check()

    def login_and_check(self):
        # Discard old cookies so persisted sessions cannot hide lost credentials.
        self.client = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.request('/login', {'username': self.username, 'password': self.password}, form=True)
        check(self.request('/api/me').get('username') == self.username, 'Password login failed')
        graph = self.instruction('GetLineage', {'lineage_id': self.ama['lineage_id']})['lineage']
        families = [block['family'] for block in graph.get('function_blocks', [])
                    if (block.get('family') or {}).get('personal_ama_id') == self.ama['resident_id']]
        check(len(families) == 1 and families[0].get('facade'), 'Personal Family was not preserved')
        for project in (['AI员工', '元胞自动机研究'] if self.included else ['AI员工']):
            self.instruction('GetProject', {'project_id': project})
        for resident in (self.ama['resident_id'], families[0]['facade']):
            self.instruction('GetConversation', {'resident_id': resident})

    def connect_and_chat(self):
        # Reuse the same loopback protocol fixture as Windows acceptance.
        # Only this disposable API account exists; no Codex fallback can mask a bug.
        self.model_api = LocalModelApi()
        self.model_api.register(self.instruction)
        self.instruction('UpdateResidentSettings', {'resident_id': self.ama['resident_id'],
                                                   'changes': {'agent_harness': {}}})
        self.chat()
        catalog = self.request('/api/agent-workbench/agent-runtime-catalog?' +
                               urlencode({'resident_id': self.ama['resident_id']}))
        check(catalog['backend'] == 'provider_api' and catalog['models'] == [self.model_api.model],
              'Default-only API catalog differs from the executing account')
        self.instruction('UpdateResidentSettings', {'resident_id': self.ama['resident_id'],
                                                   'changes': {'model': self.model_api.model}})

    def chat(self):
        self.model_api.chat(self.instruction, self.ama['resident_id'])

    def close(self):
        if self.model_api is not None:
            self.model_api.close()


def verify_background(destination, marker):
    result = subprocess.run(
        [str(destination / 'dependencies/python/bin/python3'), '-I',
         str(destination / 'code/management_portal/resource_source_changes.py')],
        input=json.dumps({'since': 0, 'files': [str(marker)], 'paths': [str(marker)]},
                         ensure_ascii=False).encode('utf-8'),
        capture_output=True, timeout=30, check=True,
    )
    check(json.loads(result.stdout)['changed'] == [str(marker)], 'Index worker corrupted the Unicode path')
    runtime = destination / 'instance/desktop/runtime'
    for component in ('resource', 'society', 'web'):
        check((runtime / (component + '.log')).is_file(), component + ' did not write a runtime log')
        for suffix in ('.log', '.error.log'):
            path = runtime / (component + suffix)
            if not path.exists():
                continue
            content = path.read_text(encoding='utf-8', errors='replace')
            for error in ('resource index refresh failed:', 'SocietyService coordinator failed:',
                          'TooManyRemoved', 'UnicodeEncodeError', 'Traceback (most recent call last)'):
                check(error not in content, path.name + ' background error: ' + error)
