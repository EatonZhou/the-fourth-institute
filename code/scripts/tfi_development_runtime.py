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

from management_common.instance_manifest import load, validate
from management_common.code_environment import runtime_python

SHARED = ("resource", "execution", "society", "worker")


def configured_root():
    instance = load()
    return instance.root if 'dev' in instance.deployments else None


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)




def management_env():
    from scripts.tfi_instance_manager import supervisor_environment
    return supervisor_environment()


def control(*args):
    return subprocess.run(["systemctl", "--user", *args], env=management_env(),
                          check=True, capture_output=True, text=True, timeout=300).stdout.strip()


def configure(root, repo, dev, dev_port, release_port):
    """Register the Release (main) and Dev worktrees as the selected instance's
    deployments, without creating database consumers."""
    from copy import deepcopy
    from management_common.host_instances import replace_manifest
    from scripts.tfi_dev import CODE_DIRECTORY, git, install_push_hook
    instance = load(root)
    main = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    dev = Path(dev).resolve()
    install_push_hook(main)
    document = deepcopy(instance.document)
    document['deployments'] = {
        'dev': {'kind': 'dev', 'code_root': str(dev / CODE_DIRECTORY), 'web_port': dev_port},
        'release': {'kind': 'release', 'code_root': str(main / CODE_DIRECTORY), 'web_port': release_port},
    }
    for component in ('dev-web', 'gateway'):
        if component not in document['services']['components']:
            document['services']['components'].append(component)
    for deployment in document['deployments'].values():
        if not (Path(deployment['code_root']) / 'management_portal/asgi.py').is_file():
            raise ValueError('Portal source is missing')
    replace_manifest(validate(instance.root, document), expected=instance.document)
    return document['deployments']


def run(root, mode):
    from management_portal.service_runtime import launch
    launch(root, 'dev-web' if mode == 'web' else 'gateway')


def affected(paths, loaded=None, *, managed=SHARED):
    """Select the responsible processes, including both Web consumers.

    ``loaded`` maps each shared component to the modules its running
    processes have imported (management_common.loaded_modules). A changed
    module restarts every component that has imported it, wherever the module
    lives; a component without a record is restarted for any changed module.
    """
    from management_common.loaded_modules import module_name
    loaded = loaded or {}
    components = set()
    for path in paths:
        path = path.removeprefix("code/")
        if path.startswith(("scripts/", "management_common/", "management_portal/hatg/")):
            components.update((*managed, "web", "dev-web", "gateway"))
        elif path.startswith("management_portal/execution/"):
            components.update(("execution", "worker", "web", "dev-web"))
        elif path.startswith("management_portal/permission_system/"):
            # Every HATG kernel authorizes through the permission port, and
            # ResourceService also runs the permission instructions and the
            # startup merge of built-in schemes.
            components.update(("resource", "execution", "society", "web", "dev-web"))
        elif path.startswith("management_portal/resources/"):
            components.update(("resource", "web", "dev-web"))
        elif path.startswith("management_portal/society_service/"):
            components.update(("society", "web", "dev-web"))
        elif path.startswith(("management_portal/", "knowledge_board_lan/")):
            components.update(("web", "dev-web"))
        module = module_name(path)
        if module is not None:
            components.update(c for c in managed if c not in loaded or module in loaded[c])
    return [c for c in (*managed, "web", "dev-web", "gateway") if c in components]


def health(port, expected=None):
    last = ""
    for _ in range(90):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                result = json.load(response)
            if result.get("ok") is False:
                raise ValueError("Web health reports not ready")
            if expected and result.get("deployment") != expected:
                raise ValueError("Unexpected Web deployment: " + str(result.get("deployment")))
            return result
        except Exception as exc:
            last = str(exc)
            time.sleep(1)
    raise RuntimeError(last)


def status(root, deployment="all"):
    manifest = load(root)
    names = tuple(manifest.deployments) if deployment == 'all' else (deployment,)
    states = []
    for name in names:
        entry = manifest.deployments[name]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{entry['web_port']}/api/health", timeout=3) as response:
                web = json.load(response)
            ok = web.get('ok') is not False and web.get('deployment') == name
        except Exception as exc:
            web, ok = {'error': str(exc)}, False
        states.append({'deployment': name, 'ok': ok, 'runtime_ok': ok, 'web': web,
                       'source': entry['code_root'], 'shared_source': str(manifest.code_root()),
                       'shared_services': [manifest.service_name(c) + '.service' for c in SHARED]})
    return states[0] if deployment != 'all' else {'ok': all(row['ok'] for row in states), 'deployments': states}


