"""Find the index sources that changed since a moment, in a child process.

The ResourceService answers requests on several threads.  Every file system
call gives up the interpreter lock and then waits for it again behind the busy
threads, so a walk of a few thousand files that takes a second alone took a
minute inside the service.  The index runs this module as a separate process
and only reads its answer.

It reads one JSON request on standard input and writes one JSON answer:

    {"since": <epoch seconds>,
     "trees": [{"root": path, "max_depth": n, "files": [pattern, ...] | null,
                "only": {"<depth>": [directory name, ...]}}],
     "files": [path, ...],
     "paths": [path, ...]}

A tree lists its root (depth 0) and the directories below it down to
``max_depth``; ``only`` restricts which directory names are listed at a depth.
Hidden names and ``__pycache__`` are skipped.  Listed directories and the
files in them whose name matches a ``files`` pattern (all files when null) are
reported when their inode changed (ctime) at or after ``since``: a write,
creation, rename or permission change sets it, and a directory's changes when
an entry is added or removed.  ``files`` are single files checked the same way.
``paths`` (the indexed records' sources) are resolved and checked for
existence.

    {"changed": [path, ...], "resolved": {path: real path}, "missing": [path, ...]}
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from typing import Any


def _skipped(name: str) -> bool:
    return name.startswith(".") or name == "__pycache__"


def find_changes(request: dict[str, Any]) -> dict[str, Any]:
    since_ns = int(float(request["since"]) * 1_000_000_000)
    changed: list[str] = []

    def changed_since(path: str) -> bool:
        try:
            return os.stat(path).st_ctime_ns >= since_ns
        except OSError:
            return False

    for tree in request.get("trees") or ():
        root = str(tree["root"])
        max_depth = int(tree.get("max_depth") or 0)
        names = tree.get("files")
        patterns = None if names is None else [str(name).lower() for name in names]
        only = {int(depth): set(names) for depth, names in (tree.get("only") or {}).items()}
        if not os.path.isdir(root):
            continue
        if changed_since(root):
            changed.append(root)
        pending = [(root, 0)]
        while pending:
            directory, depth = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    listing = list(entries)
            except OSError:
                continue
            for entry in listing:
                if _skipped(entry.name):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    child_depth = depth + 1
                    if child_depth > max_depth or (child_depth in only and entry.name not in only[child_depth]):
                        continue
                    if changed_since(entry.path):
                        changed.append(entry.path)
                    pending.append((entry.path, child_depth))
                elif patterns is None or any(fnmatch.fnmatchcase(entry.name.lower(), pattern) for pattern in patterns):
                    if changed_since(entry.path):
                        changed.append(entry.path)
    for path in request.get("files") or ():
        if changed_since(str(path)):
            changed.append(str(path))
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for path in request.get("paths") or ():
        path = str(path)
        expanded = os.path.expanduser(path)
        resolved[path] = os.path.realpath(expanded)
        if not os.path.exists(expanded):
            missing.append(path)
    return {
        "changed": sorted(set(changed)),
        "resolved": resolved,
        "missing": sorted(set(missing)),
    }


def main() -> int:
    request = json.loads(sys.stdin.buffer.read() or b"{}")
    sys.stdout.write(json.dumps(find_changes(request), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
