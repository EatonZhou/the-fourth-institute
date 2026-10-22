"""One explicit update pass for shared native Provider runtimes.

The System Lineage schedules a Resident to invoke this pass. Each Provider
whose runtime Portal installs declares how (``AgentProvider.runtime_updater``,
a ``ProviderRuntime``).  Installs are staged in versioned directories;
activation waits for active executions.
"""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import json
import logging
import os
import ctypes
import hashlib
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import traceback
from urllib.request import urlopen

from packaging.version import Version

from management_common.portal_database import portal_database
from management_common.file_lock import try_lock_descriptor
from management_common.process_runtime import run_process
from management_common.portal_paths import path as portal_path, resolve_directory
from management_common.site_config import read_site_config

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
_PROVIDER_DEPLOYMENT = read_site_config().get("deployment", {}).get("providers", {})
RUNTIMES = resolve_directory(_PROVIDER_DEPLOYMENT.get("runtime_root", str(portal_path("staging_root") / "provider-runtimes")))
STATE_ROOT = resolve_directory(_PROVIDER_DEPLOYMENT.get("state_root", str(portal_path("staging_root") / "provider-update-state")))


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
    from scripts.portal_service_control import ServiceControl
    from scripts.portal_service_units import _run as run_unit_command
    def run(*command, check=True):
        if command[:2] == ("systemctl", "--user"):
            return run_unit_command(*command, check=check)
        return run_process(command, check=check, text=True, capture_output=True, timeout=180)
    return ServiceControl(runner=run)


def npm_version(package: str, tag: str) -> str:
    value = run_command("npm", "view", package, f"dist-tags.{tag}", "--json", timeout=135)
    version = json.loads(value)
    if not isinstance(version, str):
        raise ValueError(f"Invalid npm version for {package}: {version!r}")
    Version(version)
    return version


def pypi_version(package: str) -> str:
    with urlopen(f"https://pypi.org/pypi/{package}/json", timeout=90) as response:
        version = json.load(response)["info"]["version"]
    Version(version)
    return version


def linked_version(link: Path, prefix: str) -> str:
    """The version in the name of the directory a runtime link points to."""

    match = re.fullmatch(re.escape(prefix) + r"(.+)", link.resolve().name) if link.exists() else None
    return match.group(1) if match else "0"


class ProviderRuntime:
    """How Portal checks, stages and switches one Provider's shared local runtime.

    A Provider declares its runtime class (``AgentProvider.runtime_updater``).
    ``available`` and ``installed`` give the versions of the packages the
    runtime is built from, in the same order; a newer release of any of them
    stages a new runtime.  Portal's clients run the current runtime through
    ``link``; activation points ``activation_link`` at the staged one
    (``link_target``) and restarts ``services``.  When the two differ, ``link``
    is a program Portal did not install: it is reused but never replaced.
    """

    services: tuple[str, ...] = ()

    @property
    def unit_files(self) -> tuple[str, ...]:
        """Static unit definitions installed before any account starts."""
        return self.services

    def available(self) -> tuple[str, ...]:
        raise NotImplementedError

    def installed(self) -> tuple[str, ...]:
        raise NotImplementedError

    def stage(self, versions: tuple[str, ...]) -> Path:
        raise NotImplementedError

    def link(self) -> Path:
        raise NotImplementedError

    def activation_link(self) -> Path:
        return self.link()

    def link_target(self, root: Path) -> Path:
        return root

    def busy(self) -> bool:
        """The runtime is in use beyond the Executions Portal counts."""

        return False


