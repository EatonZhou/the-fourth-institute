"""One explicit update pass for shared native Provider runtimes.

The System Lineage schedules a Resident to invoke this pass. Each Provider
whose runtime Portal installs declares how (``AgentProvider.runtime_updater``,
a ``ProviderRuntime``).  Installs are staged in versioned directories;
activation replaces native services and automatically resumes interrupted inputs.
"""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import json
import logging
import os
import hashlib
from management_common.directory_links import create_windows_junction as _windows_junction
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import threading
import uuid
from urllib.request import urlopen

from packaging.version import Version

from management_common.portal_database import portal_database
from management_common.file_lock import lock_stream, try_lock_descriptor
from management_common.process_runtime import run_process
from management_common.portal_paths import path as portal_path
from management_common.code_environment import provider_entry, provider_directory
from .provider_preparation import phase, diagnosed, PreparationError
from .provider_activation import activation_window, notify_pending_updates, publish_activation_ready, wait_for_execution_idle

LOGGER = logging.getLogger(__name__)
ACTIVATION_WAIT_SECONDS = 72 * 60 * 60
ROOT = Path(__file__).resolve().parents[2]
RUNTIMES = provider_directory()
STATE_ROOT = portal_path("provider-state")


def npm_binary(root: Path, name: str) -> Path:
    """npm --global --prefix writes Windows shims at the prefix root."""
    return root / (f"{name}.cmd" if sys.platform == "win32" else f"bin/{name}")


def venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def codex_cli_version(output: str) -> str:
    match = re.search(r"(?<!\d)(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.]+)?)\b", output)
    return match.group(1) if match else "0"


# ``run_command``, ``npm_version``, ``pypi_version`` and ``linked_version``
# serve the ``ProviderRuntime`` of every Provider, wherever it is declared.
def run_command(*command: str, timeout: int = 1800, env: dict[str, str] | None = None) -> str:
    result = run_process(command, check=True, text=True, capture_output=True, timeout=timeout, env=env)
    return result.stdout.strip()


def _service_control():
    if sys.platform in {"win32", "darwin"}:
        from management_common.instance_manifest import load
        from scripts.instance_services import controller
        return controller(load())
    from scripts.portal_service_control import ServiceControl
    from scripts.portal_service_units import _run as run_unit_command
    def run(*command, check=True):
        if command[:2] == ("systemctl", "--user"):
            return run_unit_command(*command, check=check)
        return run_process(command, check=check, text=True, capture_output=True, timeout=180)
    return ServiceControl(runner=run)


@diagnosed("download")
def npm_version(package: str, tag: str) -> str:
    args = (package, f"dist-tags.{tag}") if tag in {"latest", "next"} else (package + "@" + tag, "version")
    value = run_command("npm", "view", *args, "--json", timeout=135)
    version = json.loads(value)
    if isinstance(version, list):
        versions = [v for v in version if isinstance(v, str) and not Version(v).is_prerelease]
        if not versions:
            raise ValueError(f"No compatible npm version for {package}")
        version = max(versions, key=Version)
    if not isinstance(version, str):
        raise ValueError(f"Invalid npm version for {package}: {version!r}")
    Version(version)
    return version


@diagnosed("download")
def pypi_version(package: str) -> str:
    with urlopen(f"https://pypi.org/pypi/{package}/json", timeout=90) as response:
        version = json.load(response)["info"]["version"]
    Version(version)
    return version


def linked_version(link: Path, prefix: str) -> str:
    """The version in the name of the directory a runtime link points to."""

    match = re.fullmatch(re.escape(prefix) + r"(.+)", link.resolve().name) if link.exists() else None
    return match.group(1) if match else "0"


def _claude_sdk(interpreter: Path) -> tuple[Path, str] | None:
    """Verify a reusable environment, including the daemon's dependencies.

    Probe the environment's own interpreter; resolving its symlink would lose
    the venv. Isolated mode prevents caller PYTHONPATH from making a broken
    runtime appear ready. No login, model call or package installation occurs.
    """
    if not interpreter.is_file():
        return None
    try:
        value = json.loads(run_command(str(interpreter), "-I", "-c",
            "import json,sys; from pathlib import Path; "
            "import claude_agent_sdk as s; import psycopg,psycopg_pool; "
            "print(json.dumps([sys.prefix,s.__version__,str(Path(s.__file__).parent / "
            "'_bundled' / ('claude.exe' if sys.platform == 'win32' else 'claude'))]))",
            timeout=10))
        prefix, version, bundled = value
        if not all(isinstance(item, str) for item in value):
            return None
        root = Path(prefix)
        Version(version)
        if not root.is_absolute() or not venv_python(root).is_file():
            return None
        if not Path(bundled).is_file() or not os.access(bundled, os.X_OK):
            return None
        if not run_command(bundled, "--version", timeout=10):
            return None
        return root, version
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None


