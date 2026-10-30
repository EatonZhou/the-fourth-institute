"""The About panel's statistics: languages, code lines and worktree size, cached briefly."""

from __future__ import annotations

import stat
import time
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from management_portal.repo_workspace_checkout import _checkout, _safe_relative_path, _target
from management_portal.repo_workspace_git import _git_bytes


_LANGUAGE_BY_SUFFIX = {
    ".c": "C",
    ".cc": "C++",
    ".clj": "Clojure",
    ".cljs": "Clojure",
    ".coffee": "CoffeeScript",
    ".cpp": "C++",
    ".cs": "C#",
    ".css": "CSS",
    ".cu": "Cuda",
    ".cuh": "Cuda",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".f": "Fortran",
    ".f90": "Fortran",
    ".fs": "F#",
    ".fsx": "F#",
    ".go": "Go",
    ".groovy": "Groovy",
    ".h": "C",
    ".hh": "C++",
    ".hpp": "C++",
    ".hs": "Haskell",
    ".html": "HTML",
    ".htm": "HTML",
    ".java": "Java",
    ".jl": "Julia",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".less": "Less",
    ".lua": "Lua",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".php": "PHP",
    ".pl": "Perl",
    ".pm": "Perl",
    ".ps1": "PowerShell",
    ".py": "Python",
    ".r": "R",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".sass": "Sass",
    ".scala": "Scala",
    ".scss": "SCSS",
    ".sh": "Shell",
    ".sol": "Solidity",
    ".sql": "SQL",
    ".svelte": "Svelte",
    ".swift": "Swift",
    ".tex": "TeX",
    ".tsx": "TypeScript",
    ".ts": "TypeScript",
    ".vue": "Vue",
    ".zig": "Zig",
}

_LANGUAGE_BY_FILENAME = {
    "cmakelists.txt": "CMake",
    "dockerfile": "Dockerfile",
    "gemfile": "Ruby",
    "makefile": "Makefile",
    "rakefile": "Ruby",
}

_LANGUAGE_COLORS = {
    "C": "#555555",
    "C#": "#178600",
    "C++": "#f34b7d",
    "Clojure": "#db5855",
    "CMake": "#da3434",
    "CSS": "#663399",
    "Cuda": "#3a4e3a",
    "Dart": "#00b4ab",
    "Dockerfile": "#384d54",
    "Elixir": "#6e4a7e",
    "F#": "#b845fc",
    "Fortran": "#4d41b1",
    "Go": "#00add8",
    "Groovy": "#4298b8",
    "Haskell": "#5e5086",
    "HTML": "#e34c26",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "Julia": "#a270ba",
    "Kotlin": "#a97bff",
    "Less": "#1d365d",
    "Lua": "#000080",
    "Makefile": "#427819",
    "Objective-C": "#438eff",
    "Objective-C++": "#6866fb",
    "PHP": "#4f5d95",
    "Perl": "#0298c3",
    "PowerShell": "#012456",
    "Python": "#3572a5",
    "R": "#198ce7",
    "Ruby": "#701516",
    "Rust": "#dea584",
    "Sass": "#a53b70",
    "Scala": "#c22d40",
    "SCSS": "#c6538c",
    "Shell": "#89e051",
    "Solidity": "#aa6746",
    "SQL": "#e38c00",
    "Svelte": "#ff3e00",
    "Swift": "#f05138",
    "TeX": "#3d6117",
    "TypeScript": "#3178c6",
    "Vue": "#41b883",
    "Zig": "#ec915c",
    "Other": "#8c959f",
}


def _language_for_path(relative_path: str) -> str:
    name = PurePosixPath(relative_path).name
    by_name = _LANGUAGE_BY_FILENAME.get(name.casefold())
    if by_name:
        return by_name
    return _LANGUAGE_BY_SUFFIX.get(PurePosixPath(name).suffix.casefold(), "")