def restart_components(deployment, components=None):
    if deployment not in ("dev", "release"):
        raise ValueError("Unknown Web deployment: " + deployment)
    mapping = {"web": "dev-web" if deployment == "dev" else "web", "resource": "resource",
               "execution-service": "execution", "execution-worker": "worker", "lineage": "society"}
    requested = list(components or ("web", "resource", "execution-service", "lineage"))
    if set(requested) - set(mapping):
        raise ValueError("Unknown restart components")
    return list(dict.fromkeys(mapping[c] for c in requested))


def request_restart(root, deployment, components=None):
    """Run outside the requesting Web cgroup so its restart cannot kill the job."""
    restart_components(deployment, components)  # Validate before accepting the job.
    manifest = load(root)
    request_id = "tfi-pair-restart-" + uuid.uuid4().hex
    command = ["systemd-run", "--user", "--collect", "--unit=" + request_id,
               "--setenv=PORTAL_INSTANCE_ROOT=" + str(manifest.root),
               str(runtime_python(manifest.code_root())), str(Path(__file__).resolve()), "reload", "--root", str(root),
               "--deployment", deployment]
    for component in components or ():
        command += ["--component", component]
    subprocess.run(command, env=management_env(), capture_output=True, text=True, check=True, timeout=30)
    return {"ok": True, "queued": True, "deployment": deployment,
            "request": {"request_id": request_id, "components": list(components or ())}}


def request_activation(root, paths, commit, *, parent_commit=""):
    """Return a receipt before restarting the Web that accepted publication."""
    manifest = load(root)
    request_id = "tfi-pair-publish-" + uuid.uuid4().hex
    request = root / "runtime" / (request_id + ".json")
    receipt = root / "runtime" / (request_id + "-result.json")
    write(request, {"commit": commit, "paths": list(paths), "receipt": str(receipt),
                    "parent_commit": parent_commit, "requested_at": time.time()})
    command = ["systemd-run", "--user", "--collect", "--unit=" + request_id,
               "--setenv=PORTAL_INSTANCE_ROOT=" + str(manifest.root),
               str(runtime_python(manifest.code_root())), str(Path(__file__).resolve()), "apply", "--root", str(root),
               "--request", str(request)]
    subprocess.run(command, env=management_env(), capture_output=True, text=True, check=True, timeout=30)
    return {"queued": True, "request_id": request_id, "receipt": str(receipt)}


def apply_request(root, request):
    from management_common.file_lock import lock_stream
    payload = read(request)
    try:
        with (root / "runtime/development-activation.lock").open("a+") as stream, lock_stream(stream):
            manifest = load(root)
            current = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=manifest.code_root("release"), text=True).strip()
            try:
                previous = read(root / "runtime/development-activation.json").get("commit")
            except (OSError, ValueError, AttributeError):
                previous = None
            result = activate(root, payload["paths"], current)
            result["requested_commit"] = payload["commit"]
            if result.get("ok") and current != previous:
                result["release_event"] = record_release(root, payload, current)
    except Exception as exc:
        write(payload["receipt"], {"ok": False, "error": str(exc), "commit": payload["commit"]})
        raise
    write(payload["receipt"], result)
    return result


def record_release(root, payload, commit):
    """Publish the newly active Release commit to Lineage release-event sources."""
    from management_portal.service_runtime import clean_environment
    try:
        # The activation job runs with the supervisor environment, which
        # carries no database settings; use the installed services' own.
        os.environ["PORTAL_DATABASE_URL"] = clean_environment(load(root))["PORTAL_DATABASE_URL"]
        from management_common.portal_release_events import record
        sequence = record({"status": "completed", "ok": True, "commit": commit, "active_commit": commit,
                           "started_at": payload.get("requested_at") or time.time(), "completed_at": time.time(),
                           "parent_commit": payload.get("parent_commit", ""), "origin": "direct"})
    except Exception as exc:
        # A notification failure does not undo the healthy activation.
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "sequence": sequence}


