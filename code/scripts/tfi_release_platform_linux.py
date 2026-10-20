"""Linux installation assets; shared orchestration owns the instance topology."""
from pathlib import Path
import json
import subprocess

from scripts.tfi_instance_layout import code_root, installer_path


def safe_path(value):
    if not isinstance(value, str) or not Path(value).is_absolute() or any(c in value for c in "\0\n\r%$'\"\\"):
        raise ValueError("Target paths must be absolute and contain no control/systemd expansion characters")
    return Path(value).resolve()


def service_controller():
    from scripts.portal_service_control import ServiceControl
    return ServiceControl(platform="linux")


def unit_name(target, component):
    return f"{target['service_prefix']}-{component}.service"


def unit_directory():
    return Path.home() / ".config/systemd/user"


def unit_path(directory, name):
    return directory / name


def unit_text(target, component, components):
    root = Path(target["install_root"])
    arguments = [target["python"], str(installer_path(target)), "_component",
                 "--root", str(root), "--component", component]
    exec_start = " ".join(json.dumps(x) for x in arguments)
    after = " ".join(unit_name(target, c) for c in components[:components.index(component)])
    working_directory = str(code_root(target))
    if ' ' in working_directory:
        working_directory = json.dumps(working_directory)
    # NATS 2.10 treats SIGTERM as failure; SIGINT is its clean shutdown path.
    stop_signal = "KillSignal=SIGINT\n" if component == "nats" else ""
    return (f"[Unit]\nDescription=TFI isolated {component} ({target['run_id']})\nAfter={after}\n"
            f"[Service]\nType=simple\nUMask=0077\nWorkingDirectory={working_directory}\n"
            f"ExecStart={exec_start}\nRestart=on-failure\nRestartSec=3\nKillMode=control-group\n"
            f"{stop_signal}TimeoutStopSec=90\n[Install]\nWantedBy=default.target\n")


def register_services(directory, units):
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name, content in units.items():
            path = directory / name
            if path.exists() or path.is_symlink():
                if path.is_symlink() or path.read_text() != content:
                    raise ValueError(f"Refusing to replace another service: {path.name}")
            else:
                with path.open("x") as stream:
                    stream.write(content)
                created.append(name)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
    except Exception:
        for name in created:
            (directory / name).unlink(missing_ok=True)
        if created:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
        raise
    return created


def remove_services(directory, names):
    for name in names:
        path = directory / name
        if path.exists():
            subprocess.run(["systemctl", "--user", "disable", name], capture_output=True, check=True)
            path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=True)
