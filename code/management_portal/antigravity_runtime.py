"""Install Google's official ACP distribution from the ACP registry, with its checksum."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen
import zipfile
from packaging.version import Version

from management_portal.execution import provider_updates as updates

REGISTRY = "https://raw.githubusercontent.com/agentclientprotocol/registry/main/antigravity-acp/agent.json"


def platform_key():
    # macOS's global Google keychain entry is not yet verified to isolate accounts.
    os_name = {"linux": "linux", "win32": "windows"}.get(sys.platform)
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(platform.machine().lower())
    if not os_name or not arch:
        raise RuntimeError("Antigravity account isolation is supported on Linux and Windows only")
    return f"{os_name}-{arch}"


def manifest():
    with urlopen(REGISTRY, timeout=60) as response:
        value = json.load(response)
    if value.get("id") != "antigravity-acp":
        raise ValueError("Unexpected ACP distribution")
    return value


class AntigravityRuntime(updates.ProviderRuntime):
    def available(self):
        platform_key()
        return (manifest()["version"],)

    def installed(self):
        path = self.link() / "distribution.json"
        return (json.loads(path.read_text())["version"],) if path.is_file() and (self.link() / ".portal-validated").is_file() else ("0",)

    def prerequisites_available(self):
        platform_key()
        return True

    def link(self):
        return updates.runtime_entry(updates._PROVIDER_DEPLOYMENT, "antigravity_runtime_current",
                                     updates.RUNTIMES / "antigravity-current")

    def stage(self, versions):
        value = manifest()
        Version(value["version"])
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
            env = {"PATH": os.environ.get("PATH", os.defpath), "GEMINI_HOME": scratch}
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
        raise RuntimeError("Antigravity ACP is not installed; prepare the Provider runtime first")
    value = json.loads(path.read_text())
    return [str(root / value["cmd"]), *value.get("args", []), "--logtostderr"]
