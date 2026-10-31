"""Stop account services owned by one release installation."""
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

ACCOUNT_UNIT = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.service"
# Codex account services running when the instance last stopped.
RESTART_RECORD = "runtime/codex-accounts-to-restart.json"


def _run(*arguments):
    result = subprocess.run(["systemctl", "--user", *arguments], check=False,
                            text=True, capture_output=True)
    # systemctl uses 1 for an empty unit-file glob as well as real errors.
    if not (arguments[0] == "list-unit-files" and result.returncode == 1
            and not result.stdout.strip() and not result.stderr.strip()):
        result.check_returncode()
    return result.stdout


def owned_services(target, directory):
    if sys.platform == "win32":
        return _windows_owned_services(target, directory)
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
        locator_lines = {f"Environment=PORTAL_PATHS_CONFIG={root / 'config/target.json'}",
                         f"Environment=PORTAL_PATHS_CONFIG={root / 'config/paths.json'}"}
        if (f"Environment=HOME={root / 'home'}" not in lines
                or not lines.intersection(locator_lines)):
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
    if template.is_symlink() or not template.is_file():
        raise ValueError("Provider service template ownership mismatch")
    lines = set(template.read_text().splitlines())
    required = {
        f"Environment=HOME={root / 'home'}",

    }
    locator_lines = {f"Environment=PORTAL_PATHS_CONFIG={root / 'config/target.json'}",
                     f"Environment=PORTAL_PATHS_CONFIG={root / 'config/paths.json'}"}
    if (not required.issubset(lines) or not lines.intersection(locator_lines)
            or not any(line.startswith("ExecStart=") and line.endswith(
                f"--account-config {root}/runtime/provider-accounts/codex/%i/launch.json") for line in lines)):
        raise ValueError("Provider service belongs to another instance")
    for name in sorted(names):
        if not re.fullmatch(re.escape(prefix) + ACCOUNT_UNIT, name):
            raise ValueError("Unexpected Provider service identity")
        properties = dict(line.split("=", 1) for line in _run(
            "show", name, "--property=FragmentPath,DropInPaths").splitlines() if "=" in line)
        if properties.get("FragmentPath") != str(template) or properties.get("DropInPaths"):
            raise ValueError("Provider service has a foreign definition or overrides")
    return {"template": template, "daemons": daemons,
            "names": sorted(names | {path.name for path in daemons}),
            "accounts": sorted(names), "account_prefix": prefix, "restart_record": root / RESTART_RECORD}


def _macos_owned_services(target, directory):
    from scripts.portal_service_control import ServiceControl
    from scripts import tfi_release_platform_macos as adapter
    from scripts.tfi_instance_layout import code_root
    from scripts.tfi_release_dependencies import runtime_python
    root = Path(target["data_roots"]["instances_root"]).resolve()
    scope = hashlib.sha256(os.fsencode(root)).hexdigest()[:10]
    prefix = ServiceControl.label("portal-codex-app-server@")
    names = _native_daemons(target, directory, adapter)
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
                            "--binary", argv[6], "--account-config", str(Path(target["install_root"]) / "runtime/provider-accounts/codex" / account / "launch.json")]
        environment = definition.get("EnvironmentVariables")
        if environment is not None:
            _check_macos_environment(target, environment)
        expected = plistlib.loads(adapter.command_unit_text(
            name, expected_command, cwd=code_root(target), log_root=root / "logs", log_name=name).encode())
        if environment is not None:
            expected["EnvironmentVariables"] = environment
        if definition != expected:
            raise ValueError("Provider launch agent belongs to another instance or has overrides")
        _check_loaded_agent(adapter, name, path)
        names.append(name)
    return _native_plan(target, "macos", names, {})


def _check_loaded_agent(adapter, name, path):
    controller = adapter.service_controller()
    result = controller.run("launchctl", "print", controller._launch_target(name), check=False)
    if result.returncode == 0:
        paths = [line.strip().removeprefix("path = ") for line in result.stdout.splitlines()
                 if line.strip().startswith("path = ")]
        if paths != [str(path)]:
            raise ValueError("Provider has a foreign loaded launch agent")


def _native_daemons(target, directory, adapter):
    names = []
    for component in ("claude", "kimi"):
        name = adapter.unit_name(target, component)
        path = adapter.unit_path(Path(directory), name)
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("Provider daemon ownership mismatch")
        content = path.read_text(encoding="utf-8")
        parser = json.loads if sys.platform == "win32" else lambda value: plistlib.loads(value.encode())
        if parser(content) != parser(adapter.unit_text(target, component, ())):
            raise ValueError("Provider daemon belongs to another instance")
        if sys.platform == "win32":
            adapter.verify_service(name, content)
        else:
            _check_loaded_agent(adapter, name, path)
        names.append(name)
    return names


def _native_plan(target, platform, names, controllers):
    return {"platform": platform, "template": None, "daemons": [], "names": names,
            "accounts": names, "controllers": controllers,
            "restart_record": Path(target["install_root"]) / "runtime/provider-services-to-restart.json"} if names else None


