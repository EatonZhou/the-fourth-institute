#!/usr/bin/env python3
"""Build the Windows preview from explicit public inputs, on Linux or Windows."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from urllib.request import urlopen
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "code"))
from prepare_install import digest, inventory
from scripts.tfi_instance_layout import GENERATED_CODE_DIRECTORIES, source_inventory


def public_seed(repo):
    seed = repo / "instance/example_instance/seed"
    specification = json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
    rows = [row for row in inventory(seed) if row["path"] != "manifest.json"]
    if rows != sorted(specification["inventory"], key=lambda row: row["path"]):
        raise ValueError("Public seed differs from its inventory")
    for name in ("accounts_root", "secrets_dir"):
        directory = seed / specification["engineering"]["files"][name]
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("Public seed contains account or secret files")
    return seed


def stage(repo, destination, archive, *, source_commit=None, source_dirty=None):
    if destination.exists():
        raise ValueError("Use a new staging directory")
    seed = public_seed(repo)
    source_inventory(repo / "code")
    destination.mkdir(parents=True)
    shutil.copytree(repo / "code", destination / "code",
                    ignore=shutil.ignore_patterns(*GENERATED_CODE_DIRECTORIES, "*.pyc", "*.pyo"))
    for name in ("prepare_install.py", "INSTALL.md", "README.md", "README_CN.md"):
        shutil.copyfile(repo / name, destination / name)
    shutil.copyfile(repo / "packaging/windows/README.md", destination / "WINDOWS.md")
    guide = destination / "packaging/windows/README.md"
    guide.parent.mkdir(parents=True)
    shutil.copyfile(repo / "packaging/windows/README.md", guide)
    shutil.copytree(repo / "packaging/windows/branding", destination / "branding")
    with zipfile.ZipFile(destination / "example-seed.zip", "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for row in inventory(seed):
            zipped.write(seed / row["path"], row["path"])
    with tarfile.open(archive) as bundle:
        for entry in bundle.getmembers():
            if not (entry.isfile() or entry.isdir()) or not entry.name.startswith("python/"):
                raise ValueError("Unexpected Python runtime member")
        bundle.extractall(destination / "dependencies", filter="data")
    runtime = destination / "dependencies/python"
    for relative in ("python.exe", "pythonw.exe", "DLLs/_tkinter.pyd", "tcl/tcl8.6/init.tcl",
                     "tcl/tk8.6/tk.tcl", "Lib/venv/__init__.py", "vcruntime140.dll", "LICENSE.txt"):
        if not (runtime / relative).is_file():
            raise ValueError("Incomplete Python runtime: " + relative)
    receipt = {
        "schema": "tfi.windows-package.v1", "target": "windows/x86_64",
        "native_verification": "not_run", "signed": False,
        "source_commit": source_commit if source_commit is not None else subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "source_dirty": source_dirty if source_dirty is not None else bool(subprocess.check_output(
            ["git", "status", "--porcelain", "--", "code", "prepare_install.py", "packaging/windows"], cwd=repo)),
        "source_tree_sha256": hashlib.sha256(json.dumps(source_inventory(repo / "code"), sort_keys=True).encode()).hexdigest(),
        "python": json.loads((repo / "packaging/windows/runtime.json").read_text(encoding="utf-8")),
        "seed_sha256": digest(destination / "example-seed.zip"),
        "initialization_requires_network": True,
    }
    (destination / "windows-package.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--makensis", default="makensis")
    parser.add_argument("--python-archive", type=Path)
    args = parser.parse_args()
    build = REPO / ".build/windows"
    build.mkdir(parents=True, exist_ok=True)
    runtime = json.loads((Path(__file__).with_name("runtime.json")).read_text())
    archive = args.python_archive or build / "python-windows.tar.gz"
    if not archive.exists():
        temporary = archive.with_suffix(".partial")
        with urlopen(runtime["url"], timeout=120) as source, temporary.open("wb") as out:
            shutil.copyfileobj(source, out)
        temporary.replace(archive)
    if archive.stat().st_size != runtime["size"] or digest(archive) != runtime["sha256"]:
        raise ValueError("Python download failed SHA-256 verification")
    payload = build / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    receipt = stage(REPO, payload, archive)
    output = build / "TheFourthInstitute-Setup-x64.exe"
    define = "/D" if os.name == "nt" else "-D"
    subprocess.run([args.makensis, define + "PAYLOAD=" + str(payload),
                    define + "OUTPUT=" + str(output), str(Path(__file__).with_name("setup.nsi"))], check=True)
    receipt.update(exe_sha256=digest(output), exe_bytes=output.stat().st_size)
    (build / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    (build / (output.name + ".sha256")).write_text(receipt["exe_sha256"] + "  " + output.name + "\n")
    print(json.dumps({"path": str(output), "size": receipt["exe_bytes"], "sha256": receipt["exe_sha256"]}))


if __name__ == "__main__":
    main()