class ProviderRuntime:
    """How Portal checks, stages and switches one Provider's shared local runtime.

    A Provider declares its runtime class (``AgentProvider.runtime_updater``).
    ``available`` and ``installed`` give the versions of the packages the
    runtime is built from, in the same order; a newer release of any of them
    stages a new runtime.  Portal's clients run the current runtime through
    ``link``; activation points ``activation_link`` at the staged one
    (``link_target``) and restarts ``services``. Programs belong to the selected
    code's dependency directory.
    """

    services: tuple[str, ...] = ()
    # A second Portal build of the same official backend gets a numbered edition.
    # Keep historical entries so running builds retain their identity after rollback.
    release_editions: dict[str, int] = {}

    @property
    def unit_files(self) -> tuple[str, ...]:
        """Static unit definitions installed before any account starts."""
        return self.services

    def available(self) -> tuple[str, ...]:
        raise NotImplementedError

    def installed(self) -> tuple[str, ...]:
        raise NotImplementedError

    def supports_versions(self, versions: tuple[str, ...]) -> bool:
        """Whether this adapter supports the installed native wire contract."""
        return True

    def stage(self, versions: tuple[str, ...]) -> Path:
        raise NotImplementedError

    def link(self) -> Path:
        raise NotImplementedError

    def version_label(self, versions, *, installed=False):
        """Public release identity, independently of package-manager inputs."""
        official = versions[0] if versions else ""
        if official not in self.release_editions:
            return official
        if installed:
            selected = self.link().resolve()
            for root in (selected, *selected.parents):
                if root.parent == RUNTIMES.resolve():
                    marker = root / "PROVIDER_EDITION"
                    edition = int(marker.read_text()) if marker.is_file() else 0
                    break
            else:
                edition = 0
        else:
            edition = self.release_editions[official]
        return f"{official}-{edition}" if edition else official

    def mark_edition(self, root, official):
        if official in self.release_editions:
            (root / "PROVIDER_EDITION").write_text(str(self.release_editions[official]) + "\n")

    def prerequisites_available(self) -> bool:
        return True

    def prepare_prerequisites(self) -> None:
        pass

    def service_control(self):
        return _service_control()

    def ensure_ready(self):
        """First-use activation; account-scoped hosts start at account login."""

    def ready(self):
        return True

    def installation_current(self, versions):
        """Whether the installed upstream versions include required local revisions."""
        return self.version_label(versions, installed=True) == self.version_label(versions)

    def activation_ready(self, root, *, deadline=None):
        return True

    def account_processes(self):
        return ()

    def restart_account_process(self, account_id):
        raise NotImplementedError

    def activation_link(self) -> Path:
        return self.link()

    def link_target(self, root: Path) -> Path:
        return root

class CodexRuntime(ProviderRuntime):
    # Each account host is its own registered component, defined when the
    # account starts; there is no static definition to install beforehand.
    unit_files = ()

    def ensure_ready(self):
        from .account_registrations import registrations
        from .account_hosting import codex_instance_state, codex_socket_path
        from management_portal.codex_app_server_client import CodexAppServerClient

        for row in registrations("codex"):
            if row["kind"] != "subscription_login" or codex_instance_state(row["account_id"])["active"] != "active":
                continue
            deadline = time.monotonic() + 45
            while True:
                client = CodexAppServerClient(codex_socket_path(row["account_id"]))
                try:
                    client.request("model/list", {}, timeout=5)
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.1)
                finally:
                    client.close()
    @property
    def services(self) -> tuple[str, ...]:
        from .account_registrations import registrations
        from .account_hosting import codex_instance_state
        # Disabled registration can still have an existing live process. Update
        # that process too, while preserving accounts deliberately left stopped.
        states = (codex_instance_state(row["account_id"]) for row in registrations("codex"))
        services = tuple(state["unit"] for state in states if state["active"] == "active")
        if services and sys.platform in {"win32", "darwin"}:
            from scripts.portal_service_units import verify_native_codex_services
            verify_native_codex_services(services)
        return services

    def available(self) -> tuple[str, ...]:
        return (npm_version("@openai/codex", "latest"),)

    def installed(self) -> tuple[str, ...]:
        if sys.platform == "win32":
            marker = self.link() / ".portal-validated"
            return (marker.read_text().strip() if marker.is_file() else linked_version(self.link(), "codex-cli-"),)
        return (codex_cli_version(run_command(str(self.link()), "--version", timeout=45)),)

    @diagnosed("dependency_install")
    def stage(self, versions: tuple[str, ...]) -> Path:
        (version,) = versions
        root = RUNTIMES / f"codex-cli-{self.version_label(versions)}"
        binary = npm_binary(root, "codex")
        marker = root / ".portal-validated"
        if not marker.is_file() or not binary.is_file():
            run_command("npm", "install", "--global", "--prefix", str(root), "--omit=dev", f"@openai/codex@{version}")
        if sys.platform == "linux":
            binary = self._write_launcher(root, binary)
        with phase("executable_validation"):
            if Version(codex_cli_version(run_command(str(binary), "--version", timeout=60))) != Version(version):
                raise RuntimeError("Codex staged version mismatch")
            run_command(str(binary), "app-server", "--help", timeout=60)
        self.mark_edition(root, version)
        marker.write_text(version + "\n")
        return root

    @staticmethod
    def _write_launcher(root: Path, entry: Path) -> Path:
        """Use the Node version selected by the backend code's dependency catalog."""
        from management_common.code_environment import native_binary
        from management_common.instance_manifest import load
        node = str(native_binary(load().code_root(), 'node'))
        launcher = root / "portal-codex"
        content = f'#!/bin/sh\nexec {shlex.quote(node)} {shlex.quote(str(entry.resolve()))} "$@"\n'
        if not launcher.is_file() or launcher.read_text() != content:
            temporary = launcher.with_name(f".{launcher.name}.{os.getpid()}.tmp")
            temporary.write_text(content)
            temporary.chmod(0o755)
            temporary.replace(launcher)
        return launcher

    def link(self) -> Path:
        return self.activation_link()

    def activation_link(self) -> Path:
        return provider_entry("codex_binary")

    def link_target(self, root: Path) -> Path:
        if sys.platform == "win32":
            return root
        return root / "portal-codex" if sys.platform == "linux" else npm_binary(root, "codex")


