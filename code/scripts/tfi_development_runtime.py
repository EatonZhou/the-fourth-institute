"""Two Web worktrees on one installed Portal's database and service runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SHARED = ("resource", "execution", "society", "worker")


def configured_root():
    """Resolve the installed pair from its explicit site or common Git config."""
    site = os.getenv("PORTAL_SITE_CONFIG")
    if site:
        root = Path(site).resolve().parent.parent
        if (root / "config/development.json").is_file():
            return root
    from scripts.tfi_dev import configuration
    try:
        value = configuration(Path(__file__).resolve().parents[2]).get("runtime_root")
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    root = Path(value) if value else None
    return root if root and (root / "config/development.json").is_file() else None


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def adapt_component(target, component, command, env, config):
    """Keep the installed identity, sockets and queues; select only source/Web."""
    env = dict(env)
    command = list(command)
    dev, release = config["dev_code"], config["release_code"]
    env["PORTAL_SHARED_SOURCE_ROOT"] = dev
    env["PORTAL_EXECUTION_WORKER_SOURCE_ROOT"] = dev
    source = dev if component in SHARED else release
    env["PYTHONPATH"] = source
    env["PORTAL_COMPONENT_SOURCE_ROOT"] = source
    if component == "web":
        command[command.index("--port") + 1] = str(config["web_ports"]["release"])
    return command, env


def management_env():
    from scripts.tfi_instance_manager import supervisor_environment
    return supervisor_environment()


def control(*args):
    return subprocess.run(["systemctl", "--user", *args], env=management_env(),
                          check=True, capture_output=True, text=True, timeout=300).stdout.strip()


def configure(root, repo, dev_port, release_port, gateway_unit=None):
    """Prepare a pair inside an existing instance; never install a second DB."""
    from scripts.tfi_dev import configuration, git, install_push_hook
    from scripts.tfi_release_install import runtime_python
    if sys.platform != "linux":
        raise ValueError("The paired runtime installer currently supports Linux user services")
    cfg = configuration(repo)
    install_push_hook(Path(cfg["main"]))
    target = read(root / "installation.json")["target"]
    if len({dev_port, release_port, target["ports"]["web"]}) != 3:
        raise ValueError("Dev Web, Release Web and public router need distinct ports")
    config = {"schema": "tfi.development-runtime.v1", "dev_code": str(Path(cfg["dev"]) / "code"),
              "release_code": str(Path(cfg["main"]) / "code"),
              "web_ports": {"dev": dev_port, "release": release_port},
              "router_port": target["ports"]["web"]}
    previous = root / "config/development.json"
    if gateway_unit or previous.is_file():
        gateway_unit = gateway_unit or read(previous).get("gateway_unit")
    if gateway_unit:
        gateway_file = Path(control("show", gateway_unit, "--property=FragmentPath", "--value"))
        if not gateway_file.is_file() or gateway_file.name != gateway_unit:
            raise ValueError("The HTTPS gateway must be an existing user service")
        config["gateway_unit"] = gateway_unit
    for source in (config["dev_code"], config["release_code"]):
        if not (Path(source) / "management_portal/asgi.py").is_file():
            raise ValueError("Portal source is missing: " + source)
    write(root / "config/development.json", config)
    registry = root / "config/deployments.json"
    write(registry, {"schema": "tfi.portal-deployments.v1", "instances": [
        {"id": name, "label": name.title(), "kind": name,
         "origin": f"http://127.0.0.1:{port}", "order": index}
        for index, (name, port) in enumerate(config["web_ports"].items())]})
    cfg["runtime_root"] = str(root)
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    write(common / "tfi-development.json", cfg)
    script = Path(config["release_code"]) / "scripts/tfi_development_runtime.py"
    # Agent commands inherit the application's private HOME. Register units
    # beside the installed Web's real user-manager unit, not in that HOME.
    installed_web_unit = Path(control("show", target["service_prefix"] + "-web.service",
                                      "--property=FragmentPath", "--value"))
    if not installed_web_unit.is_file():
        raise ValueError("The installed Web has no user service unit")
    unit_root = installed_web_unit.parent
    for name, mode in (("dev-web", "web"), ("router", "router")):
        command = [runtime_python(target), str(script), mode, "--root", str(root)]
        content = (f"[Unit]\nDescription=TFI paired {name}\n"
                   f"[Service]\nType=simple\nUMask=0077\nWorkingDirectory={config['dev_code']}\n"
                   f"ExecStart={' '.join(json.dumps(v) for v in command)}\n"
                   "Restart=on-failure\nRestartSec=3\nTimeoutStopSec=90\n"
                   "[Install]\nWantedBy=default.target\n")
        path = unit_root / (target["service_prefix"] + "-" + name + ".service")
        if path.exists() and "Description=TFI paired " not in path.read_text():
            raise ValueError("An unrelated service already owns " + str(path))
        path.write_text(content)
    if gateway_unit:
        drop_in = unit_root / (gateway_unit + ".d")
        drop_in.mkdir(exist_ok=True)
        path = drop_in / "tfi-development.conf"
        content = ("# TFI paired runtime\n[Service]\n"
                   + "Environment=" + json.dumps("PORTAL_DEPLOYMENT_REGISTRY_PATH=" + str(registry)) + "\n"
                   + "Environment=" + json.dumps("PORTAL_SHARED_SOURCE_ROOT=" + config["dev_code"]) + "\n")
        if path.exists() and not path.read_text().startswith("# TFI paired runtime"):
            raise ValueError("An unrelated gateway override already exists")
        path.write_text(content)
    site_path = root / "config/site.json"
    site = read(site_path)
    site.setdefault("deployment", {}).update(source_root=config["dev_code"], release_root=config["release_code"])
    write(site_path, site)
    control("daemon-reload")
    return config


def run(root, mode):
    from scripts.tfi_release_install import component_command, runtime_python
    target = read(root / "installation.json")["target"]
    config = read(root / "config/development.json")
    command, env = component_command(target, "web")
    if mode == "web":
        command[command.index("--port") + 1] = str(config["web_ports"]["dev"])
        env["PORTAL_INSTANCE"] = "dev"
        env["PORTAL_BUILD_ID"] = "dev-" + subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=config["dev_code"], text=True).strip()
        source = config["dev_code"]
    else:
        source = config["release_code"]
        env["PORTAL_DEPLOYMENT_REGISTRY_PATH"] = str(root / "config/deployments.json")
        # Default to the published view; a logged-in user's switch is sticky.
        env["PORTAL_ROUTER_DEV_USERS"] = ""
        command = [runtime_python(target), "-m", "management_portal.portal_deployment_router",
                   "--port", str(config["router_port"])]
    env["PYTHONPATH"] = source
    os.chdir(source)
    os.execve(command[0], command, env)


def affected(paths):
    """Select the responsible processes, including both Web consumers."""
    components = set()
    for path in paths:
        path = path.removeprefix("code/")
        if path.startswith(("scripts/", "management_common/", "management_portal/hatg/")):
            components.update((*SHARED, "web", "dev-web", "router"))
        elif path.startswith("management_portal/execution/"):
            components.update(("execution", "worker", "web", "dev-web"))
        elif path.startswith("management_portal/resources/"):
            components.update(("resource", "web", "dev-web"))
        elif path.startswith("management_portal/society_service/"):
            components.update(("society", "web", "dev-web"))
        elif path.startswith(("management_portal/", "knowledge_board_lan/")):
            components.update(("web", "dev-web"))
    return [c for c in (*SHARED, "web", "dev-web", "router") if c in components]


def health(port, expected=None):
    last = ""
    for _ in range(90):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                result = json.load(response)
            if result.get("ok") is False:
                raise ValueError("Web health reports not ready")
            if expected and result.get("instance") != expected:
                raise ValueError("Unexpected Web instance: " + str(result.get("instance")))
            return result
        except Exception as exc:
            last = str(exc)
            time.sleep(1)
    raise RuntimeError(last)


def status(root, instance="all"):
    config = read(root / "config/development.json")
    target = read(root / "installation.json")["target"]
    names = ("dev", "release") if instance == "all" else (instance,)
    states = []
    for name in names:
        if name not in config["web_ports"]:
            raise ValueError("Unknown Web instance: " + name)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{config['web_ports'][name]}/api/health", timeout=3) as response:
                web = json.load(response)
            ok = web.get("ok") is not False and web.get("instance") == name
        except Exception as exc:
            web, ok = {"error": str(exc)}, False
        states.append({"instance": name, "ok": ok, "runtime_ok": ok, "web": web,
                       "source": config[name + "_code"], "shared_source": config["dev_code"],
                       "shared_services": [target["service_prefix"] + "-" + c + ".service" for c in SHARED]})
    return states[0] if instance != "all" else {"ok": all(s["ok"] for s in states), "instances": states}


def restart_components(instance, components=None):
    if instance not in ("dev", "release"):
        raise ValueError("Unknown Web instance: " + instance)
    mapping = {"web": "dev-web" if instance == "dev" else "web", "resource": "resource",
               "execution-service": "execution", "execution-worker": "worker", "lineage": "society"}
    requested = list(components or ("web", "resource", "execution-service", "lineage"))
    if set(requested) - set(mapping):
        raise ValueError("Unknown restart components")
    return list(dict.fromkeys(mapping[c] for c in requested))


def request_restart(root, instance, components=None):
    """Run outside the requesting Web cgroup so its restart cannot kill the job."""
    from scripts.tfi_release_install import runtime_python
    restart_components(instance, components)  # Validate before accepting the job.
    target = read(root / "installation.json")["target"]
    request_id = "tfi-pair-restart-" + uuid.uuid4().hex
    command = ["systemd-run", "--user", "--collect", "--unit=" + request_id,
               runtime_python(target), str(Path(__file__).resolve()), "reload", "--root", str(root),
               "--instance", instance]
    for component in components or ():
        command += ["--component", component]
    subprocess.run(command, env=management_env(), capture_output=True, text=True, check=True, timeout=30)
    return {"ok": True, "queued": True, "instance": instance,
            "request": {"request_id": request_id, "components": list(components or ())}}


def request_activation(root, paths, commit):
    """Return a receipt before restarting the Web that accepted publication."""
    from scripts.tfi_release_install import runtime_python
    target = read(root / "installation.json")["target"]
    request_id = "tfi-pair-publish-" + uuid.uuid4().hex
    request = root / "runtime" / (request_id + ".json")
    receipt = root / "runtime" / (request_id + "-result.json")
    write(request, {"commit": commit, "paths": list(paths), "receipt": str(receipt)})
    command = ["systemd-run", "--user", "--collect", "--unit=" + request_id,
               runtime_python(target), str(Path(__file__).resolve()), "apply", "--root", str(root),
               "--request", str(request)]
    subprocess.run(command, env=management_env(), capture_output=True, text=True, check=True, timeout=30)
    return {"queued": True, "request_id": request_id, "receipt": str(receipt)}


def apply_request(root, request):
    from management_common.file_lock import lock_stream
    payload = read(request)
    try:
        with (root / "runtime/development-activation.lock").open("a+") as stream, lock_stream(stream):
            config = read(root / "config/development.json")
            current = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config["release_code"], text=True).strip()
            result = activate(root, payload["paths"], current)
            result["requested_commit"] = payload["commit"]
    except Exception as exc:
        write(payload["receipt"], {"ok": False, "error": str(exc), "commit": payload["commit"]})
        raise
    write(payload["receipt"], result)
    return result


def restart(root, instance, components=None):
    prefix = read(root / "installation.json")["target"]["service_prefix"]
    for component in restart_components(instance, components):
        unit = prefix + "-" + component + ".service"
        if component == "worker":
            control("kill", "--kill-whom=main", "--signal=HUP", unit)
        else:
            control("restart", unit)
    config = read(root / "config/development.json")
    return {"ok": True, "instance": instance, "web": health(config["web_ports"][instance], instance)}


def activate(root, paths, commit, *, initial=False):
    target = read(root / "installation.json")["target"]
    config = read(root / "config/development.json")
    components = list((*SHARED, "web", "dev-web", "router")) if initial else affected(paths)
    # Code activation never stops PostgreSQL, NATS or provider account services.
    if components:
        (root / "config/generation.env").write_text("PORTAL_BUILD_ID=portal-" + commit[:12] + "\n")
    prefix = target["service_prefix"]
    completed = []
    for component in components:
        unit = prefix + "-" + component + ".service"
        if component == "worker":
            # Preserve claimed executions through the manager's handoff protocol.
            control("kill", "--kill-whom=main", "--signal=HUP", unit)
        else:
            control("restart", unit)
        if component == "router" and config.get("gateway_unit"):
            control("restart", config["gateway_unit"])
        completed.append(component)
        print(json.dumps({"activated": component}), flush=True)
    control("enable", prefix + "-dev-web.service", prefix + "-router.service")
    checks = {name: health(port, name) for name, port in config["web_ports"].items()}
    checks["public"] = health(config["router_port"])
    result = {"ok": True, "commit": commit, "components": completed, "health": checks}
    write(root / "runtime/development-activation.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("configure", "activate", "web", "router", "reload", "apply"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--dev-port", type=int, default=28401)
    parser.add_argument("--release-port", type=int, default=28400)
    parser.add_argument("--instance", choices=("dev", "release"), default="dev")
    parser.add_argument("--component", action="append")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--gateway-unit", help="Existing HTTPS upstream router to use the same Dev/Release registry")
    args = parser.parse_args()
    if args.action == "configure":
        value = configure(args.root, args.repo, args.dev_port, args.release_port, args.gateway_unit)
    elif args.action == "activate":
        cfg = read(args.root / "config/development.json")
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cfg["release_code"], text=True).strip()
        value = activate(args.root, [], commit, initial=True)
    elif args.action == "reload":
        value = restart(args.root, args.instance, args.component)
    elif args.action == "apply":
        value = apply_request(args.root, args.request)
    else:
        run(args.root, args.action)
        return
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
