"""macOS launchd assets for an isolated TFI installation."""
from __future__ import annotations

import plistlib
from pathlib import Path

from scripts.tfi_instance_layout import code_root, installer_path


def safe_path(value):
    if not isinstance(value, str) or not Path(value).is_absolute() or any(c in value for c in "\0\n\r"):
        raise ValueError("Target paths must be absolute and contain no control characters")
    return Path(value).resolve()


def service_controller():
    from scripts.portal_service_control import ServiceControl
    return ServiceControl(platform="macos")


def unit_name(target, component):
    return f"{target['service_prefix']}-{component}.service"


def unit_directory():
    return Path.home() / "Library" / "LaunchAgents"


def unit_path(directory, name):
    from scripts.portal_service_control import ServiceControl
    return directory / f"{ServiceControl.label(name)}.plist"


def unit_text(target, component, components):
    del components
    root = Path(target["install_root"])
    name = unit_name(target, component)
    return command_unit_text(name, [target["python"], str(installer_path(target)), "_component",
                                    "--root", str(root), "--component", component],
                             cwd=code_root(target), log_root=root / "runtime", log_name=component)


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
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name, content in units.items():
            path = unit_path(directory, name)
            if path.exists() or path.is_symlink():
                if path.is_symlink() or path.read_text(encoding="utf-8") != content:
                    raise ValueError(f"Refusing to replace another launchd job: {name}")
                continue
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
            created.append(name)
    except Exception:
        for name in created:
            unit_path(directory, name).unlink(missing_ok=True)
        raise
    return created


def remove_services(directory, names):
    for name in names:
        path = unit_path(directory, name)
        if path.is_symlink():
            raise ValueError(f"Refusing to remove symlinked launchd job: {name}")
        path.unlink(missing_ok=True)
