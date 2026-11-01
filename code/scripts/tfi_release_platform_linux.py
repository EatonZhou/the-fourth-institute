"""Linux systemd user units for one instance's registered components."""
from pathlib import Path
import json
import shlex
import subprocess
import tempfile

from management_common.code_environment import runtime_python


def service_controller():
    from scripts.portal_service_control import ServiceControl
    return ServiceControl(platform="linux")


def unit_directory():
    # Register where the existing user manager actually searches; a unit file
    # beneath another directory can exist on disk while the service is unknown.
    result = subprocess.run(
        ['systemctl', '--user', 'show', '--property=UnitPath', '--value'],
        check=True, capture_output=True, text=True, timeout=15)
    candidates = [Path(value) for value in shlex.split(result.stdout)
                  if Path(value).is_absolute() and Path(value).parts[-2:] == ('systemd', 'user')]
    if not candidates:
        raise RuntimeError('User service manager did not report its unit directory')
    return candidates[0]


def unit_path(directory, name):
    return directory / name


# Services on the request path run ahead of agent shells (default weight 100)
# and test runs (management_portal.test_framework.executor, weight 20).
REQUEST_PATH_CPU_WEIGHT = "CPUWeight=1000\n"


def manifest_unit_text(instance, component):
    code = instance.service_code(component)
    arguments = [str(runtime_python(code)), '-m', 'management_portal.service_runtime',
                 '--component', component]
    exec_start = " ".join(json.dumps(x, ensure_ascii=False).replace('%', '%%').replace('$', '$$') for x in arguments)
    from scripts.instance_service_definitions import dependencies
    after = " ".join(instance.service_name(c) + '.service' for c in dependencies(instance, component))
    working_directory = str(code).replace('%', '%%')
    # NATS 2.10 treats SIGTERM as failure; SIGINT is its clean shutdown path.
    stop_signal = "KillSignal=SIGINT\n" if component == "nats" else ""
    # The Worker Manager runs Provider adapters, which keep the default weight.
    weight = "" if component == "worker" else REQUEST_PATH_CPU_WEIGHT
    restart = 'always' if component.startswith('codex-app-server@') else 'on-failure'
    limits = 'MemoryHigh=32G\nMemoryMax=36G\nMemorySwapMax=1G\n' if component.startswith('codex-app-server@') else ''
    environment = ''.join('Environment=' + json.dumps(key + '=' + value, ensure_ascii=False).replace('%', '%%') + '\n'
                          for key, value in instance.service_environment(component).items())
    return (f"[Unit]\nDescription=TFI {component} ({instance.name})\nAfter={after}\n"
            f"[Service]\nType=simple\nUMask=0077\nWorkingDirectory={working_directory}\n"
            f"{environment}"
            f"ExecStart={exec_start}\nRestart={restart}\nRestartSec=3\nKillMode=control-group\n"
            f"{stop_signal}{weight}{limits}TimeoutStopSec=270\n[Install]\nWantedBy=default.target\n")


def _publish(path, content):
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.' + path.name + '.',
                                     delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def register_services(directory, units):
    """Write the given definitions; the caller has verified their ownership.

    systemd applies changed definitions at daemon-reload; running services keep
    their process until restarted. A failure restores every previous file.
    """
    directory.mkdir(parents=True, exist_ok=True)
    previous = {}
    try:
        for name, content in units.items():
            path = unit_path(directory, name)
            if path.is_symlink():
                raise ValueError(f"Refusing a linked service definition: {name}")
            previous[name] = path.read_text(encoding='utf-8') if path.exists() else None
            if previous[name] != content:
                _publish(path, content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
    except Exception:
        for name, content in previous.items():
            path = unit_path(directory, name)
            if content is None:
                path.unlink(missing_ok=True)
            else:
                _publish(path, content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
        raise
    return [name for name, content in previous.items() if content is None]


def remove_services(directory, names):
    for name in names:
        path = unit_path(directory, name)
        if path.is_symlink():
            raise ValueError(f"Refusing a linked service definition: {name}")
        if path.exists():
            subprocess.run(["systemctl", "--user", "disable", name], capture_output=True, check=True)
            path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=True)
    if names:
        # A unit stopped by force stays listed as failed until its state is reset.
        subprocess.run(["systemctl", "--user", "reset-failed", *names], capture_output=True, check=False)
