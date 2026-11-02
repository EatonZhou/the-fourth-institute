"""Resolve the exact dependency environment required by a code checkout.

Instance configuration never supplies a Python executable. The installer and
developer prepare the environment identified here before starting that code.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from .release_dependencies import STARTUP_PROFILES

GENERATED_CODE_DIRECTORIES = frozenset({"node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
DEPENDENCY_MANIFESTS = ('package.json', 'management_portal/test_framework/tau_runtime/package.json',
                        'management_portal/dsh_runtime/package.json')


def source_inventory(code: Path) -> dict:
    """Code integrity excludes installed packages and import/test caches."""
    code = Path(code)
    result = {}
    for directory, names, files in os.walk(code, followlinks=False):
        names[:] = sorted(name for name in names if name not in GENERATED_CODE_DIRECTORIES)
        for name in [*names, *files]:
            path = Path(directory) / name
            if path.is_symlink() or name == ".git":
                raise ValueError(f"Unexpected link or Git metadata in source: {path}")
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = Path(directory) / name
            with path.open("rb") as stream:
                result[path.relative_to(code).as_posix()] = {
                    "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                    "executable": bool(path.stat().st_mode & 0o111),
                }
    return result


def dependencies_directory(code: Path) -> Path:
    """Dependencies prepared for a code checkout live beside it, never in an instance."""
    return Path(code).parent / "dependencies"


def dependency_source_paths(names) -> list[str]:
    """Fingerprint inputs, equally applicable to a checkout or a Git tree."""
    names = set(names)
    files = sorted(name for name in names if PurePosixPath(name).parent == PurePosixPath('.')
                   and PurePosixPath(name).match('requirements*.txt'))
    files.append('scripts/bootstrap_portal')
    files += [name for name in DEPENDENCY_MANIFESTS if name in names]
    return files


def environment_key_from_sources(sources, *, wheel_lock=None, selected=STARTUP_PROFILES) -> str:
    """Hash exact source bytes; a publisher need not checkout its future Release."""
    digest = hashlib.sha256(json.dumps(sorted(selected)).encode())
    for name in dependency_source_paths(sources):
        digest.update(name.encode())
        digest.update(sources[name])
    if wheel_lock is not None:
        digest.update(b"bundled-python-lock")
        digest.update(wheel_lock)
    return digest.hexdigest()


def environment_key(code: Path, selected=STARTUP_PROFILES) -> str:
    code = Path(code)
    names = [path.name for path in code.glob('requirements*.txt')]
    names += [name for name in DEPENDENCY_MANIFESTS if (code / name).is_file()]
    sources = {name: (code / name).read_bytes() for name in dependency_source_paths(names)}
    wheel_lock = code.parent / "dependencies/python-wheels/requirements.lock"
    return environment_key_from_sources(sources, wheel_lock=wheel_lock.read_bytes() if wheel_lock.is_file() else None,
                                        selected=selected)


def environment_directory(code: Path) -> Path:
    code = Path(code)
    return code.parent / "dependencies" / environment_key(code) / "venv"


def runtime_python(code: Path) -> Path:
    # Keep the lexical venv path; resolve() would bypass its site-packages.
    root = environment_directory(code)
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise FileNotFoundError(f"Prepare the code dependency environment before starting services: {root}")
    return python


def native_directory(code: Path, package: str, host=None) -> Path:
    from .distribution_targets import identify_host
    host = host or identify_host()
    catalog = json.loads((code / "config/release-native-dependencies.json").read_text())
    entry = catalog["packages"][host["platform"] + "/" + host["architecture"]][package]
    return code.parent / "dependencies/native" / (package + "-" + entry["version"] + "-" + entry["sha256"][:12])


def native_binary(code: Path, package: str) -> Path:
    root = native_directory(code, package)
    executable = {"nats": "nats-server", "node": "node", "git": "git"}[package]
    prefix = 'cmd/' if package == 'git' else ("" if package == "nats" or os.name == "nt" else "bin/")
    path = root / (prefix + executable + (".exe" if os.name == "nt" else ""))
    if not path.is_file():
        raise FileNotFoundError(f"Prepare the code native dependency before starting services: {path}")
    return path


def provider_directory(instance=None) -> Path:
    from .instance_manifest import load
    return (instance or load()).code_root().parent / 'dependencies/providers'


def provider_entry(key: str, instance=None) -> Path:
    entries = {
        'codex_binary': 'codex-cli-current' if os.name == 'nt' else 'codex',
        'claude_runtime_current': 'claude-agent-sdk-current',
        'claude_app_server_current': 'claude-app-server-current',
        'kimi_runtime_current': 'kimi-code-current',
        'dsh_runtime_current': 'dsh-runtime-current',
        'opencode_runtime_current': 'opencode-current',
        'antigravity_runtime_current': 'antigravity-current',
    }
    return provider_directory(instance) / entries[key]