class ClaudeRuntime(ProviderRuntime):
    unit_files = ("portal-claude-code.service",)

    def ready(self):
        from management_portal.claude_code_client import ClaudeCodeClient, ClaudeCodeUnavailable
        try:
            ClaudeCodeClient().health()
            return True
        except ClaudeCodeUnavailable:
            return False

    @diagnosed("service_activation")
    def ensure_ready(self):
        from scripts.portal_service_units import start_claude_service
        if self.ready():
            return
        def wait_ready():
            deadline = time.monotonic() + 45
            while not self.ready():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Claude daemon did not become ready")
                time.sleep(.1)
        start_claude_service(wait_ready=wait_ready)

    @property
    def services(self) -> tuple[str, ...]:
        from scripts.portal_service_units import claude_account_services, provider_runtime_services
        return provider_runtime_services(self.unit_files[0]) + claude_account_services()

    def available(self) -> tuple[str, ...]:
        from management_portal.claude_runtime import PROVIDER_REVISION
        sdk = pypi_version("claude-agent-sdk")
        installed = self.installed()
        if len(installed) == 3 and installed[0] == sdk:
            cli = installed[1]
        else:
            # Anthropic pins the bundled CLI in each official SDK release tag.
            with urlopen(f"https://raw.githubusercontent.com/anthropics/claude-agent-sdk-python/"
                         f"v{Version(sdk)}/src/claude_agent_sdk/_cli_version.py", timeout=30) as response:
                match = re.search(r'^__cli_version__ = "(\d+\.\d+\.\d+)"$', response.read(4096).decode(), re.M)
            if not match:
                raise ValueError("Missing bundled Claude Code version in the SDK release")
            cli = match[1]
        return sdk, cli, str(PROVIDER_REVISION)

    def version_label(self, versions, *, installed=False):
        from management_portal.claude_runtime import provider_version
        if len(versions) != 3:
            return super().version_label(versions)
        return provider_version(versions[1], int(versions[2]))

    def selected_sdk(self, package):
        from management_portal.claude_runtime import release_sdk
        return release_sdk(package, provider_entry("claude_runtime_current"))

    def installed(self) -> tuple[str, ...]:
        package = self.link().resolve()
        if not (package / "SDK_DIRECTORY").is_file():
            return ("0",)
        components = json.loads((package / "COMPONENTS.json").read_text())
        versions = (components["agent_sdk"], components["claude_code"], str(components["revision"]))
        found = _claude_sdk(venv_python(self.selected_sdk(package)))
        if found and versions[0] == found[1] and self.version_label(versions) == (package / "VERSION").read_text().strip():
            return versions
        return ("0",)

    @diagnosed("dependency_install")
    def stage(self, versions: tuple[str, ...]) -> Path:
        from management_portal.claude_runtime import PROVIDER_REVISION
        from scripts.claude_app_server_package import stage_release
        sdk, cli, revision = versions
        if revision != str(PROVIDER_REVISION):
            raise ValueError("Unknown Claude provider revision")
        version = self.version_label(versions)
        release = RUNTIMES / f"claude-provider-{version}"
        selected = self.selected_sdk(release if release.exists() else self.link())
        root = RUNTIMES / f"claude-agent-sdk-{sdk}-r{revision}"
        found = _claude_sdk(venv_python(selected))
        if found and found[1] == sdk and (selected / ".portal-validated").is_file():
            if (selected / ".portal-validated").read_text() == "\n".join(self.requirements(sdk)) + "\n":
                return stage_release(ROOT, RUNTIMES, found[0], sdk, expected_cli=cli)
        self.stage_sdk(sdk, root)
        return stage_release(ROOT, RUNTIMES, root, sdk, expected_cli=cli)

    @staticmethod
    def requirements(version):
        extras = [line.strip() for line in (ROOT / "requirements-claude-code.txt").read_text().splitlines()
                  if line.strip() and not line.startswith("#")
                  and not line.strip().startswith(("claude-agent-sdk==", "claude-agent-sdk>=", "claude-agent-sdk "))
                  and line.strip() != "claude-agent-sdk"]
        return [f"claude-agent-sdk=={version}", *extras]

    def stage_sdk(self, version: str, root: Path) -> Path:
        binary = venv_python(root)
        marker = root / ".portal-validated"
        requirements = self.requirements(version)
        # The marker names every installed requirement, so a dependency the
        # daemon gains is installed into an existing runtime as well.
        validated = "\n".join(requirements) + "\n"
        if not binary.is_file():
            run_command(sys.executable, "-m", "venv", str(root))
        if not marker.is_file() or marker.read_text() != validated:
            run_command(str(binary), "-m", "pip", "install", *requirements, timeout=3600)
        with phase("executable_validation"):
            run_command(str(binary), "-c", "import claude_agent_sdk as s,sys; assert s.__version__ == sys.argv[1]", version)
            bundled = run_command(str(binary), "-c", "from pathlib import Path; import sys, claude_agent_sdk as s; print(Path(s.__file__).parent/'_bundled'/('claude.exe' if sys.platform == 'win32' else 'claude'))")
            run_command(bundled, "--version", timeout=60)
        marker.write_text(validated)
        return root

    def link(self) -> Path:
        return self.activation_link()

    def activation_link(self) -> Path:
        return provider_entry("claude_app_server_current")