def _language_stats(
    checkout: Path,
    directory: str = "",
) -> tuple[list[dict[str, Any]], int]:
    args = ["ls-tree", "-r", "-l", "-z", "HEAD"]
    if directory:
        args.extend(["--", directory])
    raw = _git_bytes(checkout, *args, allowed_returncodes=(0, 128))
    totals: dict[str, int] = {}
    for entry in raw.split(b"\x00"):
        if not entry:
            continue
        metadata, separator, encoded_path = entry.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) < 4 or fields[1] != b"blob":
            continue
        try:
            size = int(fields[3])
        except ValueError:
            continue
        language = _language_for_path(
            encoded_path.decode("utf-8", errors="replace")
        )
        if language and size > 0:
            totals[language] = totals.get(language, 0) + size
    total_bytes = sum(totals.values())
    if not total_bytes:
        return [], 0
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    visible = ordered[:5]
    remaining_bytes = sum(value for _name, value in ordered[5:])
    if remaining_bytes:
        visible.append(("Other", remaining_bytes))
    return [
        {
            "name": name,
            "bytes": size,
            "percent": round(size * 100 / total_bytes, 1),
            "color": _LANGUAGE_COLORS.get(name, _LANGUAGE_COLORS["Other"]),
        }
        for name, size in visible
    ], total_bytes


def _tracked_code_line_count(checkout: Path, directory: str = "") -> int:
    args = ["ls-files", "-z"]
    if directory:
        args.extend(["--", directory])
    raw = _git_bytes(checkout, *args, allowed_returncodes=(0, 128))
    total = 0
    for encoded_path in raw.split(b"\x00"):
        if not encoded_path:
            continue
        relative_path = encoded_path.decode("utf-8", errors="surrogateescape")
        if not _language_for_path(relative_path):
            continue
        path = checkout / relative_path
        try:
            if not stat.S_ISREG(path.lstat().st_mode):
                continue
            file_lines = 0
            last_byte = b""
            binary = False
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    if b"\x00" in chunk:
                        binary = True
                        break
                    file_lines += chunk.count(b"\n")
                    last_byte = chunk[-1:]
        except (OSError, UnicodeError):
            continue
        if binary:
            continue
        total += file_lines + int(bool(last_byte and last_byte != b"\n"))
    return total


def _repository_worktree_size(checkout: Path) -> int:
    """Return the size of tracked and visible untracked worktree files."""
    raw = _git_bytes(
        checkout,
        "ls-files",
        "-co",
        "--exclude-standard",
        "-z",
        allowed_returncodes=(0, 128),
    )
    total = 0
    seen: set[str] = set()
    for encoded_path in raw.split(b"\x00"):
        if not encoded_path:
            continue
        relative_path = encoded_path.decode("utf-8", errors="surrogateescape")
        if relative_path in seen:
            continue
        seen.add(relative_path)
        try:
            file_stat = (checkout / relative_path).lstat()
        except OSError:
            continue
        if stat.S_ISREG(file_stat.st_mode) or stat.S_ISLNK(file_stat.st_mode):
            total += max(0, int(file_stat.st_size))
    return total


@lru_cache(maxsize=256)
def _cached_directory_statistics(checkout_path: str, directory: str, generation: int) -> tuple[list[dict[str, Any]], int, int]:
    checkout = Path(checkout_path)
    languages, language_bytes = _language_stats(checkout, directory)
    return languages, language_bytes, _tracked_code_line_count(checkout, directory)


@lru_cache(maxsize=64)
def _cached_repository_size(checkout_path: str, generation: int) -> int:
    return _repository_worktree_size(Path(checkout_path))


def repo_statistics(repo_id: str, path: str = "") -> dict[str, Any]:
    """Optional About-panel data. Keep worktree scans out of navigation reads."""
    _record, checkout = _checkout(repo_id)
    relative = _safe_relative_path(path, allow_empty=True)
    directory = _target(checkout, relative, must_exist=bool(relative))
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"not a directory: {relative}")
    generation = int(time.monotonic() // 120)
    languages, language_bytes, code_line_count = _cached_directory_statistics(
        str(checkout), relative, generation,
    )
    return {
        "id": repo_id,
        "path": relative,
        "languages": languages,
        "language_bytes": language_bytes,
        "code_line_count": code_line_count,
        "repository_size_bytes": _cached_repository_size(str(checkout), generation),
    }
