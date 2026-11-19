"""Explicit Linux deployment fixture; never uses the running Portal instance.

Run with the host's Python 3.12. Each run owns an exported Release snapshot,
new dependencies, an instance root, a unique service prefix and fresh database.
The host's normal systemd user manager is required. No production credentials
or PORTAL_* settings are inherited. Evidence stays outside the retired root.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import time
import uuid


def environment(base):
    env = {key: os.environ[key] for key in ('USER', 'LOGNAME', 'LANG', 'LC_ALL',
           'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS') if key in os.environ}
    env.update(PATH='/usr/bin:/bin', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
               TMPDIR=str(base / 'tmp'), NO_PROXY='127.0.0.1,localhost,::1')
    return env


def run(base, label, argv, *, cwd=None, private=False):
    folder = base / 'evidence' / label
    folder.mkdir(parents=True, exist_ok=False)
    started = time.time()
    code = -1
    try:
        with (folder / 'console.log').open('w') as stream:
            result = subprocess.run(argv, cwd=cwd, env=environment(base), stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=1800)
            code = result.returncode
    except subprocess.TimeoutExpired:
        code = 124
    if private:
        (folder / 'console.log').chmod(0o600)
    record = {'argv': argv, 'cwd': str(cwd) if cwd else None, 'exit_code': code,
              'elapsed_seconds': time.time() - started, 'private_output': private}
    (folder / 'command.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'stage': label, 'exit_code': code,
                      'elapsed_seconds': record['elapsed_seconds']}), flush=True)
    return code


def remember_ownership(base, state):
    document = json.loads((Path(state['root']) / 'instance.json').read_text())
    if document['name'] != state['name']:
        raise ValueError('Instance manifest ownership mismatch')
    ports = sorted({int(value) for key, value in document['network'].items() if key.endswith('_port')}
                   | {int(item['web_port']) for item in document['deployments'].values()})
    ownership = {'name': state['name'], 'root': state['root'], 'ports': ports,
                 'units': [f"tfi-{state['name']}-{part}.service" for part in
                           ('postgres', 'nats', 'resource', 'execution', 'society', 'worker', 'web')]}
    (base / 'ownership.json').write_text(json.dumps(ownership, indent=2) + '\n')


def verify_stopped(base, *, retired):
    ownership = json.loads((base / 'ownership.json').read_text())
    for port in ownership['ports']:
        with socket.socket() as client:
            client.settimeout(1)
            if client.connect_ex(('127.0.0.1', port)) == 0:
                raise AssertionError(f'Owned port still open: {port}')
    units = {}
    for unit in ownership['units']:
        output = subprocess.check_output(['systemctl', '--user', 'show', unit,
            '--property=LoadState,ActiveState,MainPID,FragmentPath'], env=environment(base), text=True)
        values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        if values['MainPID'] != '0' or values['ActiveState'] not in ('inactive', 'failed'):
            raise AssertionError(f'Owned service still running: {unit}')
        if retired and (values['LoadState'] != 'not-found' or values['FragmentPath']):
            raise AssertionError(f'Owned service definition remains: {unit}')
        units[unit] = values
    if retired:
        if Path(ownership['root']).exists():
            raise AssertionError('Retired instance root still exists')
        code = base / 'release/code'
        check = 'from management_common.host_instances import read_registry; import sys; assert sys.argv[1] not in read_registry()'
        subprocess.run(['/usr/bin/python3.12', '-c', check, ownership['name']],
                       cwd=code, env=environment(base), check=True)
    record = {'retired': retired, 'ports_closed': ownership['ports'], 'services': units, 'passed': True}
    (base / 'evidence' / ('retirement-check.json' if retired else 'stop-check.json')).write_text(
        json.dumps(record, indent=2) + '\n')
    return 0


def prepare(base, release, revision, patch=None):
    if platform.system() != 'Linux':
        raise ValueError('This deployment case requires Linux')
    if base.exists():
        raise ValueError('A fresh fixture directory is required')
    base.mkdir(parents=True, mode=0o700)
    (base / 'tmp').mkdir()
    commit = subprocess.check_output(['git', '-C', str(release), 'rev-parse', revision + '^{commit}'], text=True).strip()
    archive = base / 'release.tar'
    with archive.open('wb') as stream:
        subprocess.run(['git', '-C', str(release), 'archive', '--format=tar', commit], stdout=stream, check=True)
    snapshot = base / 'release'
    snapshot.mkdir()
    with tarfile.open(archive) as source:
        source.extractall(snapshot, filter='data')
    candidate = None
    if patch:
        target = base / 'candidate.patch'
        shutil.copyfile(patch, target)
        subprocess.run(['git', 'apply', '--check', str(target)], cwd=snapshot, check=True)
        subprocess.run(['git', 'apply', str(target)], cwd=snapshot, check=True)
        candidate = {'patch_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                     'patch': str(target)}
    state = {'schema': 'tfi.linux-deployment-fixture.v1', 'base': str(base),
             'release_commit': commit, 'archive_sha256': hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest(),
             'name': 'tau-linux-' + uuid.uuid4().hex[:10], 'root': str(base / 'instance'),
             'host': platform.platform(), 'machine': platform.machine(), 'uid': os.getuid(),
             'python': platform.python_version(), 'isolation': 'fresh installation on Linux; shared host systemd user manager'}
    state['candidate'] = candidate
    (base / 'fixture.json').write_text(json.dumps(state, indent=2) + '\n')
    return run(base, 'prepare', ['/usr/bin/python3.12', str(snapshot / 'prepare_install.py'), '--platform', 'linux'], cwd=snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'create', 'status', 'stop', 'start', 'retire', 'first-admin',
                                         'verify-stopped', 'verify-retired'))
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--release', type=Path)
    parser.add_argument('--revision')
    parser.add_argument('--label')
    parser.add_argument('--candidate-patch', type=Path,
                        help='Explicit reviewed delta over the immutable Release commit; recorded separately')
    args = parser.parse_args()
    base = args.base.resolve()
    if args.action == 'prepare':
        if not args.release or not args.revision:
            parser.error('prepare requires --release and --revision')
        return prepare(base, args.release, args.revision, args.candidate_patch)
    state = json.loads((base / 'fixture.json').read_text())
    if state['base'] != str(base) or state['root'] != str(base / 'instance') or not re.fullmatch(r'tau-linux-[0-9a-f]{10}', state['name']):
        raise ValueError('Fixture ownership mismatch')
    if args.action.startswith('verify-'):
        return verify_stopped(base, retired=args.action == 'verify-retired')
    if (Path(state['root']) / 'instance.json').is_file():
        remember_ownership(base, state)
    code = base / 'release/code'
    command = ['/usr/bin/python3.12', str(code / 'scripts/tfi_release_install.py'), args.action]
    if args.action == 'create':
        command += ['--engineering', str(base / 'release/.build/example_instance-linux/engineering/manifest.json'),
                    '--name', state['name'], '--root', state['root'], '--python', '/usr/bin/python3.12']
    else:
        command += ['--instance', state['name']]
        if args.action == 'first-admin':
            command += ['--username', 'linux_tau_admin']
    result = run(base, args.label or args.action, command, cwd=code, private=args.action == 'first-admin')
    if result == 0 and args.action == 'create':
        remember_ownership(base, state)
    if result == 0 and args.action == 'retire':
        (base / 'browser-credentials.json').unlink(missing_ok=True)
    return result


if __name__ == '__main__':
    raise SystemExit(main())
