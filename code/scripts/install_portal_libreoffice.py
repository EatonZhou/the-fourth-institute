#!/usr/bin/env python3
"""Install an unprivileged LibreOffice Impress runtime on Debian/Ubuntu.

Uses the host's configured, signed APT package index and verifies downloads with
APT. No system packages are installed or changed. Other platforms can install
LibreOffice normally or set PORTAL_LIBREOFFICE to their soffice executable.
Use --repair after moving an existing private runtime to regenerate its launcher
and bootstrap paths without downloading packages.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.instance_paths import instance_root


def configure_runtime(root: Path) -> Path:
    """Keep package paths relative to this private runtime, even after a move."""
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
    # Both pristine packages and earlier installers use absolute bootstrap URLs.
    # ORIGIN is LibreOffice's URL for the program directory, so neither a host
    # migration nor spaces in the new instance path require another rewrite.
    fundamental = root / "usr/lib/libreoffice/program/fundamentalrc"
    content = fundamental.read_text(encoding="utf-8")
    previous = re.search(r"(?m)^BRAND_BASE_DIR=(file://[^\r\n]*?)/usr/lib/libreoffice$", content)
    prefix = previous.group(1) if previous else "file://"
    for directory, relative in (
        ("/usr/lib/libreoffice", "${ORIGIN}/.."),
        ("/etc/libreoffice", "${ORIGIN}/../../../../etc/libreoffice"),
        ("/usr/share/java", "${ORIGIN}/../../../share/java"),
    ):
        content = content.replace(prefix + directory, relative)
    fundamental.write_text(content, encoding="utf-8")
    wrapper = root / "soffice"
    wrapper.write_text(
        '#!/bin/sh\n'
        'runtime_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || exit 1\n'
        'library_path="$runtime_root/usr/lib/libreoffice/program:$runtime_root/usr/lib"\n'
        'for library_dir in "$runtime_root"/usr/lib/*-linux-gnu; do\n'
        '    if [ -d "$library_dir" ]; then library_path="$library_path:$library_dir"; fi\n'
        'done\n'
        'export LD_LIBRARY_PATH="$library_path${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
        'exec "$runtime_root/usr/lib/libreoffice/program/soffice" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=instance_root() / "tools/libreoffice")
    parser.add_argument("--repair", action="store_true", help="Reconfigure an existing private runtime without downloading")
    options = parser.parse_args()
    root = options.destination.resolve()
    if not options.repair:
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
    wrapper = configure_runtime(root)
    subprocess.run([str(wrapper), "--headless", "--version"], check=True)
    print("LibreOffice preview runtime ready:", wrapper)


if __name__ == "__main__":
    main()
