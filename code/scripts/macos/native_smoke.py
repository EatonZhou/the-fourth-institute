"""Authenticated acceptance against a temporary instance of an installed macOS app."""
import http.cookiejar
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

from scripts.windows.native_smoke import LocalModelApi


INSTANCE = 'desktop'


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def registered_instance(platform):
    """The desktop instance as the installed code's host registry records it, or None."""
    probe = ("import sys,json;sys.path.insert(0,sys.argv[1]);from management_common import host_instances;"
             "r=host_instances.read_registry();i=host_instances.find(sys.argv[2]) if sys.argv[2] in r else None;"
             "print(json.dumps(i and {'root':str(i.root),'manifest':i.document}))")
    result = subprocess.run([sys.executable, '-c', probe, str(Path(platform) / 'code'), INSTANCE],
                            capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


class InstalledSession:
    def __init__(self, instance, included):
        self.root = Path(instance['root'])
        self.included = included
        self.base = f"http://127.0.0.1:{instance['manifest']['network']['entry_port']}"
        self.client = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.model_api = None
        self.private_values = set()

    def request(self, path, payload=None, *, form=False):
        if isinstance(payload, dict):
            self.private_values.update(str(value) for key, value in payload.items()
                                       if key in {'password', 'invitation_secret', 'secret'} and value)
        data = None if payload is None else (urlencode(payload) if form else json.dumps(payload)).encode('utf-8')
        headers = {'Origin': self.base}
        if data is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        try:
            with self.client.open(Request(self.base + path, data=data, headers=headers), timeout=30) as response:
                body = response.read()
                return json.loads(body) if 'application/json' in response.headers.get('Content-Type', '') else body
        except HTTPError as exc:
            try:
                error = json.loads(exc.read(8192)).get('error', {})
                detail = ({key: error[key] for key in ('code', 'message') if key in error}
                          if isinstance(error, dict) else str(error))
                detail = json.dumps(detail, ensure_ascii=False)
            except (ValueError, AttributeError):
                detail = 'Non-JSON error response'
            for value in self.private_values:
                detail = detail.replace(value, '[redacted]')
            operation = (payload or {}).get('instruction', '') if isinstance(payload, dict) else ''
            raise RuntimeError(f'{path.split("?", 1)[0]} {operation}: HTTP {exc.code}: {detail}') from exc

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
        link = (self.root / 'secrets/first-admin-link.txt').read_text(encoding='utf-8')
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
        # All routes belong to this disposable API; chat selects its direct route.
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

    def move_projects(self):
        paths = self.request('/api/admin/paths')
        source = Path(next(row['path'] for row in paths['areas'] if row['name'] == 'projects'))
        target = self.root / 'data/projects-moved'
        accepted = self.request('/api/admin/paths', {'area': 'projects', 'target': str(target)})
        identifier = accepted['operation']['id']
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            record = json.loads((self.root / 'runtime/portal-control.json').read_text())
            if record['id'] == identifier and record['status'] not in {'queued', 'running'}:
                check(record['status'] == 'completed', 'Native area move failed: ' + str(record.get('error')))
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('Native area move did not finish')
        check(target.is_dir() and not source.exists(), 'Native area move retained its source')
        for _ in range(60):
            try:
                if self.request('/api/health')['ok']:
                    self.chat()
                    return
            except OSError:
                pass
            time.sleep(0.5)
        raise RuntimeError('Native area move did not restore Web readiness')

    def close(self):
        if self.model_api is not None:
            self.model_api.close()


def verify_background(destination, instance_root, marker):
    result = subprocess.run(
        [str(destination / 'dependencies/python/bin/python3'), '-I',
         str(destination / 'code/management_portal/resource_source_changes.py')],
        input=json.dumps({'since': 0, 'files': [str(marker)], 'paths': [str(marker)]},
                         ensure_ascii=False).encode('utf-8'),
        capture_output=True, timeout=30, check=True,
    )
    check(json.loads(result.stdout)['changed'] == [str(marker)], 'Index worker corrupted the Unicode path')
    runtime = Path(instance_root) / 'runtime'
    for component in ('resource', 'society', 'web'):
        check((runtime / (component + '.log')).is_file(), component + ' did not write a runtime log')
        for suffix in ('.log', '.error.log'):
            path = runtime / (component + suffix)
            if not path.exists():
                continue
            content = path.read_text(encoding='utf-8', errors='replace')
            for error in ('resource index refresh failed:', 'SocietyService coordinator failed:',
                          'TooManyRemoved', 'UnicodeEncodeError', 'Traceback (most recent call last)'):
                position = content.find(error)
                if position >= 0:
                    # The log tail can hide the actual failure behind later slow
                    # query diagnostics. Preserve the offending traceback in CI.
                    detail = content[max(0, position - 200):position + 8000]
                    raise RuntimeError(path.name + ' background error: ' + error + '\n' + detail)
