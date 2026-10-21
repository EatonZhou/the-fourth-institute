#!/usr/bin/env python3
"""Build and exercise a native, self-contained macOS preview DMG."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO), str(REPO / "code")]
from prepare_install import digest
from management_common.distribution_targets import identify_host
from scripts.tfi_instance_layout import GENERATED_CODE_DIRECTORIES, source_inventory
from scripts.windows.build import public_seed, bundle_seed, bundle_native_dependencies, verify_prepared_payload


def command(argv, **kwargs):
    result = subprocess.run(list(map(str, argv)), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace", **kwargs)
    if result.stdout:
        print(result.stdout, flush=True)
    if result.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} exited {result.returncode}:\n{result.stdout[-6000:]}")
    return result


def smoke(payload, destination, included):
    """Real DB import, launchd services, HTTP/login, repeat start and teardown."""
    python = payload / "dependencies/python/bin/python3"
    launcher = payload / "code/scripts/tfi_macos_launcher.py"
    argv = [python, launcher, "--bundle", payload, "--platform", destination, "--headless"]
    if not included:
        argv.append("--without-examples")
    try:
        command(argv, timeout=900)
        state_path = destination / "instance/desktop/installation.json"
        before = json.loads(state_path.read_text())
        manifest = json.loads((destination / ".build/example_instance-macos/engineering/manifest.json").read_text())
        if manifest["include_examples"] != included:
            raise ValueError("Selected seed was not used")
        # Second launch must preserve DB identity and data, not reimport seed.
        command(argv, timeout=240)
        after = json.loads(state_path.read_text())
        if before["target"] != after["target"] or before["manifest_sha256"] != after["manifest_sha256"]:
            raise ValueError("Repeated launch changed instance identity")
    except Exception:
        runtime = destination / "instance/desktop/runtime"
        for name in ("command-error.log", "resource.error.log", "execution.error.log", "society.error.log",
                     "worker.error.log", "web.error.log", "dependency-install.log"):
            path = runtime / name
            if path.is_file():
                detail = name + "\n" + path.read_text(errors="replace")[-6000:]
                print(detail, flush=True)
                annotation = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print("::error title=macOS runtime diagnostic::" + annotation, flush=True)
        raise
    finally:
        if destination.exists():
            # Logs have instance-local paths but never contain invitation tokens.
            try:
                command([python, launcher, "--platform", destination, "--unregister"], timeout=120)
            except RuntimeError as exc:
                raise RuntimeError(f"Native smoke cleanup failed; no release will be produced: {exc}") from exc


def build(repo, output):
    host = identify_host()
    if host["platform"] != "macos":
        raise ValueError("DMGs must be built and smoke-tested on a native macOS runner")
    arch = host["architecture"]
    runtime = json.loads((repo / "code/scripts/macos/runtime.json").read_text())[arch]
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "cache"
    cache.mkdir(exist_ok=True)
    archive = cache / "python.tar.gz"
    if not archive.exists():
        with urlopen(runtime["url"], timeout=120) as response, archive.open("wb") as stream:
            shutil.copyfileobj(response, stream)
    if archive.stat().st_size != runtime["size"] or digest(archive) != runtime["sha256"]:
        archive.unlink(missing_ok=True)
        raise ValueError("Python runtime checksum mismatch; removed incomplete download")
    image_root = output / "image"
    if image_root.exists():
        shutil.rmtree(image_root)
    contents = image_root / "The Fourth Institute.app/Contents"
    payload = contents / "Resources/platform"
    payload.mkdir(parents=True)
    source_inventory(repo / "code")
    shutil.copytree(repo / "code", payload / "code",
                    ignore=shutil.ignore_patterns(*GENERATED_CODE_DIRECTORIES, "*.pyc", "*.pyo"))
    for name in ("prepare_install.py", "INSTALL.md", "README.md", "README_CN.md", "LICENSE"):
        shutil.copyfile(repo / name, payload / name)
    shutil.copytree(repo / "code/scripts/windows/branding", payload / "branding")
    seed = public_seed(repo)
    for included, name in ((True, "example-seed.zip"), (False, "base-seed.zip")):
        bundle_seed(seed, payload / name, include_examples=included)
    with tarfile.open(archive) as bundled:
        if any(not entry.name.startswith("python/") for entry in bundled.getmembers()):
            raise ValueError("Unexpected Python archive layout")
        bundled.extractall(payload / "dependencies", filter="data")
    python = payload / "dependencies/python/bin/python3"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    command([python, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
             "--only-binary=:all:", "-r", payload / "code/requirements.txt"], env=env)
    # Check GUI and native imports, not merely package metadata.
    command([python, "-c", "import tkinter,ssl,psycopg,cryptography,psutil; r=tkinter.Tk(); r.update(); r.destroy()"], env=env)
    # Exercise the real Darwin ACL API before starting any services.
    command([python, "-c", """
