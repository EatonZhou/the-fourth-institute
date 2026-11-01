"""Offline unit replacement for the one-time instance manifest conversion."""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from scripts.instance_service_definitions import render, missing_launchers


@dataclass(frozen=True)
class Replacement:
    path: Path
    content: str | None
    previous: str | None
    unit_name: str = ''
    enabled: bool = False
    enable_after: bool = False


def _environment(text):
    environment = {}
    for line in text.splitlines():
        if line.startswith('Environment='):
            for assignment in shlex.split(line.partition('=')[2]):
                key, _, value = assignment.partition('=')
                environment[key] = value.replace('%%', '%')
    return environment


def _owned(instance, text):
    environment = _environment(text)
    if (environment.get('PORTAL_INSTANCE_ROOT') == str(instance.root) or
            (environment.get('PORTAL_PATHS_CONFIG') in {str(instance.root / 'config/target.json'), str(instance.root / 'config/paths.json')}
             and environment.get('HOME') == str(instance.root / 'home'))):
        return True
    # The former paired router selected the instance as an explicit CLI argument.
    starts = [shlex.split(line.partition('=')[2]) for line in text.splitlines() if line.startswith('ExecStart=')]
    if len(starts) != 1:
        return False
    command = starts[0]
    code_roots = [Path(d['code_root']) for d in instance.deployments.values()]
    paired = (len(command) == 5
              and command[1] in {str(code / 'scripts/tfi_development_runtime.py') for code in code_roots}
              and command[2] in {'router', 'web'} and command[3:] == ['--root', str(instance.root)])
    installed = (len(command) == 7
                 and command[1] in {str(code / 'scripts/tfi_release_install.py') for code in code_roots}
                 and command[2:6] == ['_component', '--root', str(instance.root), '--component']
                 and command[6] in instance.document['services']['components'])
    return paired or installed


