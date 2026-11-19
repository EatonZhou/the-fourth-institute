"""Resumable instance update orchestration; persistent domains own their changes.

No seed import, guessed service, or application data copy belongs here. Owners
inspect their installed objects and return serializable plans in the target
code environment. The receipt is also the recovery input, not a success cache.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from management_common.configuration_files import atomic_json
from management_common.instance_manifest import load
from scripts import instance_services as services


def code_identity(instance):
    result = {}
    for deployment, row in instance.deployments.items():
        code = Path(row['code_root'])
        try:
            revision = subprocess.run(['git', '-C', str(code), 'rev-parse', 'HEAD'],
                                      capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            revision = subprocess.CompletedProcess([], 1, stdout='')
        diff = subprocess.run(['git', '-C', str(code), 'diff', '--binary', 'HEAD', '--', '.'],
                              capture_output=True, timeout=30) if revision.returncode == 0 else None
        if diff is not None and diff.returncode:
            raise ValueError('Cannot inspect source changes before update')
        # Packages have no Git metadata. Their version file remains a useful
        # identity; dependency identity is checked by prepare_code separately.
        version = code / 'VERSION'
        package = next((code.parent / name for name in ('macos-package.json', 'windows-package.json')
                        if (code.parent / name).is_file()), None)
        metadata = []
        if revision.returncode and not package:
            from management_common.code_environment import GENERATED_CODE_DIRECTORIES
            for directory, names, files in os.walk(code):
                names[:] = sorted(n for n in names if n not in GENERATED_CODE_DIRECTORIES and n != '.git')
                for name in sorted(files):
                    if name.endswith(('.pyc', '.pyo')):
                        continue
                    path = Path(directory) / name
                    stat = path.stat()
                    metadata.append((str(path.relative_to(code)), stat.st_size, stat.st_mtime_ns))
        result[deployment] = {'path': str(code), 'commit': revision.stdout.strip() if revision.returncode == 0 else '',
                             'version': version.read_text().strip() if version.is_file() else '',
                             'diff_sha256': hashlib.sha256(diff.stdout).hexdigest() if diff else '',
                             'package_sha256': hashlib.sha256(package.read_bytes()).hexdigest() if package else '',
                             'unversioned_metadata': hashlib.sha256(json.dumps(metadata).encode()).hexdigest() if metadata else ''}
    return result


def current(instance):
    record = read_receipt(instance)
    return bool(record and record.get('status') == 'updated' and
                record.get('instance_id') == instance.instance_id and record.get('code') == code_identity(instance))


def receipt_path(instance):
    return instance.directory('runtime') / 'update-code.json'


def read_receipt(instance):
    path = receipt_path(instance)
    return json.loads(path.read_text()) if path.exists() else None


def run(instance, *, prepare, child, observe, base_python=None, offline=False, plan_only=False):
    """CLI's instance control lock spans this operation, including recovery."""
    previous = read_receipt(instance)
    if previous and previous.get('instance_id') != instance.instance_id:
        raise ValueError('Update receipt belongs to a different instance')
    blocker = services.start_blocker(instance)
    if blocker:
        raise ValueError(blocker)
    identity = code_identity(instance)
    recovering = previous and previous.get('status') in {'running', 'failed'} and previous.get('mutation_started', False)
    if recovering and previous['code'] != identity:
        raise ValueError('Interrupted update belongs to different code; restore that code and resume first')
    control = services.controller(instance)
    before = previous['before'] if recovering else services.status(instance)
    if not recovering and hasattr(control, 'is_enabled'):
        for component, state in before.items():
            state['enabled'] = control.is_enabled(services.unit_name(instance, component))
    database_before = before.get('postgres', {})
    database_wanted = database_before.get('active') == 'active' or (
        database_before.get('active') == 'failed' and database_before.get('enabled', True))
    record = previous if recovering else {
        'schema': 'tfi.instance_update.v1', 'id': uuid.uuid4().hex,
        'instance_id': instance.instance_id, 'code': identity, 'before': before,
        'completed': [], 'owners': {}, 'started_at': time.time(),
    }
    record['status'] = 'running'
    record.pop('error', None)

    def save(phase):
        record['phase'] = phase
        record['updated_at'] = time.time()
        atomic_json(receipt_path(instance), record)

    def owners(phase, plan=None):
        # Input/output files avoid command-line size limits. They contain plans
        # and public diagnostics only, never account tokens or database rows.
        request = instance.directory('runtime') / 'update-request.json'
        atomic_json(request, {'phase': phase, 'plan': plan or {}})
        return json.loads(child(instance, 'instance_update_owners.py', str(request)))

    # Preserve an immutable receipt for each finished attempt.
    if previous and not recovering and not plan_only:
        atomic_json(instance.directory('runtime') / 'updates' / (previous['id'] + '.json'), previous)
    try:
        if not plan_only:
            save('prepare_dependencies')
            for code in sorted({Path(r['code_root']) for r in instance.deployments.values()}):
                prepare(code, base_python=base_python, offline=offline)
        services.verify_definitions(instance, exact=False)
        # Domain inspection needs its database, even on a stopped instance.
        if control.status(services.unit_name(instance, 'postgres'))['active'] != 'active':
            control.start(services.unit_name(instance, 'postgres'))
            services.wait_database(instance)
        plan = record['plan'] if recovering else owners('plan')
        record['plan'] = plan
        if plan_only:
            return {'status': 'planned', 'code': identity, 'plan': plan, 'services': before}
        save('preflight')
        if plan.get('blockers'):
            raise ValueError('Feature update preflight failed; see update-code.json plan.blockers')
        if code_identity(instance) != identity:
            raise ValueError('Code changed during update preparation; retry against a stable checkout')
        # Persist original running intent before any stop; retry must never
        # replace it with the partially stopped state left by a failed update.
        record['mutation_started'] = True
        save('stop')
        services.lifecycle('stop', instance.root)
        for component in plan.get('services', []):
            services.register_component(instance.root, component)
        instance = load(instance.root)
        control = services.controller(instance)
        services.install_definitions(instance)
        control.start(services.unit_name(instance, 'postgres'))
        services.wait_database(instance)
        save('apply_domains')
        record['owners'] = owners('apply', plan)
        if record['owners'].get('blockers'):
            raise ValueError('Feature reconciliation incomplete; see update-code.json owners.blockers')
        record['completed'] = ['dependencies', 'definitions', 'domains']
        wanted = {c for c, state in before.items() if state['active'] == 'active' or
                  (state['active'] == 'failed' and state.get('enabled', True))}
        wanted.update(plan.get('start_services', []))
        # Required new services cannot implicitly reactivate an intentionally
        # stopped prerequisite. Owners report these as preflight conflicts.
        save('restart')
        if services.platform_name() in {'linux', 'macos'}:
            for component in plan.get('start_services', []):
                control.set_enabled(services.unit_name(instance, component), True)
        services.lifecycle('start', instance.root,
                           excluded_components=tuple(c for c in services.components(instance) if c not in wanted))
        save('verify')
        record['verification'] = owners('verify', plan)
        if not database_wanted:
            control.stop(services.unit_name(instance, 'postgres'))
        states = services.status(instance)
        record['services'] = states
        if record['verification'].get('blockers'):
            raise ValueError('Feature verification failed; see update-code.json verification.blockers')
        if any(states[c]['active'] != 'active' for c in wanted):
            raise ValueError('An expected service is not running')
        if hasattr(control, 'is_enabled'):
            for component, original in before.items():
                if 'enabled' in original and control.is_enabled(services.unit_name(instance, component)) != original['enabled']:
                    raise ValueError('Service autostart preference changed during update: ' + component)
        if 'web' in wanted:
            deadline = time.monotonic() + 180
            while True:
                health = observe(instance)
                checks = [c for c in health['checks'] if not c.get('component') or c['component'] in wanted]
                if all(c['ok'] for c in checks):
                    break
                if time.monotonic() >= deadline:
                    raise ValueError('Updated instance health checks failed')
                time.sleep(1)
            record['health'] = checks
        record['status'] = 'updated'
        record['completed'].append('verification')
        save('complete')
        return record
    except Exception as exc:
        if not plan_only:
            record['status'] = 'failed'
            # Errors may contain credentials. The receipt names the type and
            # phase; the exception is handled by the installer's protected log.
            record['error'] = type(exc).__name__
            save(record.get('phase', 'preflight'))
        raise
    finally:
        if not database_wanted:
            control.stop(services.unit_name(instance, 'postgres'))
