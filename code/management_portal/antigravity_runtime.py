"""Stage Google's pinned CLI using the SHA-512 from its official bootstrap manifests."""
from __future__ import annotations

import hashlib
import json
import os
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


def distribution():
    system, arch = platform_key().split("-")
    target = f"{system}_{'amd64' if arch == 'x86_64' else 'arm64'}"
    if system == "linux" and list(Path("/lib").glob("libc.musl-*.so.1")):
        target += "_musl"
    return json.loads(MANIFEST.read_text())[target]


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
    def available(self):
        distribution()
        return (VERSION,)

    def supports_versions(self, versions):
        return tuple(versions) == (VERSION,)

    def installed(self):
        return (VERSION,) if self._record(self.link()) else ("0",)

    def prerequisites_available(self):
        platform_key()
        return True

    def link(self):
        return updates.provider_entry("antigravity_runtime_current")

    @staticmethod
    def _record(root):
        try:
            value = json.loads((root / "distribution.json").read_text())
            if (value.get("version") == VERSION and value.get("sha512") == distribution()["sha512"]
                    and binary_path(root).is_file() and (root / ".portal-validated").is_file()):
                return value
        except (OSError, ValueError, AttributeError):
            pass
        return {}

    def stage(self, versions):
        if not self.supports_versions(versions):
            raise ValueError(f"Antigravity provider requires CLI {VERSION}")
        item = distribution()
        root = updates.RUNTIMES / f"antigravity-cli-{VERSION}"
        existing = self._record(root)
        if existing and _digest(binary_path(root)) == existing.get("binary_sha256"):
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
                if client.initialize().get("agentInfo", {}).get("version") != VERSION:
                    raise RuntimeError("Antigravity CLI handshake version mismatch")
            record = {"version": VERSION, "sha512": item["sha512"], "binary_sha256": _digest(binary)}
            (stage / "distribution.json").write_text(json.dumps(record) + "\n")
            (stage / ".portal-validated").write_text(VERSION + "\n")
            for file in (binary, stage / "distribution.json", stage / ".portal-validated"):
                file.replace(root / file.name)
        return root


def verified_binary():
    runtime = AntigravityRuntime()
    root = runtime.link().resolve()
    record = runtime._record(root)
    binary = binary_path(root)
    if not record or _digest(binary) != record.get("binary_sha256"):
        raise NativeAgentUnavailable(f"Prepare the verified Antigravity CLI {VERSION} runtime")
    return binary


def command(env):
    return _cli_args(verified_binary(), env)
