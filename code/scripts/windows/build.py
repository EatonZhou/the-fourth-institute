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
import tempfile
from urllib.request import urlopen
import zipfile

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "code"))
from prepare_install import digest, inventory
from scripts.tfi_instance_layout import GENERATED_CODE_DIRECTORIES, source_inventory
from scripts.tfi_release_native_dependencies import artifact_name, download, unpack, package_root
from scripts.tfi_seed_selection import plan


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


def bundle_seed(seed, archive, *, include_examples):
    """Filter before packaging: the desktop never extracts unselected files."""
    specification = json.loads((seed / 'manifest.json').read_text(encoding='utf-8'))
    manifest, sql, excluded = plan(seed, specification['engineering'], include_examples=include_examples)
    manifest['include_examples'] = include_examples
    rows = [row for row in specification['inventory'] if not any(
        row['path'] == p or row['path'].startswith(p + '/') for p in excluded)]
    if sql is not None:
        sql = sql.encode('utf-8')
        rows = [{**row, 'size': len(sql), 'sha256': hashlib.sha256(sql).hexdigest()}
                if row['path'] == manifest['database']['path'] else row for row in rows]
    specification.update(engineering=manifest, inventory=rows)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for row in rows:
            if sql is not None and row['path'] == manifest['database']['path']:
                zipped.writestr(row['path'], sql)
            else:
                zipped.write(seed / row['path'], row['path'])
        zipped.writestr('manifest.json', json.dumps(specification, ensure_ascii=False, indent=2) + '\n')


def bundle_engineering(engineering, archive):
    """Ship the already verified candidate, avoiding on-device assembly."""
    manifest = json.loads((engineering / 'manifest.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        for row in manifest['inventory']:
            zipped.write(engineering / row['path'], row['path'])
        zipped.write(engineering / 'manifest.json', 'manifest.json')


def verify_prepared_payload(platform, *, target_platform="windows"):
    """Exercise packaged code and seed through the real prepare/validate path."""
    with tempfile.TemporaryDirectory(prefix="verify-" + target_platform + "-", dir=platform.parent) as temporary:
        root = Path(temporary)
        shutil.copytree(platform / "code", root / "code")
        for name in ("prepare_install.py", "INSTALL.md"):
            shutil.copyfile(platform / name, root / name)
        env = os.environ.copy()
        env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        for options in ([], ['--without-examples']):
            seed = root / 'instance/example_instance/seed'
            if seed.exists():
                shutil.rmtree(seed)
            with zipfile.ZipFile(platform / ('base-seed.zip' if options else 'example-seed.zip')) as zipped:
                zipped.extractall(seed)
            subprocess.run([sys.executable, str(root / "prepare_install.py"), "--platform", target_platform, *options],
                           cwd=root, env=env, check=True)
            if target_platform == 'windows':
                bundle_engineering(root / '.build/w/engineering',
                                   platform / ('base-engineering.zip' if options else 'example-engineering.zip'))


def bundle_native_dependencies(repo, destination, native_cache, *, target="windows/x86_64"):
    """Ship the same pinned archives consumed by the normal installer."""
    bundled = []
    packages = json.loads((repo / "code/config/release-native-dependencies.json").read_text(encoding="utf-8"))
    archives = destination / "dependencies/native/archives"
    archives.mkdir(parents=True)
    for name, package in sorted(packages["packages"][target].items()):
        entry = dict(package, name=name, platform=target)
        filename = artifact_name(entry)
        cached = download(entry, native_cache / filename)
        with tempfile.TemporaryDirectory(prefix="verify-native-", dir=native_cache) as tmp:
            tree = Path(tmp) / "contents"
            unpack(cached, tree, zipped=entry["url"].endswith(".zip"))
            package_root(tree, entry)
        shutil.copyfile(cached, archives / filename)
        bundled.append({**entry, "path": "dependencies/native/archives/" + filename,
                        "size": cached.stat().st_size})
    return bundled


def stage(repo, destination, archive, *, source_commit=None, source_dirty=None, native_cache=None):
    if destination.exists():
        raise ValueError("Use a new staging directory")
    seed = public_seed(repo)
    source_inventory(repo / "code")
    destination.mkdir(parents=True)
    shutil.copytree(repo / "code", destination / "code",
                    ignore=shutil.ignore_patterns(*GENERATED_CODE_DIRECTORIES, "*.pyc", "*.pyo"))
    for name in ("prepare_install.py", "INSTALL.md", "README.md", "README_CN.md"):
        shutil.copyfile(repo / name, destination / name)
    shutil.copyfile(repo / "code/scripts/windows/README.md", destination / "WINDOWS.md")
    shutil.copytree(repo / "code/scripts/windows/branding", destination / "branding")
    for included, name in ((True, 'example-seed.zip'), (False, 'base-seed.zip')):
        bundle_seed(seed, destination / name, include_examples=included)
    verify_prepared_payload(destination)
    for name in ('example-seed.zip', 'base-seed.zip'):
        (destination / name).unlink()
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
    native_cache = native_cache or repo / ".build/windows/native-cache"
    bundled = bundle_native_dependencies(repo, destination, native_cache)
    receipt = {
        "schema": "tfi.windows-package.v1", "target": "windows/x86_64",
        "native_verification": "not_run", "signed": False,
        "source_commit": source_commit if source_commit is not None else subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "source_dirty": source_dirty if source_dirty is not None else bool(subprocess.check_output(
            ["git", "status", "--porcelain", "--", "code", "prepare_install.py", "code/scripts/windows"], cwd=repo)),
        "source_tree_sha256": hashlib.sha256(json.dumps(source_inventory(repo / "code"), sort_keys=True).encode()).hexdigest(),
        "python": json.loads((repo / "code/scripts/windows/runtime.json").read_text(encoding="utf-8")),
        "engineering_sha256": digest(destination / "example-engineering.zip"),
        "base_engineering_sha256": digest(destination / "base-engineering.zip"),
        "engineering_prepared_at_build": True,
        "initialization_requires_network": True,
        "bundled_native_dependencies": bundled,
        "native_preparation_requires_network": False,
        "prepared_engineering_validation": "passed",
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
