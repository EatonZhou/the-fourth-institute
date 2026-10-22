"""Stop account services owned by one release installation."""
import hashlib
import os
import plistlib
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
    if sys.platform == "darwin":
        return _macos_owned_services(target, directory)
    if not sys.platform.startswith("linux"):
        return None
    prefix = target["service_prefix"]
    if not re.fullmatch(r"tfi-[a-z0-9][a-z0-9-]*", prefix):
        raise ValueError("Invalid installation service prefix")
    daemons = []
    for suffix in ("claude-code", "kimi-code"):
        name = prefix + "-" + suffix + ".service"
        path = Path(directory) / name
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("Provider daemon ownership mismatch")
        lines = set(path.read_text().splitlines())
        root = Path(target["install_root"])
        if not {
            f"Environment=HOME={root / 'home'}",
            f"Environment=PORTAL_PATHS_CONFIG={root / 'config/paths.json'}",
        }.issubset(lines):
            raise ValueError("Provider daemon belongs to another instance")
        properties = dict(line.split("=", 1) for line in _run(
            "show", name, "--property=FragmentPath,DropInPaths").splitlines() if "=" in line)
        if properties.get("FragmentPath") != str(path) or properties.get("DropInPaths"):
            raise ValueError("Provider daemon has a foreign definition or overrides")
        daemons.append(path)
    prefix += "-codex-app-server@"
    template = Path(directory) / (prefix + ".service")
    names = set()
    for command in ("list-units", "list-unit-files"):
        output = _run(command, "--all", "--no-legend", "--plain", "--full", prefix + "*.service")
        names.update(line.split()[0] for line in output.splitlines() if line.strip())
    names.discard(template.name)
    if not template.exists() and not template.is_symlink() and not names:
        return ({"template": None, "daemons": daemons,
                 "names": [path.name for path in daemons]} if daemons else None)
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
    return {"template": template, "daemons": daemons,
            "names": sorted(names | {path.name for path in daemons})}


def _macos_owned_services(target, directory):
    from scripts.portal_service_control import ServiceControl
    from scripts import tfi_release_platform_macos as adapter
    from scripts.tfi_instance_layout import code_root
    from scripts.tfi_release_dependencies import runtime_python
    root = Path(target["data_roots"]["instances_root"]).resolve()
    accounts = Path(target["data_roots"]["accounts_root"])
    scope = hashlib.sha256(os.fsencode(root)).hexdigest()[:10]
    prefix = ServiceControl.label("portal-codex-app-server@")
    names = []
    for path in sorted(Path(directory).glob(prefix + "*-" + scope + ".plist")):
        match = re.fullmatch(re.escape(prefix) + r"([A-Za-z0-9][A-Za-z0-9_-]{0,127})-" + scope + r"\.plist", path.name)
        if not match or path.is_symlink() or not path.is_file():
            raise ValueError("Provider launch agent ownership mismatch")
        account = match.group(1)
        name = f"portal-codex-app-server@{account}-{scope}.service"
        definition = plistlib.loads(path.read_bytes())
        argv = definition.get("ProgramArguments", [])
        if (len(argv) != 9 or not isinstance(argv[6], str) or not Path(argv[6]).is_absolute()):
            raise ValueError("Provider launch agent command mismatch")
        expected_command = [runtime_python(target), "-m", "management_portal.codex_app_server_host",
                            "--socket", str(root / "sockets" / f"codex-{account}.sock"),
                            "--binary", argv[6], "--account-home", str(accounts / "codex" / account)]
        environment = definition.get("EnvironmentVariables")
        if environment is not None:
            _check_macos_environment(target, environment)
        expected = plistlib.loads(adapter.command_unit_text(
            name, expected_command, cwd=code_root(target), log_root=root / "logs", log_name=name).encode())
        if environment is not None:
            expected["EnvironmentVariables"] = environment
        if definition != expected:
            raise ValueError("Provider launch agent belongs to another instance or has overrides")
        names.append(name)
    return {"platform": "macos", "template": None, "daemons": [], "names": names} if names else None


def _check_macos_environment(target, environment):
    """Recognize the production Codex environment when stopping an owned job.

    Legacy jobs omit it. New jobs must retain this instance's paths, not gain
    arbitrary environment overrides merely because their argv still matches.
    """
    from scripts import tfi_release_install as api
    if not isinstance(environment, dict):
        raise ValueError("Provider launch agent environment is invalid")
    expected = api.clean_environment(target)
    required = {"PORTAL_INSTANCES_ROOT", "PORTAL_ACCOUNTS_ROOT", "PORTAL_SECRETS_DIR",
                "PORTAL_NETWORK_CONFIG", "PORTAL_SITE_CONFIG", "PORTAL_PATHS_CONFIG",
                "PATH", "HOME", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE", "PYTHONUTF8"}
    allowed = required | {"XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR", "LANG",
                          "TFI_MACOS_SERVICE_DIRECTORY", "PORTAL_PROVIDER_EGRESS_ENV_FILE"}
    root = Path(target["install_root"])
    configured = api.read_json(root / "config/site.json").get("deployment", {}).get("providers", {})
    if configured.get("egress_env_file"):
        path = Path(configured["egress_env_file"])
        if path.is_absolute():
            expected["PORTAL_PROVIDER_EGRESS_ENV_FILE"] = str(path.resolve())
    if (not required.issubset(environment) or set(environment) - allowed
            or any(expected.get(key) != value for key, value in environment.items())):
        raise ValueError("Provider launch agent environment belongs to another instance or has overrides")


def start_services(plan, controller):
    """Registered shared daemons start with the instance; Codex stays on demand."""
    if plan is not None:
        for path in plan["daemons"]:
            controller.start(path.name)


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
    if plan["names"] and plan.get("platform") != "macos":
        _run("disable", *plan["names"])


def remove_template(target, directory):
    plan = owned_services(target, directory)
    if plan is not None:
        if plan.get("platform") == "macos":
            from scripts.tfi_release_platform_macos import remove_services
            remove_services(Path(directory), plan["names"])
            return
        if plan["names"]:
            _run("disable", *plan["names"])
        if plan["template"] is not None:
            plan["template"].unlink()
        for path in plan["daemons"]:
            path.unlink()
        _run("daemon-reload")
