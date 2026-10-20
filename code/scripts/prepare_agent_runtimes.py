#!/usr/bin/env python3
"""Prepare native runtime packages declared by execution adapters."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.execution.provider_updates import _link


def npm_executable() -> str:
    """Resolve the native npm launcher, including npm.cmd on Windows."""
    if sys.platform == "win32":
        return shutil.which("npm.cmd") or shutil.which("npm") or "npm.cmd"
    return shutil.which("npm") or "npm"


def _ensure_runtime_link(definition, package: Path) -> None:
    """Bootstrap the shared path once; the System Lineage task owns later switches."""
    runtime = definition.updater()
    if runtime is None:
        return
    current = runtime.link()
    if current.exists() and (current.is_symlink() or current.is_junction()):
        return
    if current.exists():
        raise RuntimeError(f"Refusing to replace non-link {definition.label} runtime: {current}")
    _link(current, runtime.link_target(package))


def main():
    for definition in AGENT_PROVIDERS.values():
        if not definition.runtime_package:
            continue
        package = ROOT / definition.runtime_package
        if all((package / "node_modules" / "@deepseek-ai" / name / "package.json").is_file()
               for name in ("dsh", "cordis", "dsh-llm")):
            _ensure_runtime_link(definition, package)
            print(f"{definition.provider}: runtime ready")
            continue
        subprocess.run([npm_executable(), "install", "--package-lock=false", "--no-audit", "--no-fund"], cwd=package, check=True)
        _ensure_runtime_link(definition, package)
        print(f"{definition.provider}: runtime installed")


if __name__ == "__main__":
    main()