class CodexRuntime(ProviderRuntime):
    # The deployment owner renders the shared template separately. Account
    # instances are runtime state, not source files or an installation input.
    unit_files = ()

    @property
    def services(self) -> tuple[str, ...]:
        from .account_registrations import registrations
        from .account_hosting import codex_instance_state
        # Disabled registration can still have an existing live process. Update
        # that process too, while preserving accounts deliberately left stopped.
        states = (codex_instance_state(row["account_id"]) for row in registrations("codex"))
        return tuple(state["unit"] for state in states if state["active"] == "active")

    def available(self) -> tuple[str, ...]:
        return (npm_version("@openai/codex", "latest"),)

    def installed(self) -> tuple[str, ...]:
        if sys.platform == "win32":
            return (linked_version(self.link(), "codex-cli-"),)
        return (codex_cli_version(run_command(str(self.link()), "--version", timeout=45)),)

    def stage(self, versions: tuple[str, ...]) -> Path:
        (version,) = versions
        root = RUNTIMES / f"codex-cli-{version}"
        binary = npm_binary(root, "codex")
        marker = root / ".portal-validated"
        if not marker.is_file() or not binary.is_file():
            run_command("npm", "install", "--global", "--prefix", str(root), "--omit=dev", f"@openai/codex@{version}")
        if Version(codex_cli_version(run_command(str(binary), "--version", timeout=60))) != Version(version):
            raise RuntimeError("Codex staged version mismatch")
        run_command(str(binary), "app-server", "--help", timeout=60)
        marker.write_text(version + "\n")
        return root

    def link(self) -> Path:
        managed = self.activation_link()
        if sys.platform == "win32" or _PROVIDER_DEPLOYMENT.get("codex_binary") or managed.is_symlink():
            return managed
        # Until Portal activates its own runtime, reuse a Codex found on PATH.
        # Once activated, the managed link stays the program services run.
        existing = shutil.which("codex")
        return Path(existing) if existing else managed

    def activation_link(self) -> Path:
        if sys.platform == "win32":
            return RUNTIMES / "codex-cli-current"
        configured = _PROVIDER_DEPLOYMENT.get("codex_binary")
        if configured:
            if not isinstance(configured, str) or any(c in configured for c in "\0\n\r"):
                raise ValueError("Codex binary must be a path")
            path = Path(configured).expanduser()
            # Activation replaces this link. Resolve only its parent so an
            # instance can reuse an installed binary without updating that copy.
            return resolve_directory(str(path.parent)) / path.name
        return RUNTIMES / "codex"

    def link_target(self, root: Path) -> Path:
        return root if sys.platform == "win32" else npm_binary(root, "codex")


class ClaudeRuntime(ProviderRuntime):
    unit_files = ("portal-claude-code.service",)

    @property
    def services(self) -> tuple[str, ...]:
        from scripts.portal_service_units import provider_runtime_services
        return provider_runtime_services(self.unit_files[0])

    def available(self) -> tuple[str, ...]:
        return (pypi_version("claude-agent-sdk"),)

    def installed(self) -> tuple[str, ...]:
        return (linked_version(self.link(), "claude-agent-sdk-"),)

    def stage(self, versions: tuple[str, ...], *, root: Path | None = None) -> Path:
        (version,) = versions
        root = root or RUNTIMES / f"claude-agent-sdk-{version}"
        binary = venv_python(root)
        marker = root / ".portal-validated"
        extras = [line.strip() for line in (ROOT / "requirements-claude-code.txt").read_text().splitlines()
                  if line.strip() and not line.startswith("#")
                  and not line.strip().startswith(("claude-agent-sdk==", "claude-agent-sdk>=", "claude-agent-sdk "))
                  and line.strip() != "claude-agent-sdk"]
        requirements = [f"claude-agent-sdk=={version}", *extras]
        # The marker names every installed requirement, so a dependency the
        # daemon gains is installed into an existing runtime as well.
        validated = "\n".join(requirements) + "\n"
        if not binary.is_file():
            run_command(sys.executable, "-m", "venv", str(root))
        if not marker.is_file() or marker.read_text() != validated:
            run_command(str(binary), "-m", "pip", "install", *requirements, timeout=3600)
        run_command(str(binary), "-c", "import claude_agent_sdk as s,sys; assert s.__version__ == sys.argv[1]", version)
        bundled = run_command(str(binary), "-c", "from pathlib import Path; import claude_agent_sdk as s; print(Path(s.__file__).parent/'_bundled'/'claude')")
        run_command(bundled, "--version", timeout=60)
        marker.write_text(validated)
        return root

    def link(self) -> Path:
        return RUNTIMES / "claude-agent-sdk-current"

    def busy(self) -> bool:
        # The daemon also runs turns Claude Code starts by itself.
        from management_portal.claude_code_client import ClaudeCodeClient, ClaudeCodeUnavailable
        try:
            return bool(int(ClaudeCodeClient().health()["active"]))
        except ClaudeCodeUnavailable:
            return False


