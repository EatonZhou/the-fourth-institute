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
from scripts.macos.python_dependencies import bundle as bundle_python_dependencies, cache_directory
from scripts.macos.native_smoke import InstalledSession, verify_background


def command(argv, **kwargs):
    result = subprocess.run(list(map(str, argv)), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace", **kwargs)
    if result.stdout:
        print(result.stdout, flush=True)
    if result.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} exited {result.returncode}:\n{result.stdout[-6000:]}")
    return result


def verify_app(app):
    command(["codesign", "--verify", "--deep", "--strict", "--verbose=2", app])


def build_icon(logo, destination):
    """Build all Finder/Dock resolutions from the existing TFI artwork."""
    with tempfile.TemporaryDirectory(prefix="tfi-icon-") as tmp:
        iconset = Path(tmp) / "tfi.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = points * scale
                suffix = "@2x" if scale == 2 else ""
                target = iconset / f"icon_{points}x{points}{suffix}.png"
                command(["sips", "-z", str(pixels), str(pixels), logo, "--out", target])
        command(["iconutil", "-c", "icns", "-o", destination, iconset])


def seal_app(app):
    # The linker may sign the executable before Info.plist exists. Seal the
    # complete bundle after assembly; a linker signature is not an app signature.
    # Ad-hoc signing checks integrity, but provides no Developer ID or notarization.
    command(["codesign", "--force", "--sign", "-", "--timestamp=none", app])
    verify_app(app)


def verify_dmg_app(dmg):
    # Check the distributed bytes, not only the source directory used by hdiutil.
    with tempfile.TemporaryDirectory(prefix="tfi-dmg-") as tmp:
        mount = Path(tmp) / "mounted"
        command(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, dmg])
        try:
            app = mount / "The Fourth Institute.app"
            verify_app(app)
            command([app / "Contents/MacOS/TheFourthInstitute", "--help"], timeout=60)
            verify_app(app)
        finally:
            command(["hdiutil", "detach", mount])


def smoke(executable, destination, included, *, require_long_rpc_path=False):
    """Exercise the shipped native entry, including stop/unregister and restart."""
    argv = [executable, "--platform", destination, "--headless"]
    cleanup = [executable, "--platform", destination, "--unregister"]
    if not included:
        argv.append("--without-examples")
    failure = None
    session = None
    offline_env = {**os.environ, 'HTTP_PROXY': 'http://127.0.0.1:9', 'HTTPS_PROXY': 'http://127.0.0.1:9',
                   'ALL_PROXY': 'http://127.0.0.1:9', 'NO_PROXY': '127.0.0.1,localhost',
                   'http_proxy': 'http://127.0.0.1:9', 'https_proxy': 'http://127.0.0.1:9',
                   'all_proxy': 'http://127.0.0.1:9', 'no_proxy': '127.0.0.1,localhost'}
    try:
        command(argv, timeout=900, env=offline_env)
        state_path = destination / "instance/desktop/installation.json"
        before = json.loads(state_path.read_text())
        if require_long_rpc_path:
            target = before["target"]
            web_socket = (Path(target["data_roots"]["instances_root"])
                          / target.get("runtime_instance", target["service_prefix"]) / "web-service.sock")
            if len(os.fsencode(web_socket)) < 104:
                raise ValueError("Native smoke did not exercise an overlength Web RPC address")
        manifest = json.loads((destination / ".build/m/engineering/manifest.json").read_text())
        if manifest["include_examples"] != included:
            raise ValueError("Selected seed was not used")
        if (destination / 'instance/example_instance/seed').exists() or any(destination.glob('*-engineering.zip')):
            raise ValueError("Desktop retained a redundant seed or engineering archive copy")
        if before['platform_preflight']['file_verification'] != 'skipped_by_operator':
            raise ValueError("Desktop performed duplicate file verification")
        # Second launch must preserve DB identity and data, not reimport seed.
        command(argv, timeout=240, env=offline_env)
        after = json.loads(state_path.read_text())
        if before["target"] != after["target"] or before["manifest_sha256"] != after["manifest_sha256"]:
            raise ValueError("Repeated launch changed instance identity")
        # Upgrade before registration: new personal Family work legitimately
        # waits for an AI account and must not be discarded to enable an update.
        if not included:
            upgrade_smoke(argv, destination)
        session = InstalledSession(before['target'], included)
        session.register()
        session.connect_and_chat()
        marker = destination / "instance/desktop/data/restart-check.txt"
        marker.write_text("user data survives unregister and restart")
        command(cleanup, timeout=120)
        command(argv, timeout=240, env=offline_env)
        restarted = json.loads(state_path.read_text())
        if (before["target"] != restarted["target"]
                or before["manifest_sha256"] != restarted["manifest_sha256"]
                or marker.read_text() != "user data survives unregister and restart"):
            raise ValueError("Restart after unregister changed instance identity or user data")
        session.login_and_check()
        session.chat()
        verify_background(destination, marker)
    except Exception as exc:
        failure = exc
        runtime = destination / "instance/desktop/runtime"
        for name in ("command-error.log", "resource.log", "resource.error.log", "execution.error.log",
                     "society.log", "society.error.log", "worker.error.log", "web.error.log", "dependency-install.log"):
            path = runtime / name
            if path.is_file():
                detail = name + "\n" + path.read_text(errors="replace")[-6000:]
                print(detail, flush=True)
                annotation = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print("::error title=macOS runtime diagnostic::" + annotation, flush=True)
        raise
    finally:
        if session is not None:
            session.close()
        if destination.exists():
            # Logs have instance-local paths but never contain invitation tokens.
            try:
                command(cleanup, timeout=120)
            except Exception as exc:
                if failure is not None:
                    raise ExceptionGroup("Native smoke and cleanup both failed", [failure, exc]) from None
                raise RuntimeError(f"Native smoke cleanup failed; no release will be produced: {exc}") from exc


