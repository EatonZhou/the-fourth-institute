"""Plan, apply, import and check an installed instance's deployment configuration.

This owns configuration, not data movement. Changed deployments require stopped
consumers and already prepared destinations. No mounts, aliases or data copies
are created. An interrupted configuration transaction is recovered explicitly.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.instance_config import (
    STATE_FILE, STATE_SCHEMA, TARGET_FILE, atomic_json, configuration_digest,
    read_installation, read_json, read_target, write_installation,
    configuration_lock,
    source_roots,
)

JOURNAL = 'maintenance/configuration-update.json'
# Changing installation identity, code or native dependencies belongs to the
# offline deployment mover/installer, never an in-process directory migration.
IDENTITY = ('install_root', 'service_prefix', 'database_name', 'database_user',
            'run_id', 'shared_code_root', 'python', 'pg_bin', 'pg_share', 'pg_lib',
            'nats_server', 'node_bin', 'managed_dependencies')


def validate(target, state):
    from scripts import tfi_release_install as api
    normalized = api.target_plan(target, protected_roots=state['protected_roots'])
    if 'development' in target:
        dev = target['development']
        if not isinstance(dev, dict) or dev.get('schema') != 'tfi.development-runtime.v1':
            raise ValueError('Invalid paired development configuration')
        ports = dev.get('web_ports', {})
        if (set(ports) != {'dev', 'release'} or
                any(type(p) is not int or not 1024 <= p <= 65535 for p in ports.values()) or
                dev.get('router_port') != normalized['ports']['web'] or
                len(set([*ports.values(), *normalized['ports'].values()])) != 5):
            raise ValueError('Paired Web ports must be distinct; router_port must equal ports.web')
        for name in ('dev_code', 'release_code'):
            p = Path(dev[name])
            if not p.is_absolute() or not (p / 'management_portal/asgi.py').is_file():
                raise ValueError('Paired source is unavailable: ' + name)
    return normalized


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def projections(root, target):
    """Generated files contain no independent deployment decisions."""
    root = Path(root)
    site_path = root / 'config/site.json'
    site = read_json(site_path) if site_path.exists() else {'site_name': 'TFI Portal'}
    site = copy.deepcopy(site)
    deployment = site.setdefault('deployment', {})
    dev = target.get('development')
    deployment.update(source_roots(target))
    result = {
        'config/site.json': json_bytes(site),
        'config/network.env': (f"PORTAL_ACCESS_MODE={'local' if target.get('bind', '127.0.0.1') == '127.0.0.1' else 'remote'}\n"
                               f"PORTAL_PORT={target['ports']['web']}\n").encode(),
    }
    # DB URLs and tool paths come from the same environment builder as startup.
    from scripts.tfi_release_install import clean_environment
    env = clean_environment(target)
    result['config/postgres.env'] = ('PORTAL_DATABASE_URL=' + env['PORTAL_DATABASE_URL'] + '\n').encode()
    if dev:
        result['config/development.json'] = json_bytes(dev)
        result['config/deployments.json'] = json_bytes({
            'schema': 'tfi.portal-deployments.v1', 'instances': [
                {'id': name, 'label': name.title(), 'kind': name,
                 'origin': f'http://127.0.0.1:{port}', 'order': index}
                for index, (name, port) in enumerate(dev['web_ports'].items())]})
    return result


def paired_outputs(root, target, directory):
    dev = target.get('development')
    if not dev:
        return {}
    from scripts.tfi_release_dependencies import runtime_python
    from scripts.tfi_release_platform_linux import REQUEST_PATH_CPU_WEIGHT
    previous = read_installation(root)['target']
    python = runtime_python(previous)
    script = Path(dev['release_code']) / 'scripts/tfi_development_runtime.py'
    result = {}
    for name, mode in (('dev-web', 'web'), ('router', 'router')):
        command = [python, str(script), mode, '--root', str(root)]
        content = (f"[Unit]\nDescription=TFI paired {name}\n"
                   f"[Service]\nType=simple\nUMask=0077\nWorkingDirectory={dev['dev_code']}\n"
                   f"ExecStart={' '.join(json.dumps(v) for v in command)}\n"
                   f"Restart=on-failure\nRestartSec=3\n{REQUEST_PATH_CPU_WEIGHT}TimeoutStopSec=90\n"
                   "[Install]\nWantedBy=default.target\n")
        path = directory / (target['service_prefix'] + '-' + name + '.service')
        if path.exists() and 'Description=TFI paired ' not in path.read_text():
            raise ValueError('Foreign paired service: ' + str(path))
        result[str(path)] = content.encode()
    if dev.get('gateway_unit'):
        name = dev['gateway_unit']
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', name):
            raise ValueError('Invalid HTTPS gateway service name')
        path = directory / (name + '.d/tfi-development.conf')
        if path.exists() and not path.read_text().startswith('# TFI paired runtime'):
            raise ValueError('Foreign gateway override')
        content = ("# TFI paired runtime\n[Service]\n"
                   + 'Environment=' + json.dumps('PORTAL_DEPLOYMENT_REGISTRY_PATH=' + str(root / 'config/deployments.json')) + '\n'
                   + 'Environment=' + json.dumps('PORTAL_SHARED_SOURCE_ROOT=' + dev['dev_code']) + '\n')
        result[str(path)] = content.encode()
    return result


def unit_changes(root, old, target):
    """Update deployment-owned fields while preserving Provider version pins.

    Native backend installation remains owned by its adapter. On Linux its
    command/version is independent runtime state; only deployment-derived
    environment, working paths and path-valued arguments are regenerated here.
    """
    from scripts import tfi_release_install as api
    if not sys.platform.startswith('linux'):
        # Native lifecycle adapters generate core definitions from this target.
        # Offline path changes must use their platform installer until supported.
        if old != target:
            raise ValueError('Changed configuration application currently requires Linux')
        return {}
    directory = api.unit_directory()
    old_env, new_env = api.clean_environment(old), api.clean_environment(target)
    mappings = sorted(((v, target['data_roots'][k]) for k, v in old['data_roots'].items()
                       if v != target['data_roots'][k]), key=lambda pair: -len(pair[0]))
    def mapped(value):
        for before, after in mappings:
            if value == before or value.startswith(before + '/'):
                return after + value[len(before):]
        return value
    changes = {}
    paths = set(directory.glob(old['service_prefix'] + '-*.service'))
    gateway = old.get('development', {}).get('gateway_unit')
    if gateway:
        paths.add(directory / gateway)
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError('Foreign linked service definition: ' + path.name)
        content = path.read_text()
        for line in content.splitlines():
            if not line.startswith('EnvironmentFile='):
                continue
            for value in shlex.split(line.partition('=')[2]):
                optional = value.startswith('-')
                source = Path(value.removeprefix('-'))
                if not source.is_absolute() or any(c in str(source) for c in '*?%'):
                    raise ValueError('Unresolved service environment input: ' + path.name)
                if optional and not source.exists():
                    continue
                # Provider proxy settings and secrets stay independent; an
                # external file must not override deployment-owned fields.
                keys = {m.group(1) for raw in source.read_text().splitlines()
                        if (m := re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=', raw))}
                if keys.intersection(old_env):
                    raise ValueError('External environment overrides deployment configuration: ' + path.name)
        for dropin in (directory / (path.name + '.d')).glob('*.conf'):
            if (path.name == gateway and dropin.name == 'tfi-development.conf'
                    and dropin.read_text().startswith('# TFI paired runtime\n')):
                continue
            raise ValueError('Service has drop-ins; reconcile before applying: ' + path.name)
        lines = []
        legacy_locator_found = False
        for line in content.splitlines():
            if line.startswith('Environment='):
                assignments = []
                for assignment in shlex.split(line[len('Environment='):]):
                    key, sep, value = assignment.partition('=')
                    legacy_locator = (key == 'PORTAL_PATHS_CONFIG' and
                                      value.replace('%%', '%') == str(Path(old['install_root']) / 'config/paths.json'))
                    legacy_locator_found = legacy_locator_found or legacy_locator
                    if sep and key in old_env and (value.replace('%%', '%') == old_env[key] or legacy_locator):
                        value = str(new_env[key]).replace('%', '%%')
                    else:
                        value = mapped(value)
                    assignments.append(json.dumps(key + '=' + value, ensure_ascii=False))
                line = 'Environment=' + ' '.join(assignments)
            elif line.startswith('WorkingDirectory='):
                line = 'WorkingDirectory=' + mapped(line.partition('=')[2])
            elif line.startswith('ExecStart='):
                args = shlex.split(line.partition('=')[2])
                line = 'ExecStart=' + ' '.join(json.dumps(mapped(arg), ensure_ascii=False) for arg in args)
            lines.append(line)
        candidate = '\n'.join(lines) + '\n'
        # Avoid gratuitous formatting rewrites on adoption or idempotent apply.
        if old != target and candidate != content:
            changes[str(path)] = candidate.encode()
        elif legacy_locator_found:
            # Retire the duplicate-file locator online without reformatting
            # unrelated commands or changing any effective deployment value.
            changes[str(path)] = content.replace(
                str(Path(old['install_root']) / 'config/paths.json'),
                str(Path(target['install_root']) / TARGET_FILE)).encode()
    changes.update(paired_outputs(Path(root), target, directory))
    return changes


def assert_stopped(target):
    from scripts import tfi_release_install as api
    if not sys.platform.startswith('linux'):
        raise ValueError('Use the native installer for changed deployments')
    prefix = target['service_prefix']
    selectors = [prefix + '-*']
    if target.get('development', {}).get('gateway_unit'):
        selectors.append(target['development']['gateway_unit'])
    rows = json.loads(subprocess.run(['systemctl', '--user', 'list-units', '--all',
                        '--output=json', *selectors], check=True, capture_output=True,
                        text=True, timeout=20).stdout)
    # All consumers, not just Web; timers/sockets must not reactivate them.
    active = [row['unit'] for row in rows if row.get('active') not in {'inactive', 'failed'}]
    if active:
        raise ValueError('Configuration changes require stopped instance consumers: ' + ', '.join(active))
    api.check_available(target, existing=True)


def plan(root, candidate=None, *, allow_topology=False):
    root = Path(root).resolve()
    state = read_installation(root)
    old = state['target']
    target = validate(candidate if candidate is not None else old, state)
    if Path(target['install_root']) != root:
        raise ValueError('Configuration belongs to another installation')
    if any(target.get(key) != old.get(key) for key in IDENTITY):
        raise ValueError('Installation identity/code/dependency changes require the offline deployment mover')
    if old.get('development') != target.get('development') and not allow_topology:
        raise ValueError('Paired topology changes require paired runtime configure')
    outputs = {str(root / name): value for name, value in projections(root, target).items()}
    outputs.update(unit_changes(root, old, target))
    outputs[str(root / TARGET_FILE)] = json_bytes(target)
    changes = {path: data for path, data in outputs.items()
               if not Path(path).exists() or Path(path).read_bytes() != data}
    return state, target, changes


def _write_bytes(path, data):
    import tempfile
    from management_common.portal_paths import _preserve_config_permissions
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing linked configuration output: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            _preserve_config_permissions(Path(name), path)
        os.replace(name, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def apply(root, candidate=None, *, adopt=False, allow_topology=False, expected_sha256=None):
    from scripts import tfi_release_install as api
    root = Path(root).resolve()
    with api.installation_lock(root), configuration_lock(root, create=True):
        journal_path = root / JOURNAL
        if journal_path.exists() and read_json(journal_path)['status'] not in {'complete', 'rolled_back'}:
            raise ValueError('Interrupted configuration update; run recover first')
        state = read_installation(root)
        if expected_sha256 is not None and expected_sha256 != configuration_digest(state['target']):
            raise ValueError('Configuration changed since planning; regenerate the candidate')
        if adopt:
            if state['schema'] == STATE_SCHEMA:
                return check(root)
            target = copy.deepcopy(state['target'])
            dev = root / 'config/development.json'
            if dev.exists():
                target['development'] = read_json(dev)
            target = validate(target, state)
            # Import asserts existing projections agree; never choose between
            # conflicting roots, and never change the current live directories.
            legacy_paths = root / 'config/paths.json'
            if legacy_paths.exists() and read_json(legacy_paths) != target['data_roots']:
                raise ValueError('Existing paths disagree with target; reconcile before import')
            changes = {str(root / TARGET_FILE): json_bytes(target)}
            # All generated deployment projections must agree before importing
            # a live instance. Adoption changes ownership, never live values.
            for name, data in projections(root, target).items():
                path = root / name
                equal = (path.exists() and (read_json(path) == json.loads(data)
                         if name.endswith('.json') else path.read_bytes() == data))
                if not equal:
                    raise ValueError('Existing deployment projection disagrees: ' + name)
        else:
            state, target, changes = plan(root, candidate, allow_topology=allow_topology)
            if target != state['target']:
                if expected_sha256 is None:
                    raise ValueError('Changed configuration requires --expected-sha256 from plan')
                assert_stopped(state['target'])
                for path in target['data_roots'].values():
                    if not Path(path).is_dir():
                        raise ValueError('Prepare data destination before applying: ' + path)
        new_state = {key: value for key, value in state.items() if key != 'target'}
        new_state.update(schema=STATE_SCHEMA, configuration_sha256=configuration_digest(target))
        changes[str(root / STATE_FILE)] = json_bytes(new_state)
        old_bytes = {path: Path(path).read_bytes() if Path(path).exists() else None
                     for path in [*changes, str(root / STATE_FILE)]}
        folder = root / 'maintenance' / ('configuration-' + str(time.time_ns()))
        folder.mkdir(mode=0o700, parents=True)
        entries = []
        for i, (path, data) in enumerate(old_bytes.items()):
            if data is not None:
                _write_bytes(folder / str(i), data)
            entries.append({'path': path, 'backup': str(folder / str(i)) if data is not None else None,
                            'before': hashlib.sha256(data).hexdigest() if data is not None else None,
                            'after': hashlib.sha256(changes[path]).hexdigest()})
        journal = {'status': 'applying', 'backup': str(folder), 'entries': entries,
                   'configuration_sha256': configuration_digest(target), 'adopt': adopt,
                   'original_target': state['target']}
        atomic_json(journal_path, journal)
        try:
            for path, data in changes.items():
                if (Path(path).read_bytes() if Path(path).exists() else None) != old_bytes[path]:
                    raise ValueError('Configuration changed concurrently: ' + path)
                _write_bytes(path, data)
            if any(not Path(path).is_relative_to(root) for path in changes):
                subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True,
                               capture_output=True, timeout=20)
            result = check(root)
            journal['status'] = 'complete'
            atomic_json(journal_path, journal)
            return {**result, 'changed_files': sorted(changes), 'backup': str(folder)}
        except Exception:
            # Preserve evidence, and never overwrite a concurrent edit during
            # recovery. Explicit recover performs the guarded restoration.
            journal['status'] = 'interrupted'
            atomic_json(journal_path, journal)
            raise


def recover(root):
    from scripts import tfi_release_install as api
    root = Path(root).resolve()
    with api.installation_lock(root), configuration_lock(root, create=True):
        path = root / JOURNAL
        journal = read_json(path)
        if journal['status'] not in {'applying', 'interrupted'}:
            raise ValueError('No interrupted configuration transaction')
        # Recover only exact known before/after images; never overwrite changes
        # made by an operator or by a subsequent installation operation.
        for entry in journal['entries']:
            destination = Path(entry['path'])
            actual = hashlib.sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
            if actual not in {entry['before'], entry['after']}:
                raise ValueError('Recovery blocked by concurrent changes: ' + str(destination))
            if entry['backup']:
                backup = Path(entry['backup'])
                if (backup.parent != Path(journal['backup']) or
                        hashlib.sha256(backup.read_bytes()).hexdigest() != entry['before']):
                    raise ValueError('Invalid configuration backup')
        if not journal['adopt']:
            # Deployment changes are offline; rollback must remain offline.
            target = journal['original_target']
            assert_stopped(target)
        for entry in reversed(journal['entries']):
            destination = Path(entry['path'])
            if entry['backup']:
                _write_bytes(destination, Path(entry['backup']).read_bytes())
            else:
                destination.unlink(missing_ok=True)
        if any(not Path(e['path']).is_relative_to(root) for e in journal['entries']):
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, capture_output=True, timeout=20)
        journal['status'] = 'rolled_back'
        atomic_json(path, journal)
        return {'ok': True, 'status': 'rolled_back'}


def check(root):
    root = Path(root).resolve()
    state = read_installation(root)
    target = validate(state['target'], state)
    drift = []
    for name, data in projections(root, target).items():
        path = root / name
        if name.endswith('.json'):
            equal = path.exists() and read_json(path) == json.loads(data)
        else:
            equal = path.exists() and path.read_bytes() == data
        if not equal:
            drift.append(name)
    if drift:
        raise ValueError('Generated configuration drift: ' + ', '.join(drift))
    # Installed adapters keep owning version-specific Provider commands; verify
    # that their deployment paths have not drifted from the canonical target.
    if sys.platform.startswith('linux'):
        from scripts import tfi_release_install as api
        env = api.clean_environment(target)
        controlled = {'HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'TMPDIR',
                      'PORTAL_PATHS_CONFIG', 'PORTAL_SITE_CONFIG', 'PORTAL_NETWORK_CONFIG',
                      'PORTAL_POSTGRES_ENV_FILE', 'PORTAL_DATABASE_URL', 'PORTAL_NATS_URL',
                      'PORTAL_EXECUTION_STATE_ROOT', 'PORTAL_EXECUTION_WORKER_HEALTH_PATH',
                      'PORTAL_EXECUTION_WORKER_GENERATION_ENV', *api.ENV_KEYS.values(),
                      *(f'PORTAL_{name}_SERVICE_SOCKET' for name in ('RESOURCE', 'EXECUTION', 'SOCIETY'))}
        directory = api.unit_directory()
        for name, data in paired_outputs(root, target, directory).items():
            path = Path(name)
            if not path.exists() or path.read_bytes() != data:
                raise ValueError('Paired service configuration drift: ' + path.name)
        paths = set(directory.glob(target['service_prefix'] + '-*.service'))
        gateway = target.get('development', {}).get('gateway_unit')
        if gateway:
            paths.add(directory / gateway)
        for path in paths:
            for line in path.read_text().splitlines():
                if not line.startswith('Environment='):
                    continue
                for assignment in shlex.split(line.partition('=')[2]):
                    key, _, value = assignment.partition('=')
                    value = value.replace('%%', '%')
                    # The pre-existing public gateway owns its own ingress
                    # port/access policy; this instance owns its shared paths,
                    # database endpoint and the generated paired-route drop-in.
                    if path.name == gateway and key == 'PORTAL_NETWORK_CONFIG':
                        continue
                    if key in controlled and value != env[key]:
                        # Site adapters can pass the concrete instance directory
                        # rather than the parent instances_root to a Provider.
                        if key == 'PORTAL_INSTANCES_ROOT' and value.startswith(env[key] + '/'):
                            continue
                        raise ValueError('Service configuration drift: ' + path.name + ' ' + key)
                    if key == 'CODEX_HOME' and value != target['data_roots']['accounts_root'] + '/codex/%i':
                        raise ValueError('Service configuration drift: ' + path.name + ' CODEX_HOME')
    return {'ok': True, 'configuration': str(root / TARGET_FILE),
            'configuration_sha256': configuration_digest(target), 'state_schema': state['schema']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'apply', 'adopt', 'check', 'recover'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--expected-sha256')
    args = parser.parse_args(argv)
    candidate = read_json(args.candidate) if args.candidate else None
    if args.command == 'plan':
        state, target, changes = plan(args.root, candidate)
        result = {'source_sha256': configuration_digest(state['target']),
                  'configuration_sha256': configuration_digest(target),
                  'changed_files': sorted(changes), 'requires_stopped_consumers': target != state['target']}
    elif args.command == 'recover':
        result = recover(args.root)
    elif args.command == 'check':
        result = check(args.root)
    else:
        result = apply(args.root, candidate, adopt=args.command == 'adopt', expected_sha256=args.expected_sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