def _windows_owned_services(target, directory):
    from scripts import tfi_release_platform_windows as adapter
    from scripts.tfi_windows_user_services import WindowsServiceControl, validate_definition
    from scripts.tfi_instance_layout import code_root
    from scripts.tfi_release_dependencies import runtime_python
    root = Path(target["data_roots"]["instances_root"])
    scope = hashlib.sha256(os.fsencode(root.resolve())).hexdigest()[:10]
    names = _native_daemons(target, directory, adapter)
    controllers = {}
    account_directory = root / "service-units"
    for path in sorted(account_directory.glob(f"portal-codex-app-server@*-{scope}.service.json")):
        name = path.name.removesuffix(".json")
        if path.is_symlink() or not path.is_file():
            raise ValueError("Provider service ownership mismatch")
        content = path.read_text(encoding="utf-8")
        value = json.loads(content)
        if value.get("mode") == "user":
            validate_definition(value, name)
            if Path(value["root"]).resolve() != Path(target["install_root"]).resolve():
                raise ValueError("Provider service belongs to another installation")
        else:
            command = value.get("command", [])
            if (len(command) != 9 or command[:3] != [runtime_python(target), "-m", "management_portal.codex_app_server_host"]
                    or command[3::2] != ["--socket", "--binary", "--account-config"]
                    or not all(isinstance(arg, str) and Path(arg).is_absolute() for arg in command[4::2])):
                raise ValueError("Provider SCM command ownership mismatch")
            launch = Path(command[-1])
            account_id = launch.parent.name
            if (launch != Path(target["install_root"]) / "runtime/provider-accounts/codex" / account_id / "launch.json"
                    or Path(command[4]).resolve() != (root / "sockets" / f"codex-{account_id}.sock").resolve()
                    or name != f"portal-codex-app-server@{account_id}-{scope}.service"
                    or Path(value.get("cwd", "")).resolve() != code_root(target).resolve()
                    or value.get("account") != target["windows_service_account"]):
                raise ValueError("Provider SCM service belongs to another installation")
        adapter.verify_service(name, content)
        names.append(name)
        controllers[name] = WindowsServiceControl(account_directory)
    return _native_plan(target, "windows", names, controllers)


def _recorded(record):
    try:
        names = json.loads(record.read_text())
    except (OSError, ValueError):
        return []
    return [name for name in names if isinstance(name, str)] if isinstance(names, list) else []


def _record(record, names):
    if not names:
        record.unlink(missing_ok=True)
        return
    record.parent.mkdir(parents=True, exist_ok=True)
    temporary = record.with_name(record.name + ".tmp")
    temporary.write_text(json.dumps(sorted(names)) + "\n")
    temporary.replace(record)


def _check_macos_environment(target, environment):
    """Recognize the production Codex environment when stopping an owned job.

    Legacy jobs omit it. New jobs must retain this instance's paths, not gain
    arbitrary environment overrides merely because their argv still matches.
    """
    from scripts import tfi_release_install as api
    if not isinstance(environment, dict):
        raise ValueError("Provider launch agent environment is invalid")
    expected = api.clean_environment(target)
    required = {"PORTAL_INSTANCES_ROOT", "PORTAL_SECRETS_DIR",
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
    """Registered shared daemons start with the instance, and so do the Codex
    accounts that were running when it stopped. A turn never starts one."""
    if plan is None:
        return
    if plan.get("platform") in {"windows", "macos"}:
        failed = []
        for name in _recorded(plan["restart_record"]):
            if name not in plan["names"]:
                continue
            try:
                plan["controllers"].get(name, controller).start(name)
            except Exception:
                failed.append(name)
        _record(plan["restart_record"], failed)
        if failed:
            raise RuntimeError("Owned Provider services did not restart")
        return
    for path in plan["daemons"]:
        controller.start(path.name)
    record = plan.get("restart_record")
    if record is None:
        return
    failed = []
    for name in _recorded(record):
        if not re.fullmatch(re.escape(plan["account_prefix"]) + ACCOUNT_UNIT, name):
            continue
        try:
            _run("enable", "--now", name)
        except subprocess.CalledProcessError:
            failed.append(name)
    # The next start retries an account that did not start.
    _record(record, failed)


def stop_services(plan, controller):
    if plan is None:
        return
    record = plan.get("restart_record")
    if record is not None:
        running = {name for name in plan["accounts"]
                   if plan.get("controllers", {}).get(name, controller).status(name)["active"] in {"active", "activating", "reloading"}}
        # A repeated stop keeps what the first one recorded.
        _record(record, running | set(_recorded(record)))
    for name in plan["names"]:
        plan.get("controllers", {}).get(name, controller).stop(name)
    for name in plan["names"]:
        state = plan.get("controllers", {}).get(name, controller).status(name)
        if state["pid"] or state["active"] not in {"inactive", "failed"}:
            raise RuntimeError("Provider services did not stop; cleanup refused")
    # Do not let default.target revive account services while their instance
    # is stopped; instance start restores the recorded ones.
    if plan["names"] and plan.get("platform") not in {"macos", "windows"}:
        _run("disable", *plan["names"])


def remove_template(target, directory):
    plan = owned_services(target, directory)
    if plan is not None:
        if plan.get("platform") == "windows":
            from scripts.tfi_release_platform_windows import remove_services
            for name in plan["names"]:
                remove_services(plan["controllers"][name].directory if name in plan["controllers"] else Path(directory), [name])
            return
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
