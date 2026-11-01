"""Service lifecycle for one manifest-selected instance.

The OS service adapter controls processes. This module selects the instance's
complete registered inventory and verifies definitions before acting on it.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib
import json
import plistlib
import shlex
import os
from pathlib import Path
import sys
import subprocess
import time

from management_common.instance_manifest import load
from management_common import host_instances
from scripts.instance_service_definitions import components, render

# Transient jobs the code starts for an instance, under its service prefix.
# They are owned like registered components: stop includes them after their
# environment proves the same instance; startup never starts them.
ON_DEMAND_JOBS = ('provider-activation',)
CREATION_RECORD = 'runtime/creation.json'


def wait_database(instance, timeout=60):
    from management_portal.service_runtime import clean_environment
    program = Path(instance.document['dependencies']['postgres']['bin']) / ('pg_isready.exe' if os.name == 'nt' else 'pg_isready')
    host = '127.0.0.1' if os.name == 'nt' else str(instance.directory('runtime') / 's')
    command = [str(program), '-h', host, '-p', str(instance.document['network']['postgres_port'])]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(command, env=clean_environment(instance, postgres=True),
                                    capture_output=True, timeout=min(5, max(0.1, deadline - time.monotonic())))
            if result.returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.25)
    raise RuntimeError('Instance PostgreSQL did not become ready before starting its clients')


def platform_name():
    if sys.platform == 'win32':
        return 'windows'
    if sys.platform == 'darwin':
        return 'macos'
    if sys.platform.startswith('linux'):
        return 'linux'
    raise ValueError('Unsupported instance service platform')


def adapter():
    return importlib.import_module('scripts.tfi_release_platform_' + platform_name())


def unit_name(instance, component):
    return instance.service_name(component) + ('' if platform_name() == 'windows' else '.service')


def job_unit(instance, job):
    if job not in ON_DEMAND_JOBS:
        raise ValueError('Unknown instance job')
    return f'{instance.service_prefix}-{job}.service'


def owned_units(instance):
    """Every service name this instance may run: components and its jobs."""
    names = {unit_name(instance, c) for c in components(instance)}
    if platform_name() == 'linux':
        names |= {job_unit(instance, job) for job in ON_DEMAND_JOBS}
    return names


def definition_directory(instance):
    if platform_name() == 'windows':
        return instance.directory('runtime') / 'service-units'
    return adapter().unit_directory()


def controller(instance):
    if platform_name() == 'windows':
        from scripts.tfi_windows_user_services import WindowsServiceControl
        return WindowsServiceControl(definition_directory(instance))
    return adapter().service_controller()


def install_definitions(instance, selected_components=None):
    """Create or update definitions of registered components.

    A definition may be replaced only when it already selects this instance;
    one that belongs elsewhere, or a loaded unit without a definition here, is
    refused. Running services keep their processes until restarted.
    """
    platform = platform_name()
    native = adapter()
    directory = definition_directory(instance)
    control = controller(instance)
    selected = components(instance) if selected_components is None else tuple(selected_components)
    if set(selected) - set(components(instance)):
        raise ValueError('Cannot define unregistered service components')
    units = {}
    for component in selected:
        name = unit_name(instance, component)
        path = native.unit_path(directory, name)
        if path.is_symlink():
            raise ValueError('Instance service definition is redirected: ' + path.name)
        if path.exists():
            if definition_root(path.read_text(encoding='utf-8'), platform) != str(instance.root):
                raise ValueError('Service belongs to another instance: ' + name)
        elif platform == 'linux' and control.status(name)['load'] != 'not-found':
            raise ValueError('Service identity is already loaded from elsewhere: ' + name)
        units[name] = native.manifest_unit_text(instance, component)
    return native.register_services(directory, units)


def remove_definitions(instance, selected_components=None):
    """Delete this instance's stopped definitions; foreign ones are refused."""
    platform = platform_name()
    native = adapter()
    if platform == 'macos' and selected_components is None:
        native.remove_control_jobs(instance)
    directory = definition_directory(instance)
    control = controller(instance)
    selected = components(instance) if selected_components is None else tuple(selected_components)
    names = []
    for component in selected:
        name = unit_name(instance, component)
        path = native.unit_path(directory, name)
        if path.is_symlink():
            raise ValueError('Instance service definition is redirected: ' + path.name)
        if not path.exists():
            continue
        if definition_root(path.read_text(encoding='utf-8'), platform) != str(instance.root):
            raise ValueError('Service belongs to another instance: ' + name)
        state = control.status(name)
        if state['pid'] or state['active'] not in {'inactive', 'failed'}:
            raise RuntimeError('Stop the service before removing its definition: ' + name)
        names.append(name)
    if names:
        native.remove_services(directory, names)
    return names


@contextmanager
def configuration_lock(root):
    with host_instances.operation_lock(root, '.configuration.lock') as acquired:
        if not acquired:
            raise BlockingIOError('Another configuration or lifecycle operation owns this instance')
        yield


def status(instance):
    control = controller(instance)
    return {component: control.status(unit_name(instance, component)) for component in components(instance)}


