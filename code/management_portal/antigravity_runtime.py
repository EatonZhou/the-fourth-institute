"""Stage Google’s CLI using its platform-specific official release manifest."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from urllib.request import urlopen

from management_common.antigravity_platform import platform_key
from management_portal.execution import provider_updates as updates
from management_portal.native_agent_transport import NativeAgentUnavailable

VERSION = "1.5.18"
MANIFEST = Path(__file__).with_name("antigravity_cli_1_5_18.json")


def credential_environment():
    return {"AGY_CLI_FORCE_FILE_TOKEN_STORAGE": "1", "AGY_CLI_DISABLE_AUTO_UPDATE": "true"}


def manifest_platform():
    system, arch = platform_key().split("-")
    target = f"{system}_{'amd64' if arch == 'x86_64' else 'arm64'}"
    if system == "linux" and list(Path("/lib").glob("libc.musl-*.so.1")):
        target += "_musl"
    return target


def distribution():
    return json.loads(MANIFEST.read_text())[manifest_platform()]


def supported_version(value):
    return (isinstance(value, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) is not None
            and updates.Version(value) >= updates.Version(VERSION))


def latest_distribution():
    base = "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests"
    with urlopen(f"{base}/{manifest_platform()}.json", timeout=30) as response:
        item = json.load(response)
    if (not isinstance(item, dict) or not supported_version(item.get("version"))
            or not str(item.get("url", "")).startswith("https://storage.googleapis.com/antigravity-public/antigravity-cli/")
            or not re.fullmatch(r"[0-9a-f]{128}", str(item.get("sha512", "")))):
        raise ValueError("Invalid official Antigravity release manifest")
    return item


def binary_path(root):
    return root / ("agy.exe" if sys.platform == "win32" else "agy")


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _cli_args(binary, env):
    # CLI resolves gemini-dir from the profile home. Keep the native conversation store.
    directory = os.path.relpath(env["GEMINI_HOME"], env["HOME"])
    return [str(binary), "acp", "--process-per-session", "--gemini-dir", directory,
            "--app-data-dir", "antigravity-acp"]


class AntigravityRuntime(updates.ProviderRuntime):
    session_controls_since = "1.5.18"
    release_editions = {VERSION: 2}
    unit_files = ()

    @property
    def services(self):
        from scripts.portal_service_units import provider_account_services
        return provider_account_services("antigravity")

    def available(self):
        return (latest_distribution()["version"],)

    def supports_versions(self, versions):
        return len(versions) == 1 and supported_version(versions[0])

    def installed(self):
        return (self._record(self.link()).get("version", "0"),)

    def prerequisites_available(self):
        platform_key()
        return True

    def link(self):
        return updates.provider_entry("antigravity_runtime_current")

    @staticmethod
    def _record(root):
        try:
            value = json.loads((root / "distribution.json").read_text())
            if (supported_version(value.get("version")) and re.fullmatch(r"[0-9a-f]{128}", str(value.get("sha512", "")))
                    and (value["version"] != VERSION or value["sha512"] == distribution()["sha512"])
                    and binary_path(root).is_file() and (root / ".portal-validated").is_file()):
                return value
        except (OSError, ValueError, AttributeError):
            pass
        return {}

    def stage(self, versions):
        if not self.supports_versions(versions):
            raise ValueError(f"Antigravity provider requires CLI {VERSION}")
        version, = versions
        item = distribution() if version == VERSION else latest_distribution()
        if item.get("version", version) != version:
            raise ValueError("Antigravity release manifest changed during preparation")
        root = updates.RUNTIMES / f"antigravity-cli-{self.version_label(versions)}"
        existing = self._record(root)
        if existing.get("version") == version and existing["sha512"] == item["sha512"] and _digest(binary_path(root)) == existing.get("binary_sha256"):
            self.mark_edition(root, version)
            return root
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as scratch:
            stage = Path(scratch)
            binary = binary_path(stage)
            with tempfile.TemporaryFile() as archive:
                with updates.phase("download"), urlopen(item["url"], timeout=120) as response:
                    digest = hashlib.sha512()
                    while block := response.read(1024 * 1024):
                        digest.update(block)
                        archive.write(block)
                if digest.hexdigest() != item["sha512"]:
                    raise ValueError("Antigravity CLI download checksum mismatch")
                archive.seek(0)
                with binary.open("wb") as output:
                    if item["url"].endswith(".tar.gz"):
                        with tarfile.open(fileobj=archive, mode="r:gz") as bundle:
                            member = bundle.getmember("antigravity")
                            if not member.isfile():
                                raise ValueError("Expected a regular Antigravity executable")
                            with bundle.extractfile(member) as source:
                                shutil.copyfileobj(source, output)
                    else:
                        shutil.copyfileobj(archive, output)
            binary.chmod(0o755)
            from management_portal.antigravity_client import AcpClient
            env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": scratch, "GEMINI_HOME": scratch,
                   **credential_environment()}
            if sys.platform == "win32":
                env.update({k: os.environ[k] for k in ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT") if k in os.environ})
                env.update(USERPROFILE=scratch, APPDATA=scratch, LOCALAPPDATA=scratch, TEMP=scratch, TMP=scratch)
            with updates.phase("executable_validation"), AcpClient(_cli_args(binary, env), env=env, cwd=scratch) as client:
                welcome = client.initialize()
                if welcome.get("agentInfo", {}).get("version") != version:
                    raise RuntimeError("Antigravity CLI handshake version mismatch")
                capabilities = welcome.get("agentCapabilities") or {}
                if (not capabilities.get("loadSession") or not capabilities.get("mcpCapabilities", {}).get("http")
                        or "close" not in (capabilities.get("sessionCapabilities") or {})):
                    raise RuntimeError("Antigravity CLI lacks required session/MCP capabilities")
            record = {"version": version, "sha512": item["sha512"], "binary_sha256": _digest(binary)}
            (stage / "distribution.json").write_text(json.dumps(record) + "\n")
            (stage / ".portal-validated").write_text(version + "\n")
            for file in (binary, stage / "distribution.json", stage / ".portal-validated"):
                file.replace(root / file.name)
            self.mark_edition(root, version)
        return root


@lru_cache(maxsize=4)
def _installed_digest(binary, file_identity):
    """Reuse verification of an unchanged installed binary across account connections."""
    return _digest(binary)


def verified_binary():
    runtime = AntigravityRuntime()
    root = runtime.link().resolve()
    record = runtime._record(root)
    binary = binary_path(root)
    if record:
        stat = binary.stat()
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if not record or _installed_digest(binary, identity) != record.get("binary_sha256"):
        raise NativeAgentUnavailable(f"Prepare the verified Antigravity CLI {VERSION} runtime")
    return binary


def command(env):
    return _cli_args(verified_binary(), env)
