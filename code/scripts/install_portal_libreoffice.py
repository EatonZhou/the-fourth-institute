#!/usr/bin/env python3
"""Install an unprivileged LibreOffice Impress runtime on Debian/Ubuntu.

Uses the host's configured, signed APT package index and verifies downloads with
APT. No system packages are installed or changed. Other platforms can install
LibreOffice normally or set PORTAL_LIBREOFFICE to their soffice executable.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.instance_paths import instance_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=instance_root() / "tools/libreoffice")
    options = parser.parse_args()
    root = options.destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    downloads = root / "packages"
    downloads.mkdir(exist_ok=True)
    plan = subprocess.check_output([
        "apt-get", "--print-uris", "--assume-yes", "--no-install-recommends", "install", "libreoffice-impress",
    ], text=True)
    packages = []
    for line in plan.splitlines():
        if not line.startswith("'"):
            continue
        filename = shlex.split(line)[1]
        name, version, _architecture = filename.rsplit("_", 2)
        packages.append(name + "=" + unquote(version))
    if not packages:
        raise SystemExit("LibreOffice is already installed; use the system executable.")
    subprocess.run(["apt-get", "download", *packages], cwd=downloads, check=True)
    for package in downloads.glob("*.deb"):
        subprocess.run(["dpkg-deb", "--extract", str(package), str(root)], check=True)
    # Package postinst normally copies these defaults with ucf. Do this only
    # inside the private tree, without running system maintainer scripts.
    shutil.copytree(root / "usr/lib/libreoffice/share/.registry", root / "etc/libreoffice/registry", dirs_exist_ok=True)
    for path in root.rglob("*"):
        if path.is_symlink():
            target = os.readlink(path)
            local = root / target.lstrip("/")
            if target.startswith("/") and local.exists():
                path.unlink()
                path.symlink_to(os.path.relpath(local, path.parent))
    # Debian's bootstrap paths are absolute; relocate only this private runtime.
    fundamental = root / "usr/lib/libreoffice/program/fundamentalrc"
    content = fundamental.read_text(encoding="utf-8")
    for directory in ("/usr/lib/libreoffice", "/etc/libreoffice", "/usr/share/java"):
        content = content.replace("file://" + directory, (root / directory.lstrip("/")).as_uri())
    fundamental.write_text(content, encoding="utf-8")
    libraries = [root / "usr/lib/libreoffice/program", root / "usr/lib"]
    libraries += list((root / "usr/lib").glob("*-linux-gnu"))
    wrapper = root / "soffice"
    wrapper.write_text(
        "#!/bin/sh\nexport LD_LIBRARY_PATH=" + shlex.quote(":".join(map(str, libraries)))
        + '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\nexec '
        + shlex.quote(str(root / "usr/lib/libreoffice/program/soffice")) + ' "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    subprocess.run([str(wrapper), "--headless", "--version"], check=True)
    print("LibreOffice preview runtime ready:", wrapper)


if __name__ == "__main__":
    main()