class KimiRuntime(ProviderRuntime):
    # Automatic updates stay on the deployed series. The transcript client for
    # 2.5.3 can be staged explicitly; standalone subagent stop differs.
    VERSION_RANGE = "~2.4.0"

    def supports_versions(self, versions):
        return all(value == "2.5.3" or Version(value) < Version("2.5.0") for value in versions)

    def account_processes(self):
        from management_portal.kimi_account_runtime import running_accounts
        return tuple(account["account_id"] for account, _, _ in running_accounts())

    def restart_account_process(self, account_id):
        from management_portal.kimi_account_runtime import restart_account
        restart_account(account_id)

    def prerequisites_available(self) -> bool:
        from management_portal.kimi_windows_runtime import available
        return available()

    def prepare_prerequisites(self) -> None:
        from management_portal.kimi_windows_runtime import prepare
        prepare()

    unit_files = ("portal-kimi-code.service",)

    @property
    def services(self) -> tuple[str, ...]:
        from scripts.portal_service_units import provider_runtime_services
        return provider_runtime_services(self.unit_files[0])

    def available(self) -> tuple[str, ...]:
        return (npm_version("@moonshot-ai/kimi-code", self.VERSION_RANGE),)

    def installed(self) -> tuple[str, ...]:
        link = self.link()
        if not link.exists():
            return ("0",)
        return (run_command(str(npm_binary(link, "kimi")), "--version", timeout=60),)

    def version_label(self, versions, *, installed=False):
        base = super().version_label(versions)
        if base in {"2.4.1", "2.5.3"} and (not installed or self.installation_current(versions)):
            return base + "-1"
        return base

    def installation_current(self, versions):
        from .kimi_runtime_patch import verified
        return versions not in {("2.4.1",), ("2.5.3",)} or verified(self.link())

    def activation_ready(self, root, *, deadline=None):
        from .kimi_runtime_patch import PATCH_ID
        if not Path(root).name.endswith('-' + PATCH_ID):
            return True
        # Installing this restriction must not replay existing business turns.
        from management_portal import kimi_account_runtime
        if deadline is None:
            return not _active_executions('kimi') and not kimi_account_runtime.runtime_busy()
        if not wait_for_execution_idle(lambda: _active_executions('kimi'), deadline):
            return False
        return kimi_account_runtime.wait_for_runtime_idle(deadline)

    @diagnosed("dependency_install")
    def stage(self, versions: tuple[str, ...]) -> Path:
        if not self.supports_versions(versions):
            raise ValueError("Kimi runtime uses an unsupported native protocol")
        self.prepare_prerequisites()
        (version,) = versions
        root = RUNTIMES / f"kimi-code-{version}"
        native_policy_revision = version in {"2.4.1", "2.5.3"}
        if native_policy_revision:
            from .kimi_runtime_patch import PATCH_ID
            root = RUNTIMES / f"kimi-code-{version}-{PATCH_ID}"
        binary = npm_binary(root, "kimi")
        marker = root / ".portal-validated"
        if not marker.is_file() or not binary.is_file():
            run_command("npm", "install", "--global", "--prefix", str(root), "--omit=dev", f"@moonshot-ai/kimi-code@{version}")
        if native_policy_revision:
            from .kimi_runtime_patch import apply
            apply(root)
        with phase("executable_validation"):
            if run_command(str(binary), "--version", timeout=60) != version:
                raise RuntimeError("Kimi staged version mismatch")
            help_text = run_command(str(binary), "web", "--help", timeout=60)
            if "--no-open" not in help_text:
                raise RuntimeError("Kimi web server contract changed")
        marker.write_text(version + "\n")
        return root

    def link(self) -> Path:
        return provider_entry("kimi_runtime_current")


