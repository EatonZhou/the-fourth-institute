"""Stop Linux account services owned by one release installation."""
import re
import subprocess
import sys
from pathlib import Path


def _run(*arguments):
    result = subprocess.run(["systemctl", "--user", *arguments], check=False,
                            text=True, capture_output=True)
    # systemctl uses 1 for an empty unit-file glob as well as real errors.
    if not (arguments[0] == "list-unit-files" and result.returncode == 1
            and not result.stdout.strip() and not result.stderr.strip()):
        result.check_returncode()
    return result.stdout


def owned_services(target, directory):
    if not sys.platform.startswith("linux"):
        return None
    prefix = target["service_prefix"]
    if not re.fullmatch(r"tfi-[a-z0-9][a-z0-9-]*", prefix):
        raise ValueError("Invalid installation service prefix")
    prefix += "-codex-app-server@"
    template = Path(directory) / (prefix + ".service")
    names = set()
    for command in ("list-units", "list-unit-files"):
        output = _run(command, "--all", "--no-legend", "--plain", "--full", prefix + "*.service")
        names.update(line.split()[0] for line in output.splitlines() if line.strip())
    names.discard(template.name)
    if not template.exists() and not template.is_symlink() and not names:
        return None
    root = Path(target["install_root"])
    accounts = Path(target["data_roots"]["accounts_root"])
    if template.is_symlink() or not template.is_file():
        raise ValueError("Provider service template ownership mismatch")
    lines = set(template.read_text().splitlines())
    required = {
        f"Environment=HOME={root / 'home'}",
        f"Environment=PORTAL_PATHS_CONFIG={root / 'config/paths.json'}",
        f"WorkingDirectory={accounts}/codex/%i",
        f"Environment=CODEX_HOME={accounts}/codex/%i",
    }
    if not required.issubset(lines):
        raise ValueError("Provider service belongs to another instance")
    for name in sorted(names):
        if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.service", name):
            raise ValueError("Unexpected Provider service identity")
        properties = dict(line.split("=", 1) for line in _run(
            "show", name, "--property=FragmentPath,DropInPaths").splitlines() if "=" in line)
        if properties.get("FragmentPath") != str(template) or properties.get("DropInPaths"):
            raise ValueError("Provider service has a foreign definition or overrides")
    return {"template": template, "names": sorted(names)}


def stop_services(plan, controller):
    if plan is None:
        return
    for name in plan["names"]:
        controller.stop(name)
    for name in plan["names"]:
        state = controller.status(name)
        if state["pid"] or state["active"] not in {"inactive", "failed"}:
            raise RuntimeError("Provider services did not stop; cleanup refused")
    # Account services are restarted on demand by the next instance use.
    # Do not let default.target revive them while their instance is stopped.
    if plan["names"]:
        _run("disable", *plan["names"])


def remove_template(target, directory):
    plan = owned_services(target, directory)
    if plan is not None:
        if plan["names"]:
            _run("disable", *plan["names"])
        plan["template"].unlink()
        _run("daemon-reload")