from pathlib import Path
import os, socket, socketserver, subprocess, tempfile
from management_common.file_permissions import atomic_write_private_bytes, read_private_bytes, PrivateFileError
from management_common.service_endpoint import LocalServiceSocketMixin, service_endpoint
with tempfile.TemporaryDirectory() as tmp:
    key = Path(tmp).resolve() / 'private/key'
    atomic_write_private_bytes(key, b'fixture')
    assert read_private_bytes(key) == b'fixture'
    subprocess.run(['/bin/chmod', '+a', 'everyone allow read', str(key)], check=True)
    try:
        read_private_bytes(key)
    except PrivateFileError:
        pass
    else:
        raise AssertionError('An extended ACL grant was accepted')
    assert key.read_bytes() == b'fixture'
    class Server(LocalServiceSocketMixin, socketserver.ThreadingUnixStreamServer):
        def __init__(self, path):
            self.endpoint = service_endpoint(path)
            super().__init__(str(path), socketserver.StreamRequestHandler)
    with Server(Path(tmp).resolve() / 'rpc.sock') as server:
        server.socket.setblocking(False)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(server.server_address)
            accepted, _ = server.get_request()
            with accepted:
                assert os.get_blocking(accepted.fileno())
                assert not os.get_blocking(server.socket.fileno())
                client.sendall(b'fixture')
                assert accepted.recv(7) == b'fixture'
"""], cwd=payload / "code", env=env)
    command([python, payload / "code/scripts/bootstrap_portal", "doctor", "core", "--tools-only",
             "--venv", python.parent.parent, "--json"], env=env)
    bundled = bundle_native_dependencies(repo, payload, cache, target="macos/" + arch)
    verify_prepared_payload(payload, target_platform="macos")
    receipt = {
        "schema": "tfi.macos-package.v1", "target": "macos/" + arch,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repo)),
        "python": runtime, "bundled_native_dependencies": bundled,
        "seed_sha256": digest(payload / "example-seed.zip"),
        "base_seed_sha256": digest(payload / "base-seed.zip"),
        "initialization_requires_network": False, "signed": False,
        "prepared_engineering_validation": "passed",
    }
    (payload / "macos-package.json").write_text(json.dumps(receipt, indent=2) + "\n")
    # Keep socket paths short even on runners with a long checkout/temp path.
    with tempfile.TemporaryDirectory(prefix="tfi-mac-", dir="/tmp") as tmp:
        for included in (False, True):
            destination = (Path(tmp) / ("full" if included else "base")).resolve()
            smoke(payload, destination, included)
    receipt["native_installation_smoke"] = "passed: base/full, login, repeat start, stop/unregister"
    executables = contents / "MacOS"
    executables.mkdir()
    command(["cc", "-Wall", "-Wextra", "-O2", "-mmacosx-version-min=15.0",
             repo / "code/scripts/macos/launcher.c", "-o", executables / "TheFourthInstitute"])
    info = {
        "CFBundleIdentifier": "org.thefourthinstitute.desktop",
        "CFBundleName": "The Fourth Institute", "CFBundleDisplayName": "The Fourth Institute",
        "CFBundleExecutable": "TheFourthInstitute", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": json.loads((repo / "code/scripts/macos/release.json").read_text())["tag"].removeprefix("macos-preview-"),
        "LSMinimumSystemVersion": "15.0", "NSHighResolutionCapable": True,
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    command([executables / "TheFourthInstitute", "--help"])
    (image_root / "Applications").symlink_to("/Applications", target_is_directory=True)
    shutil.copyfile(repo / "code/scripts/macos/README.md", image_root / "README.md")
    dmg = output / ("TheFourthInstitute-macOS-" + ("arm64" if arch == "aarch64" else "x64") + ".dmg")
    command(["hdiutil", "create", "-ov", "-format", "UDZO", "-volname", "The Fourth Institute",
             "-srcfolder", image_root, dmg])
    command(["hdiutil", "verify", dmg])
    receipt.update(dmg_sha256=digest(dmg), dmg_bytes=dmg.stat().st_size)
    (output / (dmg.name + ".sha256")).write_text(receipt["dmg_sha256"] + "  " + dmg.name + "\n")
    (output / (dmg.stem + ".json")).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"path": str(dmg), "sha256": receipt["dmg_sha256"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / ".build/macos")
    try:
        build(REPO, parser.parse_args().output)
    except Exception as exc:
        message = str(exc)[-12000:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::error title=macOS native build failed::" + message, flush=True)
        raise