def definition_root(text, platform):
    if platform == 'macos':
        return plistlib.loads(text.encode()).get('EnvironmentVariables', {}).get('PORTAL_INSTANCE_ROOT')
    if platform == 'windows':
        value = json.loads(text)
        if value.get('mode') == 'user':
            return value.get('environment', {}).get('PORTAL_INSTANCE_ROOT')
        arguments = [part.strip('"') for part in shlex.split(value.get('binary', ''), posix=False)]
        if '--root' in arguments:
            return arguments[arguments.index('--root') + 1]
        return None
    environment = {}
    for line in text.splitlines():
        if line.startswith('Environment='):
            for item in shlex.split(line.partition('=')[2]):
                key, _, value = item.partition('=')
                environment[key] = value.replace('%%', '%')
    return environment.get('PORTAL_INSTANCE_ROOT')


def verify_definitions(instance, *, exact=True, selected_components=None):
    platform = platform_name()
    native = adapter()
    directory = definition_directory(instance)
    expected = render(instance, platform) if exact else {}
    control = controller(instance)
    selected = components(instance) if selected_components is None else tuple(selected_components)
    if set(selected) - set(components(instance)):
        raise ValueError('Cannot verify unregistered service components')
    for component in selected:
        name = unit_name(instance, component)
        path = native.unit_path(directory, name)
        if not exact and not path.exists() and control.status(name)['load'] == 'not-found':
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError('Instance service definition is missing or redirected: ' + path.name)
        content = path.read_text(encoding='utf-8')
        if definition_root(content, platform) != str(instance.root):
            raise ValueError('Service belongs to another instance: ' + path.name)
        if exact and content != expected[path.name]:
            raise ValueError('Instance service definition differs from its manifest: ' + path.name)
        if platform == 'linux':
            result = control.run('systemctl', '--user', 'show', name,
                                 '--property=FragmentPath,DropInPaths', check=True)
            properties = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            if properties.get('FragmentPath') != str(path) or properties.get('DropInPaths'):
                raise ValueError('Loaded instance service has a different definition or overrides: ' + name)
        elif platform == 'macos':
            # A plist on disk is not proof of the job launchd has loaded.
            loaded = control.run('launchctl', 'print', control._launch_target(name), check=False)
            sources = [line.strip().removeprefix('path = ') for line in (loaded.stdout or '').splitlines()
                       if line.strip().startswith('path = ')]
            if loaded.returncode == 0 and sources != [str(path)]:
                raise ValueError('Loaded launchd job comes from a different definition: ' + name)
    return directory


def creation_record(instance):
    """The installer's record of a creation that has not completed, or None."""
    path = instance.root / CREATION_RECORD
    if path.is_symlink():
        raise ValueError('Instance creation record cannot be redirected')
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def start_blocker(instance):
    area = instance.root / 'runtime/area-relocation.json'
    if area.is_symlink():
        raise ValueError('Area relocation record cannot be redirected')
    if area.is_file() and json.loads(area.read_text()).get('status') != 'completed':
        return 'Area relocation has not completed; repeat its area move with the same target'
    relocation = instance.root / 'runtime/instance-relocation.json'
    if relocation.is_symlink():
        raise ValueError('Instance relocation record cannot be redirected')
    if relocation.is_file() and json.loads(relocation.read_text()).get('status') != 'completed':
        return 'Instance relocation has not completed; repeat relocate with its target root'
    record = creation_record(instance)
    if record is not None:
        return 'Instance creation has not completed: ' + str(record.get('error') or record.get('phase'))
    from scripts.instance_service_definitions import missing_launchers
    missing = missing_launchers(instance)
    return 'Missing component launchers: ' + ', '.join(missing) if missing else ''


def lifecycle(action, root, *, shutdown_timeout=20.0, excluded_components=(), creating=False):
    if action not in {'status', 'start', 'stop'}:
        raise ValueError('Unsupported instance service action')
    instance = load(root)
    if set(excluded_components) - set(components(instance)):
        raise ValueError('Excluded components must be registered')
    if excluded_components and action != 'start':
        raise ValueError('Only startup accepts excluded components')
    if action == 'status':
        states = status(instance)
        return {'instance_id': instance.instance_id, 'name': instance.name, 'services': states,
                'status': 'healthy' if all(row['active'] == 'active' for row in states.values()) else 'unhealthy',
                'ok': all(row['active'] == 'active' for row in states.values())}
    if action == 'start' and not creating and start_blocker(instance):
        raise ValueError(start_blocker(instance))
    with configuration_lock(instance.root):
        verify_definitions(instance, exact=action == 'start')
        control = controller(instance)
        if action == 'stop' and platform_name() == 'linux':
            from scripts.tfi_shutdown import stop_instance
            return stop_instance(instance.root, timeout=shutdown_timeout)
        ordered = components(instance)
        skipped = set(excluded_components)
        if action == 'start':
            ordered = tuple(c for c in ordered if c not in skipped)
        if action == 'stop':
            ordered = tuple(reversed(ordered))
        for component in ordered:
            getattr(control, action)(unit_name(instance, component))
            if action == 'start' and component == 'postgres':
                wait_database(instance)
        states = status(instance)
        if action == 'stop' and any(row['active'] not in {'inactive', 'failed'} or row['pid'] for row in states.values()):
            raise RuntimeError('Instance services still have running processes')
        if action == 'start' and any(states[c]['active'] != 'active' for c in ordered):
            raise RuntimeError('Not all instance services started successfully')
        return {'status': 'started' if action == 'start' else 'stopped',
                'instance_id': instance.instance_id, 'services': states, 'skipped': sorted(skipped)}
