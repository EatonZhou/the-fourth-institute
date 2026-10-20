"""Paths owned by an instance, independent of its shared application checkout."""
from pathlib import Path
import hashlib
import os


GENERATED_CODE_DIRECTORIES = frozenset({"node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


def source_inventory(root):
    """Shared source integrity excludes installed packages and import/test caches."""
    root = Path(root)
    result = {}
    for directory, names, files in os.walk(root, followlinks=False):
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
                result[path.relative_to(root).as_posix()] = {
                    "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                    "executable": bool(path.stat().st_mode & 0o111),
                }
    return result


def code_root(target):
    return Path(target.get("shared_code_root") or Path(target["install_root"]) / "code")


def database_root(target):
    root = Path(target["install_root"])
    return root / ("data/database" if target.get("shared_code_root") else "runtime/pg")


def dependencies_root(target):
    if target.get("shared_code_root"):
        return code_root(target).parent / "dependencies"
    return Path(target["install_root"]) / "runtime/dependencies"


def native_dependencies_root(target):
    if target.get("shared_code_root"):
        return dependencies_root(target) / "native"
    return Path(target["install_root"]) / "runtime/native"


def installer_path(target):
    if target.get("shared_code_root"):
        return code_root(target) / "scripts/tfi_release_install.py"
    return Path(target["install_root"]) / "runtime/installer.py"


def validate_shared_code(target):
    if not target.get("shared_code_root"):
        return
    code = code_root(target)
    root = Path(target["install_root"]).resolve()
    if not code.is_absolute() or code.is_symlink() or code != code.resolve():
        raise ValueError("shared_code_root must be a canonical absolute directory")
    if code == root or code in root.parents or root in code.parents:
        raise ValueError("Shared code and instance state must be separate directories")
    if not (code / "scripts/tfi_release_install.py").is_file():
        raise ValueError("Shared Portal source is missing")
    dependencies = dependencies_root(target)
    if dependencies != dependencies.resolve() or dependencies == root or dependencies in root.parents:
        raise ValueError("Shared dependencies must not redirect into instance state")
