"""Discover installed instances and dispatch their existing installer lifecycle.

The HTTP process may itself be stopped. Control jobs therefore run outside its
service, using the service owner's environment rather than an instance's HOME.
"""
from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

CODE = Path(__file__).resolve().parents[1]
PLATFORM = CODE.parent
sys.path.insert(0, str(CODE))
sys.dont_write_bytecode = True
from management_common.file_lock import try_lock_descriptor
from scripts import tfi_release_install as installer


def supervisor_environment():
    """Never carry another instance's DB, HOME, sockets or service registry."""
    env = {k: v for k, v in os.environ.items()
           if k in {"PATH", "SystemRoot", "WINDIR", "LANG", "TMP", "TEMP"}}
    if os.name == "nt":
        import ctypes
        def folder(identifier):
            buffer = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, identifier, None, 0, buffer):
                raise RuntimeError("Cannot locate the service owner's Windows profile")
            return buffer.value
        env.update(USERPROFILE=folder(40), HOME=folder(40), LOCALAPPDATA=folder(28),
                   APPDATA=folder(26))
    else:
        import pwd
        owner = pwd.getpwuid(os.getuid())
        env.update(HOME=owner.pw_dir, USER=owner.pw_name, LOGNAME=owner.pw_name)
        if sys.platform.startswith("linux"):
            env.update(XDG_RUNTIME_DIR=f"/run/user/{os.getuid()}",
                       DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{os.getuid()}/bus")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def command(action, *, name=None, current=""):
    args = [sys.executable, str(Path(__file__).resolve()), action]
    if name is not None:
        args += ["--name", name]
    if current:
        args += ["--current", current]
    result = subprocess.run(args, env=supervisor_environment(), capture_output=True,
                            text=True, timeout=45)
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("Instance controller returned no valid result") from None
    if result.returncode:
        raise ValueError(value.get("error", "Instance operation failed"))
    return value


def instance_root(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        raise ValueError("Invalid instance name")
    base = PLATFORM / "instance"
    root = base / name
    if base.is_symlink() or root.is_symlink() or root.resolve().parent != base.resolve():
        raise ValueError("Instance must be a direct directory under instance/")
    if not root.is_dir() or (root / installer.STATE_FILE).is_symlink():
        raise ValueError("Instance is not installed here")
    return root


def state_for(name):
    root = instance_root(name)
    state = installer.owned_state(root)
    if Path(state["target"].get("shared_code_root", "")).resolve() != CODE:
        raise ValueError("Instance uses a different code directory")
    return root, state


def start_blocker(state):
    return installer.start_blocker(state)


def access_urls(target):
    bind, port = target["bind"], int(target["ports"]["web"])
    address = ipaddress.ip_address(bind)
    addresses = {bind}
    if address.is_unspecified:
        addresses = set()
        if sys.platform.startswith("linux"):
            # Hostname and default-route discovery miss secondary LAN/VPN
            # interfaces. Read the OS interface list, never the request Host.
            try:
                interfaces = subprocess.run(
                    ["ip", "-j", "address", "show", "up"], check=True,
                    capture_output=True, text=True, timeout=15)
                for interface in json.loads(interfaces.stdout):
                    addresses.update(info["local"] for info in interface.get("addr_info", [])
                                     if info.get("family") in {"inet", "inet6"} and info.get("local"))
            except (OSError, subprocess.SubprocessError, ValueError, TypeError):
                pass  # Keep portable hostname/route discovery if ip is absent.
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                addresses.update(item[4][0] for item in socket.getaddrinfo(
                    socket.gethostname(), None, family, socket.SOCK_STREAM))
            except OSError:
                pass
        # UDP connect only selects a local route; it does not send a packet.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 9))
                addresses.add(probe.getsockname()[0])
        except OSError:
            pass
        addresses = {a for a in addresses if not ipaddress.ip_address(a).is_loopback
                     and not ipaddress.ip_address(a).is_link_local
                     and not ipaddress.ip_address(a).is_unspecified
                     and (bind == "::" or ipaddress.ip_address(a).version == 4)}
    return [f"http://{'[' + a + ']' if ':' in a else a}:{port}/" for a in sorted(addresses)]


def control_path(root):
    runtime = root / "runtime"
    if runtime.is_symlink():
        raise ValueError("Instance runtime cannot be a symlink")
    runtime.mkdir(exist_ok=True, mode=0o700)
    path = runtime / "portal-control.json"
    if path.is_symlink():
        raise ValueError("Invalid instance control record")
    return path


def job_alive(record):
    if sys.platform.startswith("linux"):
        check = subprocess.run(
            ["systemctl", "--user", "show", record["unit"], "-p", "ActiveState", "--value"],
            capture_output=True, text=True, timeout=5)
        if check.returncode:
            raise RuntimeError("Cannot query instance control job")
        return check.stdout.strip() in {"active", "activating", "deactivating"}
    if sys.platform == "darwin":
        check = subprocess.run(["launchctl", "list", record["unit"]],
                               capture_output=True, text=True, timeout=5)
        return check.returncode == 0 and '"PID"' in check.stdout
    try:
        os.kill(int(record.get("pid", 0)), 0) if record.get("pid") else None
        return bool(record.get("pid"))
    except OSError:
        return False


def operation(root):
    path = control_path(root)
    if not path.exists():
        return None
    record = installer.read_json(path)
    if record["status"] in {"queued", "running"}:
        if not job_alive(record) and time.time() - record["created_at"] > 30:
            return {**record, "status": "failed", "error": "Instance control job was interrupted"}
    return record


def inspect_instance(name, current=""):
    root, state = state_for(name)
    controller = installer.service_controller()
    services = {c: controller.status(installer.unit_name(state["target"], c))
                for c in installer.COMPONENTS}
    live = [s["active"] in {"active", "activating", "deactivating"} or s["pid"]
            for s in services.values()]
    running = all(s["active"] == "active" for s in services.values())
    status = "running" if running else "partial" if any(live) else "stopped"
    job = operation(root)
    busy = job and job["status"] in {"queued", "running"}
    blocker = start_blocker(state)
    return {"name": name, "status": status, "current": str(root) == current,
            "can_start": not busy and not blocker and not running,
            "can_stop": not busy and any(live), "start_blocker": blocker,
            "urls": access_urls(state["target"]) if services["web"]["active"] == "active" else [],
            "local_only": ipaddress.ip_address(state["target"]["bind"]).is_loopback,
            "operation": job}


def catalog(current=""):
    base = PLATFORM / "instance"
    items = []
    if base.is_dir() and not base.is_symlink():
        for root in sorted(base.iterdir()):
            if root.is_dir() and not root.is_symlink() and (root / installer.STATE_FILE).is_file():
                try:
                    items.append(inspect_instance(root.name, current))
                except Exception as exc:
                    items.append({"name": root.name, "status": "unavailable",
                                  "error": str(exc), "can_start": False, "can_stop": False, "urls": []})
    return {"instances": items}


@contextlib.contextmanager
def control_lock(root):
    path = root / ".portal-control.lock"
    if path.is_symlink():
        raise ValueError("Invalid instance control lock")
    with path.open("a") as stream:
        if not try_lock_descriptor(stream.fileno()):
            raise ValueError("Another instance control request is in progress")
        yield


def launch(root, record):
    args = [sys.executable, str(Path(__file__).resolve()), "_run",
            "--name", root.name, "--operation-id", record["id"]]
    env = supervisor_environment()
    log = root / "runtime/portal-control.log"
    if log.is_symlink():
        raise ValueError("Invalid instance control log")
    if sys.platform.startswith("linux"):
        subprocess.run(["systemd-run", "--user", "--collect", "--quiet",
                        "--unit", record["unit"], "--property=Type=exec",
                        "--property=UMask=0077", "--property=RuntimeMaxSec=1200",
                        f"--property=WorkingDirectory={CODE}",
                        f"--property=StandardOutput=append:{log}",
                        f"--property=StandardError=append:{log}", *args],
                       env=env, check=True, capture_output=True, timeout=15)
    elif sys.platform == "win32":
        with log.open("ab") as output:
            child = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=output, close_fds=True,
                                     creationflags=subprocess.DETACHED_PROCESS
                                     | subprocess.CREATE_NEW_PROCESS_GROUP
                                     | subprocess.CREATE_BREAKAWAY_FROM_JOB)
            record["pid"] = child.pid
    elif sys.platform == "darwin":
        subprocess.run(["launchctl", "submit", "-l", record["unit"],
                        "-o", str(log), "-e", str(log), "--", *args],
                       env=env, check=True, capture_output=True, timeout=15)
    else:
        raise ValueError("Instance control is not supported on this platform")


def enqueue(action, name):
    if action not in {"start", "stop"}:
        raise ValueError("Only start and stop are supported")
    root, state = state_for(name)
    with control_lock(root):
        prior = operation(root)
        if prior and prior["status"] in {"queued", "running"}:
            raise ValueError("An operation is already running for this instance")
        # Serialize acceptance with CLI install/upgrade. The worker acquires
        # this same installer lock again before changing services.
        with installer.installation_lock(root):
            pass
        if action == "start" and start_blocker(state):
            raise ValueError(start_blocker(state))
        identifier = uuid.uuid4().hex
        record = {"id": identifier, "unit": f"tfi-control-{identifier}",
                  "action": action, "status": "queued", "created_at": time.time()}
        installer.write_json(control_path(root), record)
        try:
            launch(root, record)
            installer.write_json(control_path(root), record)
        except Exception:
            record.update(status="failed", error="Unable to launch the independent instance controller")
            installer.write_json(control_path(root), record)
            raise
    return {"name": name, "operation": record}


def execute(name, identifier):
    # Allow the web server to deliver its response before stopping itself.
    time.sleep(2)
    root, _ = state_for(name)
    with control_lock(root):
        record = installer.read_json(control_path(root))
        if record["id"] != identifier or record["status"] != "queued":
            raise ValueError("Instance operation no longer matches")
        record.update(status="running", pid=os.getpid())
        installer.write_json(control_path(root), record)
    try:
        installer.lifecycle(record["action"], root)
        record.update(status="completed")
    except Exception as exc:
        record.update(status="failed", error=str(exc))
    record["finished_at"] = time.time()
    installer.write_json(control_path(root), record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "start", "stop", "_run"))
    parser.add_argument("--name")
    parser.add_argument("--current", default="")
    parser.add_argument("--operation-id")
    args = parser.parse_args()
    try:
        result = (catalog(args.current) if args.action == "list" else
                  execute(args.name, args.operation_id) if args.action == "_run" else
                  enqueue(args.action, args.name))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
