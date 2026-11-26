"""Install Google's official ACP distribution from the ACP registry, with its checksum."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen
import zipfile
from packaging.version import Version

from management_portal.execution import provider_updates as updates
from management_common.antigravity_platform import platform_key
from management_common.file_permissions import atomic_write_private_bytes
from management_portal.native_agent_transport import NativeAgentUnavailable

REGISTRY = "https://raw.githubusercontent.com/agentclientprotocol/registry/main/antigravity-acp/agent.json"
MACOS_FILE_STORAGE_MINIMUM_VERSION = Version("1.5.2")


def credential_environment() -> dict[str, str]:
    """Use per-account GEMINI_HOME files instead of the shared macOS Keychain."""
    return {"AGY_ACP_FORCE_FILE_STORAGE": "1"} if sys.platform == "darwin" else {}


def _supports_credential_storage(version: str) -> bool:
    return sys.platform != "darwin" or Version(version) >= MACOS_FILE_STORAGE_MINIMUM_VERSION


def _require_credential_storage(version: str) -> None:
    if not _supports_credential_storage(version):
        raise NativeAgentUnavailable(
            f"Antigravity ACP {MACOS_FILE_STORAGE_MINIMUM_VERSION} or newer is required for separate accounts on macOS"
        )


def manifest():
    with urlopen(REGISTRY, timeout=60) as response:
        value = json.load(response)
    if value.get("id") != "antigravity-acp":
        raise ValueError("Unexpected ACP distribution")
    return value


class AntigravityRuntime(updates.ProviderRuntime):
    def available(self):
        platform_key()
        version = manifest()["version"]
        _require_credential_storage(version)
        return (version,)

    def installed(self):
        path = self.link() / "distribution.json"
        if not path.is_file() or not (path.parent / ".portal-validated").is_file():
            return ("0",)
        version = json.loads(path.read_text())["version"]
        return (version,) if _supports_credential_storage(version) else ("0",)

    def prerequisites_available(self):
        platform_key()
        return True

    def link(self):
        return updates.provider_entry("antigravity_runtime_current")

    def stage(self, versions):
        with updates.phase("download"):
            value = manifest()
        Version(value["version"])
        _require_credential_storage(value["version"])
        with updates.phase("download"):
            if tuple(versions) != (value["version"],):
                raise RuntimeError("Antigravity registry changed; check the available version again")
            item = value["distribution"]["binary"][platform_key()]
        if Path(item["cmd"]).is_absolute() or ".." in Path(item["cmd"]).parts:
            raise ValueError("Unsafe Antigravity executable path")
        url = urlsplit(item["archive"])
        if url.scheme != "https" or url.hostname != "dl.google.com":
            raise ValueError("Antigravity must be downloaded from Google's distribution host")
        root = updates.RUNTIMES / f"antigravity-{value['version']}"
        if (root / ".portal-validated").is_file():
            return root
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile() as archive:
            digest = hashlib.sha256()
            with updates.phase("download"):
                with urlopen(item["archive"], timeout=120) as response:
                    while chunk := response.read(1024 * 1024):
                        digest.update(chunk)
                        archive.write(chunk)
            if digest.hexdigest() != item["sha256"]:
                raise ValueError("Antigravity archive checksum mismatch")
            archive.seek(0)
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    target = root / member.filename
                    if not target.resolve().is_relative_to(root.resolve()) or stat.S_ISLNK(member.external_attr >> 16):
                        raise ValueError("Unsafe path in Antigravity archive")
                bundle.extractall(root)
        for name in (item["cmd"], "localharness_external"):
            path = root / name
            if path.is_file():
                path.chmod(path.stat().st_mode | 0o500)
        record = {"version": value["version"], "cmd": item["cmd"], "args": item.get("args", []),
                  "sha256": item["sha256"]}
        (root / "distribution.json").write_text(json.dumps(record) + "\n")
        # Handshake verifies the executable and framing without login or an AI call.
        from management_portal.antigravity_client import AcpClient
        with tempfile.TemporaryDirectory() as scratch:
            env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": scratch, "GEMINI_HOME": scratch,
                   **credential_environment()}
            if sys.platform == "win32":
                # The native binary needs a Windows user profile even for the
                # credential-free initialize handshake. Keep every profile
                # directory in scratch; never inherit an operator's login.
                allowed = {"SYSTEMROOT": "SystemRoot", "WINDIR": "WINDIR", "COMSPEC": "COMSPEC",
                           "PATHEXT": "PATHEXT", "TEMP": "TEMP", "TMP": "TMP"}
                env.update({allowed[key.upper()]: item for key, item in os.environ.items()
                            if key.upper() in allowed})
                env.update(USERPROFILE=scratch, APPDATA=scratch, LOCALAPPDATA=scratch)
            with updates.phase("executable_validation"):
                with AcpClient(command(root), env=env) as client:
                    info = client.initialize()
                    if info.get("agentInfo", {}).get("version") != value["version"]:
                        raise RuntimeError("Antigravity staged version mismatch")
        (root / ".portal-validated").write_text(value["version"] + "\n")
        return root


def command(root: Path | None = None):
    root = root or AntigravityRuntime().link()
    path = root / "distribution.json"
    if not path.is_file():
        raise NativeAgentUnavailable("Antigravity ACP is not installed; prepare the Provider runtime first")
    value = json.loads(path.read_text())
    _require_credential_storage(value["version"])
    return [str(root / value["cmd"]), *value.get("args", []), "--logtostderr"]

# Linux x64 build verified with Portal's authenticated HTTP ToolServer.
CLI_CANARY = "cli-1.5.17"
CLI_DIGEST = "f3f384bd2c15a74a036ebc3b0a2f48695cc6e56523ba0f9524fb6e4c4adb3baa"


def cli_session_marker(root: Path, session_id: str) -> Path:
    return root / "sessions" / hashlib.sha256(session_id.encode()).hexdigest()


def canary_command(root: Path, session_id: str, native: bool, env: dict):
    """The rollout flag applies to new bindings. Each resumed CLI binding stays pinned."""
    marker = cli_session_marker(root, session_id) if session_id else root / "enabled"
    if not marker.is_file():
        return None
    if not native:
        if session_id:
            raise NativeAgentUnavailable("This Full Access session requires a new restricted session")
        return None
    binary = root / "antigravity"
    with binary.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != CLI_DIGEST:
            raise NativeAgentUnavailable("Antigravity CLI differs from the verified HTTP MCP build")
    home = Path(env["HOME"])
    gemini = Path(env["GEMINI_HOME"]).relative_to(home)
    return [str(binary), "acp", "--gemini-dir", str(gemini), "--app-data-dir", "antigravity-acp"]


def pin_cli_session(root: Path, session_id: str):
    atomic_write_private_bytes(cli_session_marker(root, session_id), CLI_CANARY.encode())