class KimiRuntime(ProviderRuntime):
    unit_files = ("portal-kimi-code.service",)

    @property
    def services(self) -> tuple[str, ...]:
        from scripts.portal_service_units import provider_runtime_services
        return provider_runtime_services(self.unit_files[0])

    def available(self) -> tuple[str, ...]:
        return (npm_version("@moonshot-ai/kimi-code", "latest"),)

    def installed(self) -> tuple[str, ...]:
        return (linked_version(self.link(), "kimi-code-"),)

    def stage(self, versions: tuple[str, ...]) -> Path:
        (version,) = versions
        root = RUNTIMES / f"kimi-code-{version}"
        binary = npm_binary(root, "kimi")
        marker = root / ".portal-validated"
        if not marker.is_file() or not binary.is_file():
            run_command("npm", "install", "--global", "--prefix", str(root), "--omit=dev", f"@moonshot-ai/kimi-code@{version}")
        if run_command(str(binary), "--version", timeout=60) != version:
            raise RuntimeError("Kimi staged version mismatch")
        help_text = run_command(str(binary), "web", "--help", timeout=60)
        if "--no-open" not in help_text:
            raise RuntimeError("Kimi web server contract changed")
        marker.write_text(version + "\n")
        return root

    def link(self) -> Path:
        return RUNTIMES / "kimi-code-current"


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

    def stage(self, versions: tuple[str, ...]) -> Path:
        version, llm = versions
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
        marker.write_text(expected_marker + "\n")
        return root

    def link(self) -> Path:
        return RUNTIMES / "dsh-runtime-current"


def runtimes() -> dict[str, ProviderRuntime]:
    """The runtime of every Provider whose runtime Portal installs."""

    from .provider_catalog import AGENT_PROVIDERS

    return {name: runtime for name, definition in AGENT_PROVIDERS.items() if (runtime := definition.updater()) is not None}


def _newer(available: tuple[str, ...], installed: tuple[str, ...]) -> bool:
    return any(Version(new) > Version(old) for new, old in zip(available, installed, strict=True))


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


def _windows_junction(path: Path, target: Path) -> None:
    if not target.is_dir():
        raise ValueError("Windows runtime activation requires a staged directory")
    if sys.platform != "win32":
        raise OSError("Windows junctions require Windows")
    from ctypes import wintypes

    resolved = str(target.resolve())
    substitute = ("\\??\\UNC\\" + resolved.lstrip("\\") if resolved.startswith("\\\\")
                  else "\\??\\" + resolved).encode("utf-16-le")
    display = resolved.encode("utf-16-le")
    buffer = substitute + b"\x00\x00" + display + b"\x00\x00"
    reparse = struct.pack("<IHHHHHH", 0xA0000003, 8 + len(buffer), 0,
                          0, len(substitute), len(substitute) + 2, len(display)) + buffer
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.restype = ctypes.c_void_p
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    path.mkdir()
    try:
        handle = api.CreateFileW(str(path), 0x40000000, 7, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value or not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            data = ctypes.create_string_buffer(reparse)
            count = wintypes.DWORD()
            if not api.DeviceIoControl(handle, 0x000900A4, data, len(reparse), None, 0,
                                       ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            api.CloseHandle(handle)
    except Exception:
        path.rmdir()
        raise


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
                "SELECT count(*) AS total FROM executions WHERE status IN ('queued', 'claimed', 'running') "
                "AND request_json::jsonb->>'provider' = %s", (provider,),
            ).fetchone()
            return int(row["total"])
    finally:
        database.close()


def _activate(provider: str, runtime: ProviderRuntime, root: Path) -> bool:
    if _active_executions(provider) or runtime.busy():
        return False
    services = runtime.services
    controller = _service_control()
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
    except Exception:
        if previous is not None:
            _link(link, previous)
            for service in services:
                try:
                    controller.restart(service)
                except Exception:
                    LOGGER.exception("Provider %s rollback restart failed: %s", provider, service)
        else:
            if sys.platform == "win32":
                _remove_windows_link(link)
            else:
                link.unlink(missing_ok=True)
        raise
    LOGGER.info("Activated %s Provider runtime %s", provider, root)
    return True


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
        try:
            state = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            state = {}

        def save():
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            try:
                temporary.write_text(json.dumps(state, sort_keys=True) + "\n")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)

        yield state, save