def plan(instance, *, obsolete_units=(), renamed_units=None) -> list[Replacement]:
    if sys.platform != 'linux':
        raise NotImplementedError('Offline unit conversion requires a native service ownership check on this platform')
    missing = missing_launchers(instance)
    if missing:
        raise ValueError('Complete component launchers before conversion: ' + ', '.join(missing))
    from scripts import tfi_release_platform_linux as adapter
    directory = adapter.unit_directory()
    if directory != directory.resolve():
        raise ValueError('User unit directory is redirected')
    replacements = []
    definitions = render(instance, 'linux')
    renamed_units = renamed_units or {}
    if (set(obsolete_units) & definitions.keys() or set(renamed_units) - set(obsolete_units)
            or set(renamed_units.values()) - definitions.keys()):
        raise ValueError('Historical service retirement and rename inventory is inconsistent')
    for name, content in definitions.items():
        path = directory / name
        previous = None
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise ValueError('Existing service definition is redirected: ' + name)
            previous = path.read_text(encoding='utf-8')
            if not _owned(instance, previous):
                raise ValueError('Existing service belongs to another instance: ' + name)
        observed = subprocess.run(['systemctl', '--user', 'show', name,
                                   '--property=LoadState,FragmentPath,DropInPaths,UnitFileState'],
                                  check=False, capture_output=True, text=True, timeout=30)
        state = dict(line.split('=', 1) for line in observed.stdout.splitlines() if '=' in line)
        if state.get('LoadState') == 'not-found' and previous is None:
            pass
        else:
            fragment = Path(state.get('FragmentPath', ''))
            template = directory / (instance.service_prefix + '-codex-app-server@.service')
            from_template = (previous is None and name.startswith(instance.service_prefix + '-codex-app-server@')
                             and fragment == template and template.is_file() and not template.is_symlink())
            if from_template:
                environment = _environment(template.read_text(encoding='utf-8'))
                from_template = (environment.get('PORTAL_INSTANCE_ROOT') == str(instance.root) or
                                 (environment.get('PORTAL_PATHS_CONFIG') in {str(instance.root / 'config/target.json'), str(instance.root / 'config/paths.json')}
                                  and environment.get('HOME') == str(instance.root / 'home')))
            if observed.returncode or state.get('DropInPaths') or (fragment != path and not from_template):
                raise ValueError('Loaded service definition has foreign ownership or overrides: ' + name)
        replacements.append(Replacement(path, content, previous, name, state.get('UnitFileState') == 'enabled'))
    for name in obsolete_units:
        if '/' in name or not name.endswith('.service') or any(c.isspace() for c in name):
            raise ValueError('Invalid historical service name')
        path = directory / name
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file() or not _owned(instance, path.read_text()):
            raise ValueError('Obsolete service does not belong to this instance: ' + name)
        result = subprocess.run(['systemctl', '--user', 'show', name,
                                 '--property=FragmentPath,DropInPaths,UnitFileState'],
                                check=True, capture_output=True, text=True, timeout=30)
        state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if state.get('FragmentPath') != str(path):
            raise ValueError('Obsolete service has a foreign loaded definition')
        for dropin in shlex.split(state.get('DropInPaths', '')):
            file = Path(dropin)
            if file != directory / (name + '.d') / 'tfi-development.conf' or file.is_symlink():
                raise ValueError('Obsolete service has unrecognized overrides')
            content = file.read_text()
            settings = _environment(content)
            expected = {'PORTAL_DEPLOYMENT_REGISTRY_PATH': str(instance.root / 'config/deployments.json'),
                        'PORTAL_SHARED_SOURCE_ROOT': str(instance.code_root())}
            directives = [line.partition('=')[0] for line in content.splitlines() if '=' in line and not line.startswith('#')]
            if settings != expected or set(directives) != {'Environment'}:
                raise ValueError('Obsolete gateway override does not match this instance')
            replacements.append(Replacement(file, None, content))
        replacements.append(Replacement(path, None, path.read_text(), name, state.get('UnitFileState') == 'enabled'))
        if name in renamed_units and state.get('UnitFileState') == 'enabled':
            replacements = [replace(item, enable_after=True) if item.unit_name == renamed_units[name]
                            else item for item in replacements]
    template = directory / (instance.service_prefix + '-codex-app-server@.service')
    if template.exists():
        if template.is_symlink() or not _owned(instance, template.read_text()):
            raise ValueError('Historical Codex template does not belong to this instance')
        replacements.append(Replacement(template, None, template.read_text()))
    return replacements


def _write(path, text):
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def apply(replacements):
    applied = []
    try:
        for item in replacements:
            current = item.path.read_text(encoding='utf-8') if item.path.exists() else None
            if item.path.is_symlink() or current != item.previous:
                raise ValueError('Service definition changed after preflight: ' + item.path.name)
            applied.append(item)
            if item.content is None:
                if item.unit_name and item.enabled:
                    subprocess.run(['systemctl', '--user', 'disable', item.unit_name], check=True,
                                   capture_output=True, text=True, timeout=30)
                item.path.unlink()
            else:
                _write(item.path, item.content)
        if replacements:
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True,
                           capture_output=True, text=True, timeout=30)
            enabled = [item.unit_name for item in replacements
                       if item.content is not None and (item.enabled or item.enable_after)]
            if enabled:
                subprocess.run(['systemctl', '--user', 'reenable', *enabled], check=True,
                               capture_output=True, text=True, timeout=30)
    except BaseException:
        rollback(applied)
        raise


def rollback(replacements):
    newly_enabled = [item.unit_name for item in replacements if item.enable_after and not item.enabled]
    if newly_enabled:
        subprocess.run(['systemctl', '--user', 'disable', *newly_enabled], check=True,
                       capture_output=True, text=True, timeout=30)
    for item in reversed(replacements):
        if item.previous is None:
            item.path.unlink(missing_ok=True)
        else:
            _write(item.path, item.previous)
    if replacements:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True,
                       capture_output=True, text=True, timeout=30)
        enabled = [item.unit_name for item in replacements if item.enabled]
        if enabled:
            subprocess.run(['systemctl', '--user', 'reenable', *enabled], check=True,
                           capture_output=True, text=True, timeout=30)