class DshRuntime(ProviderRuntime):
    """DSH and its LLM package are released separately; the bridge comes from the Portal tree."""

    PACKAGES = ("@deepseek-ai/dsh", "@deepseek-ai/dsh-llm")

    def available(self) -> tuple[str, ...]:
        return tuple(npm_version(package, "next") for package in self.PACKAGES)

    def installed(self) -> tuple[str, ...]:
        try:
            dependencies = json.loads((self.link() / "package.json").read_text())["dependencies"]
            return tuple(str(dependencies[package]) for package in self.PACKAGES)
        except (OSError, KeyError, ValueError):
            return ("0",) * len(self.PACKAGES)

    def version_label(self, versions, *, installed=False):
        if len(versions) != 2 or versions[0] == versions[1]:
            return super().version_label(versions)
        official, llm = versions
        # Reserve the bare official number for the upstream matched pair. A
        # staged variant carries its assigned edition across restarts/rollback.
        editions = {}
        legacy = set()
        for root in RUNTIMES.glob(f"dsh-runtime-{official}-*"):
            if not (root / ".portal-validated").is_file():
                continue
            dependencies = json.loads((root / "package.json").read_text())["dependencies"]
            if dependencies[self.PACKAGES[0]] != official or dependencies[self.PACKAGES[1]] == official:
                continue
            secondary = dependencies[self.PACKAGES[1]]
            marker = root / "PROVIDER_EDITION"
            if marker.is_file():
                editions[secondary] = int(marker.read_text())
            else:
                legacy.add(secondary)
        for secondary in sorted(legacy, key=Version):
            edition = 1
            while edition in editions.values():
                edition += 1
            editions[secondary] = edition
        edition = editions.get(llm, max(editions.values(), default=0) + 1)
        return f"{official}-{edition}"

    def stage(self, versions: tuple[str, ...]) -> Path:
        version, llm = versions
        label = self.version_label(versions)
        root = RUNTIMES / f"dsh-runtime-{version}-{llm}"
        root.mkdir(parents=True, exist_ok=True)
        marker = root / ".portal-validated"
        source = ROOT / "management_portal/dsh_runtime"
        package = json.loads((source / "package.json").read_text())
        package["dependencies"].update(zip(self.PACKAGES, versions))
        # The target LLM release owns its Cordis requirement. The repository's
        # bootstrap pin belongs to the older locked runtime, which may be active.
        package["dependencies"]["@deepseek-ai/cordis"] = json.loads(run_command(
            "npm", "view", f"@deepseek-ai/dsh-llm@{llm}",
            "peerDependencies.@deepseek-ai/cordis", "--json", timeout=135,
        ))
        (root / "package.json").write_text(json.dumps(package, indent=2) + "\n")
        for module in (*source.glob("*.mjs"), *source.glob("*.cjs")):
            shutil.copy2(module, root / module.name)
        # A staged runtime lives outside the code tree. Resolve the shared
        # transport from the installed code, rather than the source wrapper's
        # relative path (which is only valid inside management_portal).
        ipc_module = ROOT / "management_common/local_ipc.cjs"
        if not ipc_module.is_file():
            raise FileNotFoundError(ipc_module)
        (root / "local_ipc.cjs").write_text(
            "module.exports = require(" + json.dumps(str(ipc_module)) + ");\n",
            encoding="utf-8",
        )
        expected_marker = hashlib.sha256(json.dumps(package, sort_keys=True).encode("utf-8")).hexdigest()
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != expected_marker:
            run_command("npm", "install", "--prefix", str(root), "--omit=dev", "--no-audit", "--no-fund", timeout=3600)
        run_command("node", "--check", str(root / "bridge.mjs"), timeout=60)
        run_command("node", "--input-type=module", "-e", "const m=await import(process.argv[1]); if(typeof m.apply !== 'function') process.exit(1)", (root / "bridge.mjs").as_uri(), timeout=90)
        if version != llm:
            (root / "PROVIDER_EDITION").write_text(label.rsplit("-", 1)[1] + "\n")
        marker.write_text(expected_marker + "\n")
        return root

    def link(self) -> Path:
        return provider_entry("dsh_runtime_current")


def runtimes() -> dict[str, ProviderRuntime]:
    """The runtime of every Provider whose runtime Portal installs."""

    from .provider_catalog import AGENT_PROVIDERS

    return {name: runtime for name, definition in AGENT_PROVIDERS.items() if (runtime := definition.updater()) is not None}


def _newer(available: tuple[str, ...], installed: tuple[str, ...]) -> bool:
    return installed == ("0",) or any(Version(new) > Version(old) for new, old in zip(available, installed, strict=True))


