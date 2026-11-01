"""Shared steps of the one-time storage migration of an old-layout instance.

The migration commands (``migrate_instance_home``, ``migrate_instance_staging``,
``migrate_instance_lineages``, ``migrate_instance_database_paths`` and
``migrate_instance_old_directories``) plan by default and change files only with
``--apply``. Apply runs while the instance's services are stopped; every check
of the live state happens in the same command, immediately before the change.

Moves are renames on one filesystem. Nothing is copied, and no backup copy is
written: the instance backup is the way back. A move whose source or target is
not as planned stops the command, and the renames already made by that command
are reversed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Iterable, Iterator

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.instance_manifest import InstanceManifest, load


@dataclass(frozen=True)
class Move:
    """One rename. ``kind`` says why; ``note`` is shown to the reviewer."""

    source: str
    target: str
    kind: str
    note: str = ""


@dataclass
class Report:
    command: str
    root: str
    apply: bool
    moves: list[dict] = field(default_factory=list)
    sections: dict = field(default_factory=dict)

    def add_moves(self, moves: Iterable[Move]) -> None:
        self.moves.extend(asdict(move) for move in moves)

    def document(self) -> dict:
        return {
            "command": self.command,
            "root": self.root,
            "mode": "apply" if self.apply else "dry-run",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "moves": self.moves,
            **self.sections,
        }


def write_report(path: Path | None, report: Report) -> dict:
    document = report.document()
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if path is not None:
        path.write_text(text, encoding="utf-8")
    return document


def read_moves(paths: Iterable[Path]) -> list[Move]:
    """Moves recorded by earlier dry-run or apply reports of these commands."""
    result = []
    for path in paths:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in document.get("moves", []):
            result.append(Move(item["source"], item["target"], item["kind"], item.get("note", "")))
        result.extend(Move(item["source"], item["target"], item["kind"], item.get("note", ""))
                      for item in document.get("aliases", []))
    return result


# -- live state -----------------------------------------------------------------


def instance_units(instance: InstanceManifest) -> set[str]:
    """Registered components plus any loaded unit carrying the instance prefix."""
    if not sys.platform.startswith("linux"):
        raise NotImplementedError("Verify native service ownership before running this migration on another platform")
    units = {instance.service_name(component) + ".service" for component in instance.document["services"]["components"]}
    observed = subprocess.run(
        ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend", instance.service_prefix + "-*.service"],
        check=True, capture_output=True, text=True, timeout=30)
    units.update(line.split()[0] for line in observed.stdout.splitlines() if line.strip())
    return units


def require_offline(instance: InstanceManifest, *, running: Iterable[str] = ()) -> dict[str, str]:
    """Every instance unit except ``running`` components is stopped, with no process left.

    ``running`` components must be active; their main PIDs are returned.
    """
    allowed = {instance.service_name(component) + ".service": component for component in running}
    pids = {}
    for unit in sorted(instance_units(instance)):
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, "--property=LoadState,ActiveState,MainPID,ControlPID,ControlGroup"],
            check=False, capture_output=True, text=True, timeout=30)
        state = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if state.get("LoadState") == "not-found":
            if unit in allowed:
                raise RuntimeError(f"Required service is not installed: {unit}")
            continue
        if result.returncode:
            raise RuntimeError(f"Cannot inspect instance service: {unit}")
        if unit in allowed:
            if state.get("ActiveState") != "active" or state.get("MainPID") in (None, "0"):
                raise RuntimeError(f"{unit} must be running for this step")
            pids[allowed[unit]] = state["MainPID"]
            continue
        if (state.get("ActiveState") not in {"inactive", "failed"}
                or state.get("MainPID") != "0" or state.get("ControlPID") != "0"):
            raise RuntimeError(f"Stop the instance services first: {unit}")
        group = state.get("ControlGroup")
        if group:
            events = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
            if not events.is_file() or "populated 1" in events.read_text():
                raise RuntimeError(f"Instance service still has processes or cannot be verified: {unit}")
    return pids


def require_database_alone(connection) -> None:
    """No other client is connected to the instance database."""
    row = connection.execute(
        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
        "AND backend_type='client backend' AND pid<>pg_backend_pid()").fetchone()
    if row[0]:
        raise RuntimeError("Other database clients are still connected")


def require_protected(pids: Iterable[int]) -> dict[int, str]:
    """Processes the operator must not lose are alive; their cgroups are returned."""
    result = {}
    for pid in pids:
        path = Path("/proc") / str(pid) / "cgroup"
        try:
            result[int(pid)] = path.read_text().strip()
        except OSError:
            raise RuntimeError(f"Protected process {pid} is not running; confirm the PID list") from None
    return result


def processes_using(prefixes: Iterable[Path], *, ignore: Iterable[int] = ()) -> list[dict]:
    """Processes whose cwd, root, executable, open files or mapped files lie under ``prefixes``."""
    roots = [str(Path(prefix)) for prefix in prefixes]
    ignored = {os.getpid(), *map(int, ignore)}

    def inside(text: str) -> str | None:
        text = text.removesuffix(" (deleted)")
        return next((root for root in roots if text == root or text.startswith(root + "/")), None)

    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in ignored:
            continue
        try:
            command = (entry / "comm").read_text().strip()
        except OSError:
            continue
        hits = set()
        for name in ("cwd", "root", "exe"):
            try:
                target = os.readlink(entry / name)
            except OSError:
                continue
            if inside(target):
                hits.add((name, target))
        try:
            for descriptor in (entry / "fd").iterdir():
                try:
                    target = os.readlink(descriptor)
                except OSError:
                    continue
                if inside(target):
                    hits.add(("fd", target))
        except OSError:
            pass
        try:
            for line in (entry / "maps").read_text().splitlines():
                parts = line.split(None, 5)
                if len(parts) == 6 and inside(parts[5]):
                    hits.add(("map", parts[5].removesuffix(" (deleted)")))
        except OSError:
            pass
        for kind, target in sorted(hits):
            found.append({"pid": int(entry.name), "command": command, "use": kind, "path": target})
    return found


# -- moves ----------------------------------------------------------------------


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def check_move(move: Move) -> str | None:
    """Why the rename cannot run now, or None."""
    source, target = Path(move.source), Path(move.target)
    if not lexists(source):
        return "source missing"
    if lexists(target):
        return "target exists"
    if target == source or target.is_relative_to(source):
        return "target inside source"
    anchor = target.parent
    while not anchor.exists():
        anchor = anchor.parent
    if anchor.stat().st_dev != source.lstat().st_dev:
        return "target on another filesystem"
    return None


def apply_moves(moves: Iterable[Move], *, after: Callable[[Move], None] | None = None) -> list[Move]:
    """Rename each source to its target; on failure, reverse this call's renames."""
    done: list[tuple[Move, list[Path]]] = []
    try:
        for move in moves:
            problem = check_move(move)
            if problem:
                raise RuntimeError(f"{problem}: {move.source} -> {move.target}")
            target = Path(move.target)
            created = []
            parent = target.parent
            while not parent.exists():
                created.append(parent)
                parent = parent.parent
            for directory in reversed(created):
                directory.mkdir()
            os.rename(move.source, move.target)
            done.append((move, created))
            if after is not None:
                after(move)
    except BaseException:
        for move, created in reversed(done):
            os.rename(move.target, move.source)
            for directory in created:
                try:
                    directory.rmdir()
                except OSError:
                    break
        raise
    return [move for move, _created in done]