def upgrade_smoke(argv, destination):
    """Exercise compatible updates with a real local PostgreSQL row.

    A distinct receipt simulates an older compatible generation. Historical
    schema migrations still require their own release-to-release validation.
    """
    probe = '''
import json, pathlib, sys
platform = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(platform / "code"))
from scripts import tfi_release_install as api
import psycopg
state = json.loads((platform / "instance/desktop/installation.json").read_text())
with psycopg.connect(api.clean_environment(state["target"])["PORTAL_DATABASE_URL"]) as db:
    if sys.argv[2] == "create":
        db.execute("CREATE TABLE public.tfi_desktop_update_probe (value text)")
        db.execute("INSERT INTO public.tfi_desktop_update_probe VALUES ('local data survives')")
    else:
        assert db.execute("SELECT value FROM public.tfi_desktop_update_probe").fetchall() == [("local data survives",)]
'''
    python = destination / "dependencies/python/bin/python3"
    command([python, "-c", probe, destination, "create"])
    marker = destination / "macos-package.json"
    original = json.loads(marker.read_text())
    marker.write_text(json.dumps({**original, "source_commit": "0" * 40}))
    command([*argv, "--upgrade"], timeout=600)
    if json.loads(marker.read_text()) != original:
        raise ValueError("Desktop update did not switch the application receipt")
    command([python, "-c", probe, destination, "check"])


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
    for name in list(env):
        if name.startswith('PIP_'):
            env.pop(name)
    env['PIP_CONFIG_FILE'] = os.devnull
    python_packages = bundle_python_dependencies(repo, payload, cache_directory(repo, 'macos/' + arch),
                                                target='macos/' + arch)
    wheelhouse = payload / 'dependencies/python-wheels'
    command([python, '-I', '-m', 'pip', '--isolated', 'install', '--no-index',
             '--find-links', wheelhouse, '--require-hashes', '--only-binary=:all:',
             '--disable-pip-version-check', '--no-compile', '-r', wheelhouse / 'requirements.lock'], env=env)
    command([python, '-I', '-m', 'pip', '--isolated', 'check', '--disable-pip-version-check'], env=env)
    # Exercise the same local-only recovery path used when preinstalled core
    # packages are missing. A fresh venv cannot reuse packages in the app.
    with tempfile.TemporaryDirectory(prefix='tfi-offline-python-') as tmp:
        environment = Path(tmp) / 'venv'
        command([python, payload / 'code/scripts/bootstrap_portal', 'install', 'core',
                 '--venv', environment, '--wheelhouse', wheelhouse], env=env, timeout=300)
        command([environment / 'bin/python', payload / 'code/scripts/bootstrap_portal',
                 'doctor', 'core', '--tools-only', '--venv', environment, '--json'], env=env)
        command([environment / 'bin/python', '-I', '-c', '''
import json, ssl, OpenSSL, cbor2, cryptography
from webauthn import generate_registration_options, generate_authentication_options, options_to_json
from webauthn import verify_registration_response, verify_authentication_response
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, AuthenticatorTransport,
    PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement)
registration = generate_registration_options(rp_id='localhost', rp_name='TFI',
    user_name='offline-check', user_id=b'offline-check')
authentication = generate_authentication_options(rp_id='localhost')
assert json.loads(options_to_json(registration))['rp']['id'] == 'localhost'
assert json.loads(options_to_json(authentication))['rpId'] == 'localhost'
assert cbor2.loads(cbor2.dumps({'offline': True})) == {'offline': True}
'''], env=env)
    # Check GUI and native imports, not merely package metadata.
    command([python, "-c", "import tkinter,ssl,psycopg,cryptography,psutil; r=tkinter.Tk(); r.update(); r.destroy()"], env=env)
    # Exercise the real Darwin ACL API before starting any services.
    command([python, "-c", """
from pathlib import Path
import os, pwd, socket, socketserver, subprocess, tempfile
from management_common.file_permissions import atomic_write_private_bytes, read_private_bytes, PrivateFileError
from management_common.service_endpoint import LocalServiceSocketMixin, service_endpoint
with tempfile.TemporaryDirectory() as tmp:
    key = Path(tmp).resolve() / 'private/key'
    atomic_write_private_bytes(key, b'fixture')
    assert read_private_bytes(key) == b'fixture'
    owner = pwd.getpwuid(os.geteuid()).pw_name
    subprocess.run(['/bin/chmod', '+a', f'user:{owner} allow read,write', str(key)], check=True)
    assert read_private_bytes(key) == b'fixture'
    # Deny-only entries (including Finder's delete protection) cannot expose data.
    subprocess.run(['/bin/chmod', '+a', 'group:everyone deny execute', str(key)], check=True)
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
    for name in ('example-seed.zip', 'base-seed.zip'):
        (payload / name).unlink()
    receipt = {
        "schema": "tfi.macos-package.v1", "target": "macos/" + arch,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=repo)),
        "python": runtime, "bundled_native_dependencies": bundled,
        "bundled_python_dependencies": python_packages,
        "offline_dependency_validation": "passed: pinned wheels, closure, pip check, fresh venv install and doctor with --no-index",
        "native_preparation_requires_network": False,
        "engineering_sha256": digest(payload / "example-engineering.zip"),
        "base_engineering_sha256": digest(payload / "base-engineering.zip"),
        "engineering_prepared_at_build": True,
        "initialization_requires_network": False,
        "signed": True, "signing_identity": "ad-hoc", "notarized": False,
        "prepared_engineering_validation": "passed",
    }
    (payload / "macos-package.json").write_text(json.dumps(receipt, indent=2) + "\n")
    executables = contents / "MacOS"
    executables.mkdir()
    command(["cc", "-Wall", "-Wextra", "-O2", "-mmacosx-version-min=15.0",
             repo / "code/scripts/macos/launcher.c", "-o", executables / "TheFourthInstitute"])
    build_icon(payload / "branding/logo.png", contents / "Resources/tfi.icns")
    info = {
        "CFBundleIdentifier": "org.thefourthinstitute.desktop",
        "CFBundleName": "The Fourth Institute", "CFBundleDisplayName": "The Fourth Institute",
        "CFBundleExecutable": "TheFourthInstitute", "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "tfi.icns",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": json.loads((repo / "code/scripts/macos/release.json").read_text())["tag"].removeprefix("macos-preview-"),
        "LSMinimumSystemVersion": "15.0", "NSHighResolutionCapable": True,
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    seal_app(contents.parent)
    command([executables / "TheFourthInstitute", "--help"])
    # Drive a real Tk loop through a failed copy and retry before real installs.
    command([python, repo / "code/tests/cases/test_macos_desktop.py",
             "MacDesktopGUI", "MacAppSignature", "MacCodexNative"],
            cwd=payload / "code", env={**env, "PYTHONPATH": str(payload / "code")}, timeout=180)
    receipt["codex_native_smoke"] = "passed: launchd, real Codex initialize/model-list/account-read, short/long socket, cleanup; no authorization or inference"
    # Exercise real user paths, including Unicode and addresses exceeding
    # Darwin's sun_path limit. A short /tmp-only install hid this startup bug.
    with tempfile.TemporaryDirectory(prefix="tfi-mac-", dir="/tmp") as tmp:
        for included in (False, True):
            destination = (Path(tmp) / "独立测试" / ("full" if included else "base")).resolve()
            smoke(executables / "TheFourthInstitute", destination, included, require_long_rpc_path=True)
    receipt["native_installation_smoke"] = "passed: native entry, long Unicode path, base/full, first administrator, AMA/PersonalFamily/project/chat reads, launcher exit survival, unregister/restart with password login, Unicode index worker, background log checks, compatible update with database row, GUI copy/retry"
    receipt["api_only_chat_smoke"] = "passed: loopback fixture only, no Codex account, Harness-only settings, default-model API chat, model catalog/save, persisted chat after service restart; no real model or key"
    verify_app(contents.parent)
    receipt["app_signature_verification"] = "passed"
    (image_root / "Applications").symlink_to("/Applications", target_is_directory=True)
    shutil.copyfile(repo / "code/scripts/macos/README.md", image_root / "README.md")
    dmg = output / ("TheFourthInstitute-macOS-" + ("arm64" if arch == "aarch64" else "x64") + ".dmg")
    command(["hdiutil", "create", "-ov", "-format", "UDZO", "-volname", "The Fourth Institute",
             "-srcfolder", image_root, dmg])
    command(["hdiutil", "verify", dmg])
    verify_dmg_app(dmg)
    receipt["dmg_app_signature_verification"] = "passed"
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