def _link(path: Path, target: Path) -> None:
    if sys.platform == "win32":
        _windows_link(path, target)
        return
    if path.exists() and not path.is_symlink():
        raise RuntimeError(f"Refusing to replace non-symlink runtime: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)



def _remove_windows_link(path: Path) -> None:
    if path.is_junction():
        path.rmdir()
    elif path.is_symlink():
        path.unlink()
    elif path.exists():
        raise RuntimeError(f"Refusing to replace non-junction runtime: {path}")


def _windows_link(path: Path, target: Path) -> None:
    if path.exists() and not (path.is_junction() or path.is_symlink()):
        raise RuntimeError(f"Refusing to replace non-junction runtime: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    backup = path.with_name(f".{path.name}.{os.getpid()}.old")
    if temporary.exists() or temporary.is_junction() or backup.exists() or backup.is_junction():
        raise RuntimeError("Stale runtime activation path requires inspection")
    _windows_junction(temporary, target)
    moved = False
    try:
        if path.exists() or path.is_junction() or path.is_symlink():
            path.rename(backup)
            moved = True
        try:
            temporary.rename(path)
        except Exception:
            if moved:
                backup.rename(path)
                moved = False
            raise
        if moved:
            _remove_windows_link(backup)
    finally:
        _remove_windows_link(temporary)


def _active_executions(provider: str) -> int:
    database = portal_database(schema="execution_runtime")
    try:
        with database.transaction() as connection:
            row = connection.execute(
                "SELECT count(*) AS total FROM executions WHERE "
                "(status IN ('claimed', 'running') OR (status='queued' AND "
                "(NULLIF(checkpoint_json::text,'')::jsonb->>'turn_id' IS NOT NULL "
                "OR NULLIF(checkpoint_json::text,'')::jsonb ? 'abandoned'))) "
                "AND NULLIF(request_json::text,'')::jsonb->>'provider' = %s", (provider,),
            ).fetchone()
            return int(row["total"])
    finally:
        database.close()


@diagnosed("service_activation")
def _validated_candidate(provider, root, baseline):
    from .provider_candidate import validate
    if provider == "claude" and root.name.startswith("claude-agent-sdk-"):
        # Pending updates recorded by the SDK-only updater before migration.
        from scripts.claude_app_server_package import stage_release
        found = _claude_sdk(venv_python(root))
        if not found:
            raise ValueError("Incomplete pending Claude SDK")
        root = stage_release(ROOT, RUNTIMES, *found)
    validate(provider, root, baseline)
    return root


@diagnosed("service_activation")
def _activate(provider: str, runtime: ProviderRuntime, root: Path, *, prepare=False) -> bool:
    root = _validated_candidate(provider, root, runtime.activation_link())
    return _activate_verified(provider, runtime, root, prepare=prepare)


def _activate_verified(provider, runtime, root, *, prepare=False):
    with activation_window(provider) as reserved:
        if not reserved:
            return False
        return _switch_if_ready(provider, runtime, root, prepare=prepare)


def _switch_if_ready(provider, runtime, root, *, prepare=False):
    if not runtime.activation_ready(root):
        return False
    if provider != "codex" and _active_executions(provider):
        _prepare_execution_recovery(provider)
    publish_activation_ready(provider, root.name)
    return _switch_runtime(provider, runtime, root, prepare=prepare)


def _prepare_execution_recovery(provider):
    from management_common.society_rpc_client import prepare_provider_runtime_update
    return prepare_provider_runtime_update(
        provider=provider, update_id=uuid.uuid4().hex,
    )


@diagnosed("service_activation")
def _switch_runtime(provider: str, runtime: ProviderRuntime, root: Path, *, prepare=False) -> bool:
    """Select a runtime; existing Codex processes keep their immutable version.

    Codex adopts the selection on its next normal service start. Updating
    must not stop live native work, including clients outside Portal.
    """
    services = () if provider == "codex" else runtime.services
    accounts = () if provider == "codex" else runtime.account_processes()
    controller = runtime.service_control()
    link = runtime.activation_link()
    previous = link.resolve() if link.exists() else None
    _link(link, runtime.link_target(root))
    try:
        for service in services:
            controller.restart(service)
            deadline = time.monotonic() + 45
            while controller.status(service)["active"] != "active":
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Provider service did not become active: {service}")
                time.sleep(.1)
        for account_id in accounts:
            runtime.restart_account_process(account_id)
        if provider != "codex" and (prepare or services or accounts):
            runtime.ensure_ready()
    except Exception as exc:
        rollback_failed = False
        if previous is not None:
            _link(link, previous)
            for service in services:
                try:
                    controller.restart(service)
                except Exception:
                    rollback_failed = True
                    LOGGER.warning("Provider %s rollback service restart failed", provider)
            for account_id in accounts:
                try:
                    runtime.restart_account_process(account_id)
                except Exception:
                    rollback_failed = True
                    LOGGER.warning("Provider %s rollback account restart failed", provider)
        else:
            if sys.platform == "win32":
                _remove_windows_link(link)
            else:
                link.unlink(missing_ok=True)
        failure = exc if isinstance(exc, PreparationError) else PreparationError("service_activation", exc)
        failure.rollback_failed = rollback_failed
        if failure is exc:
            raise
        raise failure from exc
    LOGGER.info("Activated %s Provider runtime %s", provider, root)
    return True


@contextmanager
def _snapshot_access():
    """Hold only while reading/replacing the small status document.

    Windows readers prevent replacement of their open file. This separate
    short lock coordinates them with publication without waiting for the
    installation lock, which can be held for an entire package download.
    """
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with (STATE_ROOT / "provider-updates.snapshot.lock").open("a+b") as lock:
        with lock_stream(lock):
            yield


def _read_state():
    with _snapshot_access():
        try:
            return json.loads((STATE_ROOT / "provider-updates.json").read_text())
        except (FileNotFoundError, ValueError):
            return {}


@contextmanager
def _update_state():
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with (STATE_ROOT / "provider-updates.lock").open("a+") as lock:
        try:
            if not try_lock_descriptor(lock.fileno()):
                raise BlockingIOError
        except BlockingIOError:
            yield None
            return
        path = STATE_ROOT / "provider-updates.json"
        state = _read_state()

        saved_pending = dict(state.get("pending", {}))
        saved_check = state.get("last_checked_at")

        def save():
            nonlocal saved_pending, saved_check
            pending = state.get("pending", {})
            changed = {key for key in saved_pending.keys() | pending.keys() if saved_pending.get(key) != pending.get(key)}
            if saved_check != state.get("last_checked_at"):
                changed.update(pending)  # A new update pass may retry a previously failed candidate.
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            try:
                with _snapshot_access():
                    temporary.write_text(json.dumps(state, sort_keys=True) + "\n")
                    if changed:
                        # Readers take this lock too. Publish before rename so a
                        # writer crash cannot leave a new candidate without a wakeup.
                        notify_pending_updates(changed)
                    temporary.replace(path)
                saved_pending = dict(pending)
                saved_check = state.get("last_checked_at")
            finally:
                temporary.unlink(missing_ok=True)

        yield state, save


def wait_for_installation_unlock(deadline):
    """Join the file lock queue outside the provider window, bounded by the job deadline."""
    released = threading.Event()
    errors = []
    def wait():
        try:
            with (STATE_ROOT / "provider-updates.lock").open("a+") as stream, lock_stream(stream):
                pass
        except OSError as error:
            errors.append(error)
        finally:
            released.set()

    # flock has no timed blocking operation. This waiter only observes release
    # and can never install a candidate after its caller has timed out.
    threading.Thread(target=wait, name="provider-installation-lock", daemon=True).start()
    if not released.wait(max(0, deadline - time.monotonic())):
        return False
    if errors:
        raise errors[0]
    return True


def _version_observation(runtime, installed, available):
    return {"version_format": 2, "installed_version": runtime.version_label(installed, installed=True),
            "available_version": runtime.version_label(available),
            "checked_at": datetime.now(timezone.utc).isoformat(), "check_error": ""}


def _observe_versions(item):
    provider, runtime = item
    installed = ()
    try:
        installed = runtime.installed()
        return provider, _version_observation(runtime, installed, runtime.available())
    except Exception as exc:
        # Package managers may include environment or URL details in exceptions.
        # A version observation only exposes the error category.
        return provider, {**_version_observation(runtime, installed, ()), "check_error": type(exc).__name__}


def provider_runtime_status(*, refresh=False):
    """Read current versions; an explicit refresh only observes upstream versions.

    The install/update pass shares these observations and the same state lock.
    This path never stages packages, changes links or activates a service.
    """
    declared = runtimes()
    if refresh:
        with _update_state() as locked:
            if locked is not None:
                state, save = locked
                with ThreadPoolExecutor(max_workers=max(1, min(8, len(declared))), thread_name_prefix="provider-version") as pool:
                    state["observations"] = dict(pool.map(_observe_versions, declared.items()))
                state["last_checked_at"] = time.time()
                save()
    try:
        state = _read_state()
    except (OSError, ValueError):
        state = {}
    result = {}
    for provider, runtime in declared.items():
        observed = (state.get("observations") or {}).get(provider) or {}
        if provider in {"claude", "kimi", "dsh"} and observed.get("version_format") != 2:
            observed = {}
        try:
            installed = runtime.version_label(runtime.installed(), installed=True)
        except Exception:
            LOGGER.warning("Installed version of %s unreadable; showing the last observation", provider, exc_info=True)
            installed = observed.get("installed_version", "")
        result[provider] = {"installed_version": installed,
                            "available_version": observed.get("available_version", ""),
                            "checked_at": observed.get("checked_at", ""),
                            "pending_switch": bool((state.get("pending") or {}).get(provider)),
                            "check_error": observed.get("check_error", "")}
    return result


def preparation_status(provider: str) -> dict:
    """Observe preparation without waiting for an installation's file lock."""
    from management_portal.business_tasks import process_is_alive, process_identity, is_active
    try:
        state = _read_state()
    except (OSError, ValueError):
        state = {}
    result = dict(state.get("preparations", {}).get(provider) or {})
    # Installation may complete through deferred activation or outside Portal.
    # Current runtime availability takes precedence over an old task receipt.
    if runtime_available(provider):
        return {"status": "ready", "source": result.get("source", "existing")}
    if not result or result.get("status") == "ready":
        return {"status": "missing"}
    owner = result.get("owner", {})
    if result.get("status") == "preparing" and (not process_is_alive(owner) or
            (owner == process_identity() and not is_active(f"provider-prepare:{provider}"))):
        result.update(status="interrupted", message="环境准备已中断，可以重试。")
    result.pop("owner", None)
    return result


def _runtime_installed(runtime):
    try:
        versions = runtime.installed()
        return bool(versions and all(v and v != "0" for v in versions) and runtime.supports_versions(versions) and runtime.link().exists()
                    and runtime.prerequisites_available() and runtime.installation_current(versions))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return False


def runtime_available(provider: str) -> bool:
    if provider == "provider_api":
        return True
    runtime = runtimes().get(provider)
    if runtime is None:
        return False
    try:
        return _runtime_installed(runtime) and runtime.ready()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return False


def prepare_runtime(provider: str, *, background: bool = True) -> dict:
    """Reuse an installed runtime or prepare only the selected Provider.

    The same lock, staging and activation owner is used by scheduled updates.
    Work survives a page closing; interrupted processes can be retried.
    """
    from management_portal import business_tasks
    if provider == "provider_api":
        return {"status": "ready", "source": "not_required"}
    runtime = runtimes().get(provider)
    if runtime is None:
        raise ValueError("Unknown Provider runtime")
    if runtime_available(provider):
        return {"status": "ready", "source": "existing"}
    task_id = f"provider-prepare:{provider}"
    with _update_state() as locked:
        if locked is None:
            return {"status": "busy", "message": "运行环境正在准备或更新，请稍后重试。"}
        state, save = locked
        state.setdefault("preparations", {})[provider] = {
            "status": "preparing", "owner": business_tasks.process_identity(),
            "started_at": datetime.now(timezone.utc).isoformat(), "phase": "prerequisites",
            "updated_at": datetime.now(timezone.utc).isoformat()}
        save()

    def work():
        with _update_state() as locked:
            if locked is None:
                return {"status": "busy", "message": "运行环境正在准备或更新，请稍后重试。"}
            state, save = locked
            result = state.setdefault("preparations", {}).setdefault(provider, {})
            def report_phase(name):
                result.update(phase=name, updated_at=datetime.now(timezone.utc).isoformat())
                save()
            try:
                report_phase("prerequisites")
                with phase("prerequisites"):
                    runtime.prepare_prerequisites()
                if _runtime_installed(runtime):
                    report_phase("service_activation")
                    with phase("service_activation"):
                        runtime.ensure_ready()
                    result.update(status="ready", source="existing")
                else:
                    pending_root = state.get("pending", {}).get(provider)
                    if pending_root and (Path(pending_root) / ".portal-validated").is_file():
                        root = Path(pending_root)
                    else:
                        report_phase("download")
                        with phase("download"):
                            versions = runtime.available()
                        report_phase("dependency_install")
                        with phase("dependency_install"):
                            root = runtime.stage(versions)
                    state.setdefault("pending", {})[provider] = str(root)
                    report_phase("service_activation")
                    if _activate(provider, runtime, root, prepare=True):
                        state["pending"].pop(provider, None)
                        result.update(status="ready", source="installed")
                    else:
                        result.update(status="pending", message="运行环境已准备，正在等待自动切换。")
            except Exception as exc:
                failure = exc if isinstance(exc, PreparationError) else PreparationError("service_activation", exc)
                result.update(status="failed", **failure.public())
                LOGGER.warning("Provider %s preparation failed at %s (%s)", provider, failure.phase, failure.kind)
            if result.get("status") == "ready":
                for field in ("phase", "diagnostic", "message"):
                    result.pop(field, None)
            result["updated_at"] = datetime.now(timezone.utc).isoformat()
            save()
            public = {key: value for key, value in result.items() if key != "owner"}
        if public["status"] == "pending":
            activate_pending_provider(provider, str(root), time.monotonic() + ACTIVATION_WAIT_SECONDS)
            receipt = _read_state().get("preparations", {}).get(provider, public)
            return {key: value for key, value in receipt.items() if key != "owner"}
        return public
    if not background:
        return work()
    business_tasks.start(task_id, work)
    return {"status": "preparing"}


def run_update_pass() -> dict[str, object]:
    """Run once; the file lock protects overlapping Resident/manual invocations."""
    result: dict[str, object] = {"checked": {}, "activated": [], "pending": [], "errors": {}}
    with _update_state() as locked:
        if locked is None:
            return {"busy": True}
        state, save = locked
        pending = state.setdefault("pending", {})

        now = time.time()
        for provider, runtime in runtimes().items():
            try:
                with phase("download"):
                    available = runtime.available()
                installed = runtime.installed()
                state.setdefault("observations", {})[provider] = _version_observation(runtime, installed, available)
                result["checked"][provider] = {"installed": runtime.version_label(installed, installed=True),
                                               "available": runtime.version_label(available)}
                if (not _newer(available, installed) and runtime.supports_versions(installed)
                        and runtime.installation_current(installed)):
                    if pending.pop(provider, None) is not None:
                        save()
                    continue
                RUNTIMES.mkdir(parents=True, exist_ok=True)
                with phase("dependency_install"):
                    root = runtime.stage(available)
                pending[provider] = str(root)
                save()
                if _activate(provider, runtime, root):
                    del pending[provider]
                    result["activated"].append(provider)
                    save()
            except Exception as exc:
                failure = exc if isinstance(exc, PreparationError) else PreparationError("service_activation", exc)
                result["errors"][provider] = failure.public()
                LOGGER.warning("Provider %s update failed at %s", provider, failure.phase)
        state["last_checked_at"] = now
        save()
        result["pending"] = sorted(pending)
        return result


def pending_provider_updates():
    """None means another update may still publish pending work."""
    pending = _read_state().get("pending")
    if pending:
        return pending
    with _update_state() as locked:
        return None if locked is None else dict(locked[0].get("pending", {}))


def _finish_pending(provider, path, apply=None, *, error=None):
    # An updater holding this file lock only ever *tries* the database lock.
    # Never block holding either lock while acquiring the other.
    with _update_state() as locked:
        if locked is None:
            return {"status": "busy"}
        state, save = locked
        if state.get("pending", {}).get(provider) != path:
            return {"status": "superseded"}
        preparation = state.get("preparations", {}).get(provider, {})
        preparing = preparation.get("status") in {"pending", "failed"}
        try:
            if error is not None:
                raise error
            if not apply(preparing):
                return {"status": "pending"}
        except Exception as exc:
            failure = exc if isinstance(exc, PreparationError) else PreparationError("service_activation", exc)
            if preparing:
                preparation.update(status="failed", **failure.public())
                save()
            LOGGER.warning("Provider %s deferred activation failed at %s", provider, failure.phase)
            return {"status": "failed", **failure.public()}
        del state["pending"][provider]
        if preparing:
            preparation.update(status="ready", source="installed", updated_at=datetime.now(timezone.utc).isoformat())
            for field in ("phase", "diagnostic", "message"):
                preparation.pop(field, None)
        save()
        return {"status": "activated"}


def activate_pending_provider(provider, path, deadline):
    """Verify an immutable candidate, then queue without the installation lock."""
    try:
        runtime = runtimes()[provider]
        baseline = runtime.activation_link().resolve()
        root = _validated_candidate(provider, Path(path), baseline)
        while True:
            with activation_window(provider, deadline=deadline) as reserved:
                if not reserved:
                    return {"status": "timeout"}

                def apply(preparing):
                    if runtime.activation_link().resolve() != baseline:
                        return False
                    return _switch_if_ready(provider, runtime, root, prepare=preparing)

                result = _finish_pending(provider, path, apply)
            if deadline is None or result["status"] not in {"pending", "busy"}:
                return result
            if result["status"] == "busy":
                ready = wait_for_installation_unlock(deadline)
            elif runtime.activation_link().resolve() != baseline:
                # First-use preparation also comes here, without an outer watcher.
                baseline = runtime.activation_link().resolve()
                root = _validated_candidate(provider, Path(path), baseline)
                continue
            else:
                ready = runtime.activation_ready(root, deadline=deadline)
            if not ready or time.monotonic() >= deadline:
                return {"status": "timeout"}
    except Exception as exc:
        while True:
            result = _finish_pending(provider, path, error=exc)
            if deadline is None or result["status"] != "busy":
                return result
            if not wait_for_installation_unlock(deadline):
                return {"status": "timeout"}