def remove_empty_directories(paths: Iterable[Path]) -> list[str]:
    """Remove each directory if it is empty (deepest first); return those removed."""
    removed = []
    for path in sorted({Path(p) for p in paths}, key=lambda p: len(p.parts), reverse=True):
        if path.is_symlink() or not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            continue
        removed.append(str(path))
    return removed


def tree_size(path: Path) -> int:
    """Bytes used under ``path`` without following links."""
    total = 0
    try:
        info = path.lstat()
    except OSError:
        return 0
    total += info.st_blocks * 512
    if path.is_dir() and not path.is_symlink():
        for directory, folders, files in os.walk(path, followlinks=False):
            for name in (*folders, *files):
                try:
                    total += os.lstat(os.path.join(directory, name)).st_blocks * 512
                except OSError:
                    pass
    return total


# -- path mapping ---------------------------------------------------------------


class PrefixMap:
    """Maps a path under a moved source to the same place under its target.

    The longest matching source wins; a source matches whole path components
    only, so ``/a/bc`` is not under ``/a/b``.
    """

    def __init__(self, pairs: Iterable[tuple[str, str]]):
        self.pairs = sorted({(str(old).rstrip("/") or "/", str(new).rstrip("/") or "/") for old, new in pairs},
                            key=lambda pair: len(pair[0]), reverse=True)

    def __bool__(self) -> bool:
        return bool(self.pairs)

    def match(self, path: str) -> tuple[str, str] | None:
        for old, new in self.pairs:
            if path == old or path.startswith(old + "/"):
                return old, new
        return None

    def map(self, path: str) -> str | None:
        found = self.match(path)
        if found is None:
            return None
        old, new = found
        return new + path[len(old):]

    @classmethod
    def from_moves(cls, moves: Iterable[Move]) -> "PrefixMap":
        return cls((move.source, move.target) for move in moves)


