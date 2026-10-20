"""Reviewed, version-pinned projection assets. No network access at runtime."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from management_common.format_tags import schema_matches
from management_portal.resources import ResourceOperationError


_ROOT = Path(__file__).resolve().parents[1] / "static" / "projection_libraries"


def _error(code: str, message: str, status: int = 400):
    return ResourceOperationError("projection.library_" + code, message, status)


def _relative(path: str) -> str:
    if (not isinstance(path, str) or not path or "\\" in path
            or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts
            or PurePosixPath(path).as_posix() != path):
        raise _error("path", "登记库路径无效")
    return path


def registry() -> dict:
    """Return public metadata keyed by name; paths are relative to the library root.

    Every listed file, including licenses and source distributions, has a SHA-256.
    Only read_library() opens assets. The manifest does not contain host paths.
    """
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))
    if not schema_matches(manifest.get("schema"), expected="tfi.projection-libraries.v1"):
        raise _error("registry", "登记库清单版本无效", 500)
    libraries = manifest["libraries"]
    seen = set()
    for library in libraries.values():
        paths = set()
        for item in library["files"]:
            path = _relative(item["path"])
            if (path in seen or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                    or type(item["size"]) is not int or item["size"] < 0):
                raise _error("registry", "登记库文件记录无效", 500)
            paths.add(path)
            seen.add(path)
        if library["entry"] not in paths or any(item["path"] not in paths for item in library["licenses"]):
            raise _error("registry", "登记库入口或许可证缺失", 500)
    return libraries


def read_library(path: str) -> tuple[bytes, str]:
    """Read an allowlisted asset and check its bytes before handing it to the host."""
    path = _relative(path)
    item = next((item for library in registry().values() for item in library["files"]
                 if item["path"] == path), None)
    if item is None:
        raise _error("not_found", "文件未登记为投影库资产", 404)
    target = _ROOT / path
    if any(parent.is_symlink() for parent in [target, *target.parents] if parent != _ROOT.parent):
        raise _error("integrity", "登记库资产不能是符号链接", 409)
    try:
        content = target.read_bytes()
    except OSError as error:
        raise _error("unavailable", "登记库资产不可读取", 409) from error
    if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise _error("integrity", "登记库资产摘要不匹配", 409)
    return content, item["content_type"]
