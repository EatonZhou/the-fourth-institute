"""macOS launchd agents for one instance's registered components."""
from __future__ import annotations

import json
import plistlib
import os
from pathlib import Path
import re
import subprocess
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


def launch_control_job(instance, record, arguments, environment, log):
    """Bootstrap an independent, single-run job with its own explicit environment.

    launchctl submit neither inherits its caller's environment nor disables
    KeepAlive. A failed operation must remain failed until the user retries it.
    """
    label = record["unit"]
    if not re.fullmatch(r"tfi-control-[0-9a-f]{32}", label):
        raise ValueError("Invalid instance control job identity")
    directory = instance.directory("runtime") / "control-jobs"
    if directory.is_symlink():
        raise ValueError("Instance control job directory is redirected")
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (label + ".plist")
    if path.exists() or path.is_symlink():
        raise ValueError("Instance control job already exists")
    definition = {
        "Label": label, "ProgramArguments": list(arguments),
        "EnvironmentVariables": dict(environment),
        "WorkingDirectory": str(instance.code_root()),
        "RunAtLoad": True, "KeepAlive": False,
        "StandardOutPath": str(log), "StandardErrorPath": str(log),
    }
    _publish(path, plistlib.dumps(definition).decode("utf-8"))
    try:
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
                       check=True, capture_output=True, timeout=45)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def remove_control_jobs(instance):
    """Remove this instance's finished jobs before its definitions/root retire."""
    directory = instance.directory("runtime") / "control-jobs"
    if directory.is_symlink():
        raise ValueError("Instance control job directory is redirected")
    if not directory.exists():
        return
    record_path = instance.directory("runtime") / "portal-control.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    jobs = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or path.suffix != ".plist":
            raise ValueError("Invalid instance control job definition")
        definition = plistlib.loads(path.read_bytes())
        label = definition.get("Label", "")
        if (not re.fullmatch(r"tfi-control-[0-9a-f]{32}", label)
                or path.stem != label
                or definition.get("EnvironmentVariables", {}).get("PORTAL_INSTANCE_ROOT") != str(instance.root)):
            raise ValueError("Control job belongs to another instance")
        if record.get("unit") == label and record.get("status") in {"queued", "running"}:
            raise RuntimeError("Instance control job is still running: " + label)
        target = f"gui/{os.getuid()}/{label}"
        state = subprocess.run(["launchctl", "print", target], capture_output=True, text=True, timeout=10)
        if state.returncode == 0 and re.search(r"(?m)^\s*pid\s*=\s*[1-9][0-9]*", state.stdout):
            raise RuntimeError("Instance control job is still running: " + label)
        jobs.append((path, target, state.returncode == 0))
    for path, target, loaded in jobs:
        if loaded:
            subprocess.run(["launchctl", "bootout", target], check=True, capture_output=True, timeout=30)
        path.unlink()
    directory.rmdir()