def walk_files(root: Path, *, skip_dirs: Iterable[str] = (".git", "node_modules", "__pycache__", ".venv", "venv")) -> Iterator[Path]:
    skipped = set(skip_dirs)
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if name not in skipped]
        for name in files:
            yield Path(directory) / name


def rewrite_text_files(paths: Iterable[Path], mapping: PrefixMap, *, apply: bool,
                       max_bytes: int = 16 * 1024 * 1024) -> dict:
    """Replace old path prefixes in UTF-8 text files, in place (owner, mode and links kept)."""
    import re

    if not mapping:
        return {"files": [], "replacements": 0}
    pattern = re.compile(
        r"(?<![\w.~-])(?:" + "|".join(re.escape(old) for old, _new in mapping.pairs) + r")(?![\w@+-]|\.\w)")
    replacement = dict(mapping.pairs)
    changed, total, errors = [], 0, []
    needles = [old.encode() for old, _new in mapping.pairs]
    for path in paths:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
                continue
            data = path.read_bytes()
            if b"\0" in data[:8192] or not any(needle in data for needle in needles):
                continue
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        count = 0

        def substitute(match):
            nonlocal count
            count += 1
            return replacement[match.group(0)]

        after = pattern.sub(substitute, text)
        if after == text:
            continue
        total += count
        changed.append({"path": str(path), "replacements": count})
        if apply:
            mode = path.stat().st_mode & 0o7777
            writable = bool(mode & 0o200)
            try:
                if not writable and path.stat().st_uid == os.getuid():
                    path.chmod(mode | 0o200)
                with path.open("r+b") as stream:
                    stream.write(after.encode("utf-8"))
                    stream.truncate()
            except OSError as exc:
                errors.append({'path': str(path), 'error': type(exc).__name__})
            finally:
                if not writable and path.stat().st_uid == os.getuid():
                    path.chmod(mode)
    return {"files": changed, "replacements": total, **({"errors": errors} if errors else {})}


def parse_common(parser) -> None:
    parser.add_argument("--root", type=Path, required=True, help="instance root (its instance.json selects the instance)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="plan only (default)")
    mode.add_argument("--apply", action="store_true", help="check the live state and make the changes")
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    parser.add_argument("--protected-pid", type=int, action="append", default=[],
                        help="a process that must stay alive and is never counted as using a moved path")


def instance_for(args) -> InstanceManifest:
    return load(args.root)