def worker_health(root):
    try:
        value = read(root / "runtime/worker-health.json")
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def wait_worker_replacement(root, previous_id, *, requested_at, timeout_seconds=270):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        value = worker_health(root)
        current = value.get("current") or {}
        worker_id = current.get("worker_id") if isinstance(current, dict) else None
        updated_at = value.get("updated_at")
        if (value.get("ok") and worker_id and worker_id != previous_id
                and isinstance(updated_at, (int, float)) and updated_at >= requested_at):
            return value
        time.sleep(0.5)
    raise RuntimeError("explicit Worker restart has not activated a new Worker")


def restart(root, deployment, components=None, *, force_worker_restart=True):
    manifest = load(root)
    prefix = manifest.service_prefix
    worker = None
    for component in restart_components(deployment, components):
        unit = prefix + "-" + component + ".service"
        if component == "worker":
            if force_worker_restart:
                current = worker_health(root).get("current") or {}
                previous_id = current.get("worker_id") if isinstance(current, dict) else None
                requested_at = time.time()
                # Explicit restart replaces even the current generation, using
                # the manager's ready/activate/drain handoff. HUP is only reload.
                control("kill", "--kill-whom=main", "--signal=USR1", unit)
                worker = wait_worker_replacement(root, previous_id, requested_at=requested_at)
            else:
                control("kill", "--kill-whom=main", "--signal=HUP", unit)
        else:
            control("restart", unit)
    manifest = load(root)
    result = {"ok": True, "deployment": deployment, "web": health(manifest.deployments[deployment]["web_port"], deployment)}
    if worker is not None:
        result["execution_worker"] = worker
    return result


def activate(root, paths, commit, *, initial=False):
    from management_common import loaded_modules
    from management_common.service_components import FUNCTIONAL_MODULES
    manifest = load(root)
    managed = (*SHARED, *(c for c in FUNCTIONAL_MODULES if c in manifest.document['services']['components']))
    components = (list((*managed, "web", "dev-web", "gateway")) if initial
                  else affected(paths, loaded_modules.loaded(loaded_modules.directory(root)), managed=managed))
    # Code activation never stops PostgreSQL, NATS or provider account services.
    if components:
        manifest.deployment_runtime('release').mkdir(parents=True, exist_ok=True)
        (manifest.deployment_runtime("release") / "generation.env").write_text("PORTAL_BUILD_ID=portal-" + commit[:12] + "\n")
    prefix = manifest.service_prefix
    completed = []
    for component in components:
        unit = prefix + "-" + component + ".service"
        if component in FUNCTIONAL_MODULES:
            state = dict(line.split('=', 1) for line in control(
                'show', unit, '-p', 'ActiveState', '-p', 'UnitFileState').splitlines() if '=' in line)
            if state.get('ActiveState') not in {'active', 'activating'} and state.get('UnitFileState') not in {'enabled', 'enabled-runtime'}:
                continue
        if component == "worker":
            # Preserve claimed executions through the manager's handoff protocol.
            control("kill", "--kill-whom=main", "--signal=HUP", unit)
        else:
            control("restart", unit)
        completed.append(component)
        print(json.dumps({"activated": component}), flush=True)
    control("enable", prefix + "-dev-web.service", prefix + "-gateway.service")
    checks = {name: health(entry["web_port"], name) for name, entry in manifest.deployments.items()}
    checks["public"] = health(manifest.document["network"]["entry_port"])
    result = {"ok": True, "commit": commit, "components": completed, "health": checks}
    write(root / "runtime/development-activation.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("configure", "activate", "web", "gateway", "reload", "apply"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--dev", type=Path, help="The Dev worktree (default: <repo>-dev, see tfi_dev.py init)")
    parser.add_argument("--dev-port", type=int, default=28401)
    parser.add_argument("--release-port", type=int, default=28400)
    parser.add_argument("--deployment", choices=("dev", "release"), default="dev")
    parser.add_argument("--component", action="append")
    parser.add_argument("--request", type=Path)
    args = parser.parse_args()
    if args.action == "configure":
        dev = args.dev or args.repo.with_name(args.repo.name + "-dev")
        value = configure(args.root, args.repo, dev, args.dev_port, args.release_port)
    elif args.action == "activate":
        manifest = load(args.root)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=manifest.code_root("release"), text=True).strip()
        value = activate(args.root, [], commit, initial=True)
    elif args.action == "reload":
        value = restart(args.root, args.deployment, args.component)
    elif args.action == "apply":
        value = apply_request(args.root, args.request)
    else:
        run(args.root, args.action)
        return
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
