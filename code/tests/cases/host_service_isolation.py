"""Keep disposable test instances off the host user's service manager.

Service tests must redirect definition directories and replace their command
runner explicitly. Read-only systemctl queries remain available for probes.
"""
import os
from pathlib import Path
import shlex
import subprocess


def protect_host_services(monkeypatch):
    from scripts import tfi_release_platform_linux as linux

    host_directories = {
        (Path.home() / '.config/systemd/user').resolve(),
        (Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user').resolve(),
    }
    register = linux.register_services
    popen = subprocess.Popen

    def isolated_register(directory, units):
        target = Path(directory).resolve()
        if any(target == host or host in target.parents for host in host_directories):
            raise RuntimeError('Tests cannot install host systemd user services; redirect the unit directory')
        return register(directory, units)

    def isolated_popen(args, *positional, **options):
        words = shlex.split(args) if isinstance(args, str) else list(args)
        if words and Path(os.fsdecode(words[0])).name == 'systemctl' and '--user' in words:
            readonly = {'show', 'status', 'is-active', 'is-enabled', 'is-failed',
                        'list-units', 'list-unit-files', 'cat', 'help', '--version'}
            verb = next((os.fsdecode(word) for word in words[1:]
                         if not os.fsdecode(word).startswith('-')), '')
            if verb not in readonly:
                raise RuntimeError('Tests cannot modify host systemd user services; mock the command runner')
        return popen(args, *positional, **options)

    monkeypatch.setattr(linux, 'register_services', isolated_register)
    monkeypatch.setattr(subprocess, 'Popen', isolated_popen)
