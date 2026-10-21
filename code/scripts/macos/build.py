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
    return subprocess.run(list(map(str, argv)), check=True, **kwargs)


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
                print(name + "\n" + path.read_text(errors="replace")[-6000:], flush=True)
        raise
    finally:
        if destination.exists():
            # Logs have instance-local paths but never contain invitation tokens.
            try:
                command([python, launcher, "--platform", destination, "--unregister"], timeout=120)
            except subprocess.CalledProcessError:
                raise RuntimeError("Native smoke cleanup failed; no release will be produced")


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
        "CFBundleShortVersionString": "0.1.0", "CFBundleVersion": "20261021.1",
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
    build(REPO, parser.parse_args().output)
