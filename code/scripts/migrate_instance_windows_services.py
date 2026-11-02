"""Offline conversion of historical Windows current-user service definitions.

The old installer stops its own processes. This module verifies their ownership
and native creation times before moving definitions to the instance runtime.
It never stops processes or changes SCM registrations.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def legacy_directory():
    configured = os.environ.get('TFI_WINDOWS_SERVICE_DIRECTORY')
    return Path(configured) if configured else Path(os.environ['LOCALAPPDATA']) / 'TFI/services'


def definitions(root, target):
    directory = legacy_directory()
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValueError('Historical service directory is redirected')
    if target.get('windows_service_mode') != 'user':
        raise ValueError('Windows conversion requires current-user services; SCM conversion is unsupported')
    result = []
    for path in sorted(directory.glob(target['service_prefix'] + '-*.json')):
        if path.is_symlink():
            raise ValueError('Historical service definition is redirected')
        text = path.read_text(encoding='utf-8')
        value = json.loads(text)
        component = value.get('component', '')
        if not component or '/' in component or '\\' in component:
            raise ValueError('Invalid historical service component')
        expected = dict(mode='user', name=target['service_prefix'] + '-' + component,
                        root=str(root), python=target['python'],
                        code=target['shared_code_root'], component=component)
        if value != expected or path.stem != expected['name']:
            raise ValueError('Historical service ownership mismatch: ' + path.name)
        result.append((path, text, component))
    if not result:
        raise ValueError('Historical service definitions are missing')
    return result


def require_stopped(root, target):
    from scripts.tfi_windows_user_services import process_stamp, read_record
    definitions(root, target)
    # Includes dynamically registered Provider supervisors, not just core units.
    for path in (root / 'runtime').glob('*.process.json'):
        if path.is_symlink():
            raise ValueError('Process record is redirected')
        record = json.loads(read_record(path))
        for pid_key, stamp_key in [('pid', 'created'), ('child_pid', 'child_created')]:
            pid = record.get(pid_key)
            if not pid:
                continue
            stamp = process_stamp(pid)
            if stamp is not None and (record.get(stamp_key) is None or stamp == record[stamp_key]):
                raise ValueError('Instance process must be stopped before conversion: ' + path.name)


def plan(instance):
    from scripts.migrate_instance_units import Replacement
    from scripts.instance_service_definitions import render, missing_launchers
    target = json.loads((instance.root / 'config/target.json').read_text(encoding='utf-8'))
    old = definitions(instance.root, target)
    if {component for _, _, component in old} != set(instance.document['services']['components']):
        raise ValueError('Historical and proposed service inventories differ')
    if missing_launchers(instance):
        raise ValueError('Complete component launchers before conversion')
    directory = instance.directory('runtime') / 'service-units'
    if directory.is_symlink() or directory.exists() and any(directory.iterdir()):
        raise ValueError('New service definition directory is occupied')
    return ([Replacement(path, None, text) for path, text, _ in old] +
            [Replacement(directory / name, text, None)
             for name, text in render(instance, 'windows').items()])


def apply(replacements):
    from scripts.tfi_windows_user_services import read_record, write_record, remove_record
    applied = []
    try:
        for item in replacements:
            current = read_record(item.path) if item.path.exists() else None
            if item.path.is_symlink() or current != item.previous:
                raise ValueError('Service definition changed after preflight: ' + item.path.name)
        for item in replacements:
            applied.append(item)
            if item.content is None:
                remove_record(item.path)
            else:
                item.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                write_record(item.path, item.content)
    except BaseException:
        rollback(applied)
        raise


def rollback(replacements):
    from scripts.tfi_windows_user_services import write_record, remove_record
    for item in reversed(replacements):
        if item.previous is None:
            remove_record(item.path)
        else:
            write_record(item.path, item.previous)


def require_offline(instance, *, running=()):
    """Check manifest-owned services and all retained process identities."""
    from scripts.instance_services import controller
    from scripts.tfi_windows_user_services import process_stamp, read_record
    control = controller(instance)
    allowed = set(running)
    pids = {}
    for component in instance.document['services']['components']:
        state = control.status(instance.service_name(component))
        if component in allowed:
            if state.get('active') != 'active' or not state.get('pid'):
                raise RuntimeError('Required service is not running: ' + component)
            pids[component] = str(state['pid'])
        elif state.get('active') not in {'inactive', 'failed'} or state.get('pid'):
            raise RuntimeError('Stop the instance services first: ' + component)
    if set(pids) != allowed:
        raise RuntimeError('Required service is not registered')
    for path in instance.directory('runtime').glob('*.process.json'):
        if path.name in {component + '.process.json' for component in allowed}:
            continue
        if path.is_symlink():
            raise ValueError('Process record is redirected')
        record = json.loads(read_record(path))
        for pid_key, stamp_key in [('pid', 'created'), ('child_pid', 'child_created')]:
            if record.get(pid_key):
                stamp = process_stamp(record[pid_key])
                if stamp is not None and (record.get(stamp_key) is None or stamp == record[stamp_key]):
                    raise RuntimeError('Stop remaining instance processes: ' + path.name)
    return pids