def _version_observation(installed, available):
    return {"installed_version": " ".join(installed), "available_version": " ".join(available),
            "checked_at": datetime.now(timezone.utc).isoformat(), "check_error": ""}


def _observe_versions(item):
    provider, runtime = item
    installed = ()
    try:
        installed = runtime.installed()
        return provider, _version_observation(installed, runtime.available())
    except Exception as exc:
        # Package managers may include environment or URL details in exceptions.
        # A version observation only exposes the error category.
        return provider, {**_version_observation(installed, ()), "check_error": type(exc).__name__}


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
        state = json.loads((STATE_ROOT / "provider-updates.json").read_text())
    except (OSError, ValueError):
        state = {}
    result = {}
    for provider, runtime in declared.items():
        observed = (state.get("observations") or {}).get(provider) or {}
        try:
            installed = " ".join(runtime.installed())
        except Exception:
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
        state = json.loads((STATE_ROOT / "provider-updates.json").read_text())
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


def runtime_available(provider: str) -> bool:
    if provider == "provider_api":
        return True
    runtime = runtimes().get(provider)
    if runtime is None:
        return False
    try:
        versions = runtime.installed()
        return bool(versions and all(v and v != "0" for v in versions) and runtime.link().exists())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return False


def prepare_runtime(provider: str) -> dict:
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
            "updated_at": datetime.now(timezone.utc).isoformat()}
        save()

    def work():
        with _update_state() as locked:
            if locked is None:
                return
            state, save = locked
            result = state.setdefault("preparations", {}).setdefault(provider, {})
            try:
                if runtime_available(provider):
                    result.update(status="ready", source="existing")
                else:
                    versions = runtime.available()
                    root = runtime.stage(versions)
                    state.setdefault("pending", {})[provider] = str(root)
                    save()
                    if _activate(provider, runtime, root):
                        state["pending"].pop(provider, None)
                        result.update(status="ready", source="installed")
                    else:
                        result.update(status="pending", message="运行环境已准备，等待当前执行结束后启用。")
            except Exception as exc:
                # Package manager output can contain configured endpoints or
                # credentials. Record only where preparation stopped.
                step = next((frame for frame in reversed(traceback.extract_tb(exc.__traceback__))
                             if frame.filename == __file__), None)
                LOGGER.warning("Provider %s preparation failed (%s in %s)", provider, type(exc).__name__,
                               step.name if step else "?")
                result.update(status="failed", message="环境准备失败，请检查下载网络和运行依赖后重试。")
            result["updated_at"] = datetime.now(timezone.utc).isoformat()
            save()
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
                available = runtime.available()
                installed = runtime.installed()
                state.setdefault("observations", {})[provider] = _version_observation(installed, available)
                result["checked"][provider] = {"installed": " ".join(installed), "available": " ".join(available)}
                if not _newer(available, installed):
                    if pending.pop(provider, None) is not None:
                        save()
                    continue
                if runtime.link() != runtime.activation_link():
                    # A reused program is updated by whoever installed it.
                    continue
                RUNTIMES.mkdir(parents=True, exist_ok=True)
                root = runtime.stage(available)
                pending[provider] = str(root)
                save()
                if _activate(provider, runtime, root):
                    del pending[provider]
                    result["activated"].append(provider)
                    save()
            except Exception as exc:
                result["errors"][provider] = str(exc)
                LOGGER.exception("Provider %s update failed; current runtime retained", provider)
        state["last_checked_at"] = now
        save()
        result["pending"] = sorted(pending)
        return result


def activate_pending_once() -> dict[str, object]:
    """Activate staged runtimes after the triggering Resident turn has ended."""
    result: dict[str, object] = {"activated": [], "pending": [], "errors": {}}
    with _update_state() as locked:
        if locked is None:
            return {"busy": True}
        state, save = locked
        pending = state.get("pending") or {}
        declared = runtimes()
        for provider, path in tuple(pending.items()):
            try:
                if provider not in declared:
                    raise ValueError(f"{provider} declares no runtime Portal installs")
                if _activate(provider, declared[provider], Path(path)):
                    del pending[provider]
                    result["activated"].append(provider)
            except Exception as exc:
                result["errors"][provider] = str(exc)
                LOGGER.exception("Provider %s deferred activation failed", provider)
        state["pending"] = pending
        save()
        result["pending"] = sorted(pending)
        return result
