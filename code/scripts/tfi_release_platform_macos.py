"""macOS launchd agents for one instance's registered components."""
from __future__ import annotations

import plistlib
import os
from pathlib import Path
import tempfile

from management_common.code_environment import runtime_python


def service_controller():
    from scripts.portal_service_control import ServiceControl
    return ServiceControl(platform="macos", agent_root=unit_directory())


def unit_directory():
    # launchd belongs to the logged-in OS user, read from its passwd entry.
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library" / "LaunchAgents"


def unit_path(directory, name):
    from scripts.portal_service_control import ServiceControl
    return directory / f"{ServiceControl.label(name)}.plist"


def manifest_unit_text(instance, component):
    code = instance.service_code(component)
    name = instance.service_name(component) + '.service'
    return command_unit_text(name, [str(runtime_python(code)), '-m', 'management_portal.service_runtime',
                                    '--component', component],
                             cwd=code, log_root=instance.directory('runtime'), log_name=component,
                             environment=instance.service_environment(component))


def command_unit_text(name, command, *, cwd, log_root, log_name, environment=None):
    from scripts.portal_service_control import ServiceControl
    definition = {
        "Label": ServiceControl.label(name),
        "ProgramArguments": [str(part) for part in command],
        "WorkingDirectory": str(cwd),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(Path(log_root) / f"{log_name}.log"),
        "StandardErrorPath": str(Path(log_root) / f"{log_name}.error.log"),
    }
    if environment:
        definition["EnvironmentVariables"] = {str(k): str(v) for k, v in environment.items()}
    return plistlib.dumps(definition, sort_keys=True).decode("utf-8")


def register_services(directory, units):
    """Write the given agents; the caller has verified their ownership.

    A loaded agent keeps its old definition until it is restarted.
    """
    directory.mkdir(parents=True, exist_ok=True)
    previous = {}
    try:
        for name, content in units.items():
            path = unit_path(directory, name)
            if path.is_symlink():
                raise ValueError(f"Refusing a linked launchd job: {name}")
            previous[name] = path.read_text(encoding="utf-8") if path.exists() else None
            if previous[name] != content:
                _publish(path, content)
    except Exception:
        for name, content in previous.items():
            if content is None:
                unit_path(directory, name).unlink(missing_ok=True)
            else:
                _publish(unit_path(directory, name), content)
        raise
    return [name for name, content in previous.items() if content is None]


def _publish(path, content):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix="." + path.name + ".",
                                     delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def remove_services(directory, names):
    for name in names:
        path = unit_path(directory, name)
        if path.is_symlink():
            raise ValueError(f"Refusing to remove symlinked launchd job: {name}")
        path.unlink(missing_ok=True)
