"""Renaming a Project changes its ID and moves its directory (decision_6591e7793116).

RenameProject moves the Project directory under the instance's configured Projects root and
rewrites every reference Portal keeps to the old ID or the old directory:

* the database, in one transaction over every Portal schema: JSON strings and
  keys equal to the old ID, whole column values equal to it, ``project:<ID>``
  key segments, and paths under the old directory.  Records of what happened
  (conversation events, chat messages, execution records, audits, instruction
  records) stay as they were written;
* the permission trees and entries that name the Project, paths included;
* files under the moved directory that name its old path, the summary and
  boot titles and ``#project("<ID>")`` marks, git worktree links and symlinks
  that point into it, and the provider state kept per working directory
  (Codex trust entries, Claude Code project folders, Kimi sessions).

The Residents of the Project and every Resident working in its directory stop
taking input while it moves and must not be running.  Mounts and services
outside Portal that use the directory block the rename: Portal cannot change
them, and they would break.
"""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

import hashlib
import fnmatch
import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from management_common.portal_database import _database_schema, portal_database
from management_portal import permission_system, project_registry

LOGGER = logging.getLogger(__name__)

SHARE_ROOT = portal_path("share_root")
PROJECTS_ROOT = SHARE_ROOT / "Projects"
HOME = Path.home()
# Execution accounts: <accounts_root>/<provider>/<account_id>, each the Provider's own home.
ACCOUNTS_ROOT = portal_path("accounts_root")
# Places outside Portal whose configuration may run code in a Project.
SYSTEMD_UNIT_DIRS = (HOME / ".config/systemd/user", Path("/etc/systemd/system"))
SERVICE_SCRIPT_ROOTS = (SHARE_ROOT / "Services",)
MOUNTINFO = Path("/proc/self/mountinfo")
# Symlinks into a Project usually live in other Projects and Resident workspaces.
SYMLINK_ROOTS = (PROJECTS_ROOT, SHARE_ROOT / "Agents")
KIMI_HOME = (portal_path("staging_root") / "shared" / "kimi-code-portal")
MAX_REWRITE_BYTES = 16 * 1024 * 1024
SCAN_TIMEOUT_SECONDS = 1800
BUSY_WORK_STATES = frozenset({"running", "stopping"})

PROJECT_ID = project_registry.PROJECT_ID

# Records of what happened keep what was true when they were written.
HISTORY_TABLES = frozenset({
    ("agent", "executions"), ("agent", "payloads"),
    ("agent", "segment_relations"), ("agent", "segments"), ("agent", "sequence_segments"),
    ("agent", "sequences"),
    ("execution_runtime", "executions"), ("execution_runtime", "execution_token_usage_events"),
    ("execution_runtime", "execution_account_usage_hourly"),
    ("lineage", "conversation_events"), ("lineage", "conversation_event_delivery"),
    ("lineage", "conversation_input_states"),
    ("lineage", "resident_execution_work"),
    ("portal_identity", "access_audit"),
    ("resident_context", "snapshots"), ("resident_context", "resident_embeddings"),
    ("resident_context", "routing_eval_cases"), ("resident_context", "routing_eval_submissions"),
    ("resource_content_changes", "content_changes"),
    ("telepus", "audit_events"), ("telepus", "session_history"), ("telepus", "processed_updates"),
    ("workbenches", "generations"), ("workbenches", "versions"),
})
HISTORY_TABLE_NAMES = frozenset({"instruction_executions", "registry_collections"})
HISTORY_SCHEMAS = frozenset({"harness_effectiveness"})
# These facts now share a table with current identity fields, but still record
# what was known at deletion rather than a current Project reference.
HISTORY_COLUMNS = frozenset({("lineage", "residents", "historical_project_id")})
TEXT_TYPES = ("text", "character varying", "json", "jsonb")


class RenameBlocked(Exception):
    """The rename cannot run now; ``details`` names what blocks it."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


@dataclass(frozen=True)
class ProjectRename:
    old_id: str
    new_id: str
    old_root: Path
    new_root: Path
    share_root: Path = SHARE_ROOT

    @property
    def renames_id(self) -> bool:
        return self.old_id != self.new_id

    @property
    def moves(self) -> bool:
        return self.old_root != self.new_root

    def path_pairs(self) -> list[tuple[str, str]]:
        """Old and new spellings of the directory, the longest first."""
        if not self.moves:
            return []
        pairs = list(dict.fromkeys([(str(self.old_root), str(self.new_root)),
                                   (self.old_root.as_posix(), self.new_root.as_posix())]))
        try:
            old_relative = self.old_root.relative_to(self.share_root).as_posix()
            new_relative = self.new_root.relative_to(self.share_root).as_posix()
        except ValueError:
            return pairs
        # The registry and older records write Project paths relative to /share.
        pairs.append(("/" + old_relative, "/" + new_relative))
        return pairs


class Rewriter:
    """Rewrites one value: the old ID where it is a whole value, the old directory in paths."""

    def __init__(self, change: ProjectRename) -> None:
        self.change = change
        pairs = change.path_pairs()
        self._replacement = dict(pairs)
        # A path starts after a separator and ends where a path name could not continue;
        # "/x/share/Projects/A" and "/share/Projects/AB" are other paths.
        self._paths = re.compile(
            r"(?<![\w.~-])(?:" + "|".join(re.escape(old) for old, _new in pairs) + r")(?![\w@+-]|\.\w)"
        ) if pairs else None
        self._segment = re.compile(
            r"(^|:)project:" + re.escape(change.old_id) + r"(?=$|:)"
        ) if change.renames_id else None
        self._mark = re.compile(
            r"#project\(\s*([\"'])" + re.escape(change.old_id) + r"\1\s*\)"
        ) if change.renames_id else None

    def needles(self) -> list[str]:
        """``LIKE`` patterns that every value this rewriter changes matches."""
        texts = {old for old, _new in self.change.path_pairs()}
        if self.change.renames_id:
            texts.add(self.change.old_id)
        # JSON written with ensure_ascii spells non-ASCII text as \\u escapes.
        texts |= {json.dumps(text)[1:-1] for text in list(texts)}
        return sorted("%" + _like_escape(text) + "%" for text in texts)

    def path(self, text: str) -> str:
        if self._paths is None:
            return text
        return self._paths.sub(lambda match: self._replacement[match.group(0)], text)

    def value(self, text: str) -> str:
        """A whole string value or JSON key."""
        if self.change.renames_id and text == self.change.old_id:
            return self.change.new_id
        text = self.path(text)
        if self._segment is not None:
            text = self._segment.sub(lambda match: match.group(1) + "project:" + self.change.new_id, text)
        return text

    def document(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {self.value(key) if isinstance(key, str) else key: self.document(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.document(item) for item in value]
        if isinstance(value, str):
            return self.value(value)
        return value

    def column(self, text: str) -> str:
        """A database column value: a JSON document or a plain string."""
        if text.lstrip()[:1] in ("{", "["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = _NOT_JSON
            if parsed is not _NOT_JSON:
                changed = self.document(parsed)
                return text if changed == parsed else _dump_like(text, parsed, changed)
        return self.value(text)

    def file_text(self, text: str, relative: str) -> str:
        """A text file in the moved directory."""
        text = self.path(text)
        if self._mark is not None:
            text = self._mark.sub(lambda match: f"#project({match.group(1)}{self.change.new_id}{match.group(1)})", text)
        if self.change.renames_id and relative in ("summary.md", "agent_port/boot.md"):
            text = self._retitle(text)
        return text

    def _retitle(self, text: str) -> str:
        """The first heading names the Project (project_seeds)."""
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if line.startswith("# "):
                lines[index] = re.sub(
                    r"(?<![\w.@+-])" + re.escape(self.change.old_id) + r"(?![\w@+-])", self.change.new_id, line,
                )
                break
        return "\n".join(lines)


_NOT_JSON = object()


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _dump_like(original: str, before: Any, after: Any) -> str:
    """Encode ``after`` the way ``original`` (the encoding of ``before``) was written."""
    for options in (
        {"ensure_ascii": False, "separators": (",", ":"), "sort_keys": True},
        {"ensure_ascii": False, "separators": (",", ":"), "sort_keys": False},
        {"ensure_ascii": True, "separators": (",", ":"), "sort_keys": True},
        {"ensure_ascii": True, "separators": (",", ":"), "sort_keys": False},
        {"ensure_ascii": False, "separators": (", ", ": "), "sort_keys": False},
        {"ensure_ascii": True, "separators": (", ", ": "), "sort_keys": False},
        {"ensure_ascii": False, "separators": (", ", ": "), "sort_keys": True},
        {"ensure_ascii": True, "separators": (", ", ": "), "sort_keys": True},
        {"ensure_ascii": False, "indent": 2, "sort_keys": False},
        {"ensure_ascii": False, "indent": 2, "sort_keys": True},
    ):
        if json.dumps(before, **options) == original:
            return json.dumps(after, **options)
    return json.dumps(after, ensure_ascii=False, separators=(",", ":"))


# -- planning -----------------------------------------------------------------


def _clean_new_id(value: object) -> str:
    project_id = str(value or "").strip()
    if not PROJECT_ID.fullmatch(project_id):
        raise ValueError("new_project_id 只能使用 1-80 个字母（包括中文）、数字或 _ . @ + -")
    return project_id


def plan(project_id: str, new_project_id: str, *, projects_root: Path | None = None) -> ProjectRename:
    """Check that ``project_id`` can become ``new_project_id`` and where it moves."""
    root = Path(projects_root or PROJECTS_ROOT).resolve(strict=False)
    new_id = _clean_new_id(new_project_id)
    document = project_registry.load()
    projects = [item for item in document.get("projects") or [] if isinstance(item, dict)]
    record = next((item for item in projects if str(item.get("id") or "").strip() == str(project_id or "").strip()), None)
    if record is None:
        raise KeyError(f"Project 不存在：{project_id}")
    old_id = str(record["id"]).strip()
    if any(str(item.get("id") or "").strip() == new_id for item in projects if item is not record):
        raise RenameBlocked(f"Project ID 已存在：{new_id}", new_project_id=new_id)
    # The old ID is rewritten wherever it is a whole value; a word the registry
    # uses for something else would be rewritten there too.
    vocabulary = {str(item) for item in document.get("status_vocab") or []}
    for tag in document.get("tag_vocab") or []:
        if isinstance(tag, dict):
            vocabulary |= {str(tag.get("id") or ""), str(tag.get("folder") or "")}
    if old_id != new_id and old_id in vocabulary:
        raise RenameBlocked(f"Project ID {old_id} 同时是 Project 状态或知识标签，不能按 ID 改名", project_id=old_id)
    old_root = project_registry.runtime_path(record.get("path")) or (root / old_id).resolve(strict=False)
    if root not in old_root.parents:
        raise RenameBlocked(f"Project 目录不在 {root} 中：{old_root}", path=str(old_root))
    new_root = (root / new_id).resolve(strict=False)
    change = ProjectRename(old_id, new_id, old_root, new_root, share_root=root.parent)
    if not change.renames_id and not change.moves:
        raise ValueError("新的 Project ID 与目录都和现在相同")
    if not old_root.is_dir():
        raise RenameBlocked(f"Project 目录不存在：{old_root}", path=str(old_root))
    if change.moves and (new_root.exists() or new_root.is_symlink()):
        raise RenameBlocked(f"目标目录已存在：{new_root}", path=str(new_root))
    if change.moves and new_root.is_relative_to(old_root):
        raise ValueError("Project 目录不能移到自己里面")
    return change


# -- Residents ------------------------------------------------------------------


class Residents:
    """Intake and work state of Residents, changed as the caller."""

    def __init__(self, actor: str, entry: str = "system") -> None:
        self.actor = actor
        self.entry = entry

    def _call(self, instruction: str, arguments: dict[str, Any]) -> Any:
        from management_portal import isa_client

        return isa_client.call(self.actor, instruction, arguments, entry=self.entry)

    def close_intake(self, resident_ids: list[str]) -> list[str]:
        closed: list[str] = []
        for chunk in _chunks(resident_ids, 200):
            result = self._call("OpenIntake", {"resident_ids": chunk, "open": False})
            closed.extend(result.get("changed_resident_ids") or [])
            if result.get("failed"):
                self.open_intake(closed)
                raise RenameBlocked("无法暂停 Resident 接单", failed=result["failed"])
        return closed

    def busy(self, resident_ids: list[str]) -> list[str]:
        busy: list[str] = []
        for chunk in _chunks(resident_ids, 200):
            for item in self._call("ListResidentStates", {"resident_ids": chunk, "include_viewer_state": False}).get("states") or []:
                state = item.get("state") if isinstance(item.get("state"), dict) else item
                if str(state.get("work_state") or "") in BUSY_WORK_STATES:
                    busy.append(str(item.get("resident_id") or ""))
        return busy

    def open_intake(self, resident_ids: list[str]) -> None:
        for chunk in _chunks(resident_ids, 200):
            try:
                self._call("OpenIntake", {"resident_ids": chunk, "open": True})
            except Exception:  # noqa: BLE001 - reported; the Residents stay closed until someone opens them
                LOGGER.exception("Project rename could not reopen intake of %s", chunk)


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _schema_prefix() -> str:
    return _database_schema("x")[:-1]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _current_resident_workspaces(connection: Any, resident_ids: list[str] | None = None):
    """Resolve ownership and extra bindings from live identities in one query."""
    lineage = _quote(_database_schema("lineage"))
    scope = " AND r.resident_id=ANY(%s)" if resident_ids is not None else ""
    parameters = (resident_ids, resident_ids) if resident_ids is not None else ()
    return connection.execute(
        f'SELECT r.resident_id,r.owned_workspace_id AS workspace_id FROM {lineage}.residents r '
        f'WHERE r.deleted_at IS NULL AND r.owned_workspace_id IS NOT NULL{scope} '
        f'UNION SELECT r.resident_id,b.workspace_id FROM {lineage}.residents r '
        f'JOIN {lineage}.resident_workspace_bindings b ON b.resident_id=r.resident_id '
        f'WHERE r.deleted_at IS NULL{scope} ORDER BY resident_id,workspace_id', parameters,
    ).fetchall()


def affected_residents(change: ProjectRename, rewriter: Rewriter) -> list[str]:
    """Residents of the Project's Lineages and Residents whose workspaces are in its directory."""
    lineage = _quote(_database_schema("lineage"))
    database = portal_database(schema="lineage")
    found: set[str] = set()
    with database.snapshot() as connection:
        if not _table_exists(connection, _database_schema("lineage"), "lineages"):
            return []
        for row in connection.execute(
            f"SELECT DISTINCT n.node_id AS resident_id FROM {lineage}.lineage_nodes n "
            f"JOIN {lineage}.lineages l ON l.lineage_id = n.lineage_id "
            f"JOIN {lineage}.residents r ON r.resident_id = n.node_id "
            "WHERE l.project_id = %s AND r.deleted_at IS NULL",
            (change.old_id,),
        ).fetchall():
            found.add(str(row[0]))
        if change.path_pairs():
            from management_portal.society_service.resource_client import workspace_paths_many
            bindings = _current_resident_workspaces(connection)
            paths = workspace_paths_many([row[1] for row in bindings], connection=connection)
            for resident_id, workspace_id in bindings:
                path = paths[workspace_id]
                if rewriter.column(path) != path:
                    found.add(str(resident_id))
    return sorted(item for item in found if item)


def _table_exists(connection: Any, schema: str, table: str) -> bool:
    return connection.execute("SELECT to_regclass(%s)", (f"{_quote(schema)}.{_quote(table)}",)).fetchone()[0] is not None


# -- database -------------------------------------------------------------------


def _portal_schemas(connection: Any) -> dict[str, str]:
    """``{schema: name without the namespace}`` of every Portal schema in the database."""
    prefix = _schema_prefix()
    output: dict[str, str] = {}
    for row in connection.execute("SELECT nspname FROM pg_namespace").fetchall():
        name = str(row[0])
        if name == "information_schema" or name.startswith("pg_"):
            continue
        if prefix:
            if name.startswith(prefix):
                output[name] = name[len(prefix):]
        elif not name.startswith("pytest_"):
            output[name] = name
    return output


def _history(name: str, table: str) -> bool:
    return name in HISTORY_SCHEMAS or table in HISTORY_TABLE_NAMES or (name, table) in HISTORY_TABLES


@dataclass
class DatabaseChanges:
    tables: dict[str, int] = field(default_factory=dict)
    registry: dict[str, set[str]] = field(default_factory=dict)

    def report(self) -> dict[str, Any]:
        return {"tables": dict(sorted(self.tables.items())), "rows": sum(self.tables.values())}


def rewrite_database(change: ProjectRename, rewriter: Rewriter, *, apply: bool) -> DatabaseChanges:
    """Rewrite (or, without ``apply``, count) every reference in one transaction."""
    changes = DatabaseChanges()
    database = portal_database(schema="lineage")
    with (database.transaction() if apply else database.snapshot()) as connection:
        if apply:
            connection.execute("SET LOCAL lock_timeout = '60s'")
        schemas = _portal_schemas(connection)
        columns: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for row in connection.execute(
            "SELECT c.table_schema, c.table_name, c.column_name, c.data_type FROM information_schema.columns c "
            "JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
            "WHERE t.table_type = 'BASE TABLE' AND c.table_schema = ANY(%s) AND c.data_type = ANY(%s) "
            "ORDER BY c.table_schema, c.table_name, c.ordinal_position",
            (sorted(schemas), list(TEXT_TYPES)),
        ).fetchall():
            columns.setdefault((str(row[0]), str(row[1])), []).append((str(row[2]), str(row[3])))
        needles = rewriter.needles()
        registry_rows: dict[str, dict[str, set[str]]] = {}
        for (schema, table), table_columns in columns.items():
            name = schemas[schema]
            if _history(name, table):
                continue
            table_columns = [(column, data_type) for column, data_type in table_columns
                             if (name, table, column) not in HISTORY_COLUMNS]
            if not table_columns:
                continue
            target = f"{_quote(schema)}.{_quote(table)}"
            selected = ", ".join(f"{_quote(column)}::text AS c{index}" for index, (column, _type) in enumerate(table_columns))
            where = " OR ".join(f"{_quote(column)}::text LIKE ANY(%s)" for column, _type in table_columns)
            if (name, table) in {("lineage", "residents"), ("workspace", "workspaces")}:
                # Deleted identities used to live in a protected history table.
                # Moving them into residents does not make their facts current.
                where = f"({where}) AND deleted_at IS NULL"
            query = f"SELECT ctid::text AS row_ctid, {selected} FROM {target} WHERE {where}"
            rows = connection.execute(
                query + (" FOR UPDATE" if apply else ""), tuple(needles for _column in table_columns),
            ).fetchall()
            changed_rows = 0
            for row in rows:
                values: dict[str, str] = {}
                updates: list[tuple[str, str, str]] = []
                for index, (column, data_type) in enumerate(table_columns):
                    before = row[f"c{index}"]
                    if before is None:
                        continue
                    after = rewriter.column(str(before))
                    values[column] = after
                    if after != before:
                        updates.append((column, data_type, after))
                if not updates:
                    continue
                changed_rows += 1
                if apply:
                    assignments = ", ".join(
                        f"{_quote(column)} = %s" + ("::jsonb" if data_type == "jsonb" else "::json" if data_type == "json" else "")
                        for column, data_type, _value in updates
                    )
                    if (name, table) == ("workspace", "workspaces"):
                        # Workspace search/index consumers observe this stamp.
                        from management_portal.resources.workspace_store import now
                        assignments += ', updated_at = %s'
                        updates.append(("updated_at", "text", now()))
                    connection.execute(
                        f"UPDATE {target} SET {assignments} WHERE ctid = %s::tid",
                        (*[value for _column, _type, value in updates], row["row_ctid"]),
                    )
                if name == "resource_registry" and table == "registry_documents":
                    registry_rows.setdefault(schema, {}).setdefault(values["collection"], set()).add(values["document_id"])
            if changed_rows:
                changes.tables[f"{name}.{table}"] = changed_rows
        for schema, collections in registry_rows.items():
            for collection, document_ids in collections.items():
                changes.registry.setdefault(collection, set()).update(document_ids)
                if apply:
                    _advance_registry(connection, schema, collection, document_ids)
        if change.renames_id:
            # Element paths in the permission schemes' trees are arrays the text rewrite above does not reach.
            moved = permission_system.rename_element(connection, change.old_id, change.new_id, apply=apply)
            if moved:
                changes.tables["permission.scheme_nodes.path"] = moved
    return changes


def _advance_registry(connection: Any, schema: str, collection: str, document_ids: set[str]) -> None:
    """Registry caches read documents written after the revision they hold."""
    revision = connection.execute(
        f"INSERT INTO {_quote(schema)}.registry_collections(collection, revision) "
        f"VALUES (%s, nextval('{schema}.registry_revision_seq')) "
        "ON CONFLICT(collection) DO UPDATE SET revision = excluded.revision RETURNING revision",
        (collection,),
    ).fetchone()[0]
    connection.execute(
        f"UPDATE {_quote(schema)}.registry_documents SET revision = %s WHERE collection = %s AND document_id = ANY(%s)",
        (revision, collection, sorted(document_ids)),
    )


# -- outside Portal -----------------------------------------------------------


def _mount_points() -> list[Path]:
    points: list[Path] = []
    try:
        lines = MOUNTINFO.read_text(encoding="utf-8").splitlines()
    except OSError:
        return points
    for line in lines:
        fields = line.split(" ")
        if len(fields) > 4:
            points.append(Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4])))
    return points


def external_blockers(change: ProjectRename, rewriter: Rewriter) -> list[dict[str, str]]:
    """Mounts and services that use the directory; Portal cannot move them along."""
    if not change.moves:
        return []
    blockers: list[dict[str, str]] = []
    for point in _mount_points():
        if point == change.old_root or change.old_root in point.parents:
            blockers.append({"kind": "mount", "path": str(point)})
    # Native mount detection supplements Linux mountinfo on macOS and Windows.
    # Never descend into another mounted filesystem during a Project move.
    seen_mounts = {item["path"] for item in blockers}
    deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
    for directory, folders, _files in os.walk(change.old_root, followlinks=False):
        if time.monotonic() >= deadline:
            raise TimeoutError("Project mount scan timed out")
        if os.path.ismount(directory):
            if directory not in seen_mounts:
                blockers.append({"kind": "mount", "path": directory})
            folders[:] = []
    for directory in SYSTEMD_UNIT_DIRS:
        for unit in sorted(directory.glob("*")) if directory.is_dir() else ():
            if not unit.is_file() or unit.suffix not in (".service", ".timer", ".path", ".socket", ".mount"):
                continue
            try:
                text = unit.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if rewriter.path(text) != text:
                blockers.append({"kind": "systemd_unit", "path": str(unit)})
    return blockers


def external_references(change: ProjectRename) -> list[str]:
    """Service scripts that name the old directory; reported, never changed."""
    if not change.moves:
        return []
    roots = [str(root) for root in SERVICE_SCRIPT_ROOTS if root.is_dir()]
    if not roots:
        return []
    return _grep_files([str(change.old_root)], roots, extra=("--exclude-dir=state", "--exclude=*.log"))


def _grep_files(patterns: Iterable[str], roots: list[str], *, extra: tuple[str, ...] = ()) -> list[str]:
    """Text files under ``roots`` that contain any of the fixed ``patterns``."""
    needles = tuple(pattern.encode("utf-8") for pattern in patterns)
    overlap = max((len(needle) for needle in needles), default=1) - 1
    excluded_dirs = [item.partition("=")[2] for item in extra if item.startswith("--exclude-dir=")]
    excluded_files = [item.partition("=")[2] for item in extra if item.startswith("--exclude=")]
    if any(not item.startswith(("--exclude-dir=", "--exclude=")) for item in extra):
        raise ValueError("Unsupported file scan filter")
    result: set[str] = set()
    deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
    for root in roots:
        for directory, folders, files in os.walk(root, followlinks=False):
            if time.monotonic() >= deadline:
                raise TimeoutError("Project reference scan timed out")
            folders[:] = [name for name in folders if not any(fnmatch.fnmatchcase(name, pattern) for pattern in excluded_dirs)]
            for name in files:
                if any(fnmatch.fnmatchcase(name, pattern) for pattern in excluded_files):
                    continue
                path = Path(directory) / name
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    matched, binary, tail = False, False, b""
                    with path.open("rb") as stream:
                        while chunk := stream.read(128 * 1024):
                            if time.monotonic() >= deadline:
                                raise TimeoutError("Project reference scan timed out")
                            if b"\0" in chunk:
                                binary = True
                                break
                            data = tail + chunk
                            matched = matched or any(needle in data for needle in needles)
                            tail = data[-overlap:] if overlap else b""
                except TimeoutError:
                    raise
                except OSError as exc:
                    LOGGER.warning("Cannot scan Project reference %s: %s", path, exc)
                    continue
                if matched and not binary:
                    result.add(str(path))
    return sorted(result)


# -- files ----------------------------------------------------------------------


@dataclass
class FileChanges:
    rewritten: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    provider_state: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        return {
            "rewritten_count": len(self.rewritten),
            "rewritten": self.rewritten[:200],
            "links": self.links,
            "provider_state": self.provider_state,
            **({"errors": self.errors} if self.errors else {}),
        }

    def fail(self, path: object, exc: BaseException) -> None:
        self.errors.append({"path": str(path), "error": str(exc) or type(exc).__name__})


def _file_patterns(change: ProjectRename) -> list[str]:
    patterns = [old for old, _new in change.path_pairs()]
    if change.renames_id:
        patterns.append("#project(")
    return patterns


def _rewrite_file(path: Path, rewrite: Callable[[str], str], result: FileChanges, into: list[str] | None = None) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if path.stat().st_size > MAX_REWRITE_BYTES:
            result.errors.append({"path": str(path), "error": "文件过大，未改写"})
            return False
        before = path.read_bytes()
        text = before.decode("utf-8")
        after = rewrite(text)
        if after == text:
            return False
        # Writing in place keeps the file's owner, mode and links.
        with path.open("r+b") as stream:
            stream.write(after.encode("utf-8"))
            stream.truncate()
        (result.rewritten if into is None else into).append(str(path))
        return True
    except UnicodeDecodeError:
        return False
    except OSError as exc:
        result.fail(path, exc)
        return False


def fix_files(change: ProjectRename, rewriter: Rewriter, residents_paths: list[tuple[str, str]]) -> FileChanges:
    """Make what refers to the moved directory follow it; errors are reported, not raised."""
    result = FileChanges()
    root = change.new_root
    patterns = _file_patterns(change)
    candidates: set[str] = set()
    if patterns:
        try:
            candidates.update(_grep_files(patterns, [str(root)]))
        except Exception as exc:  # noqa: BLE001 - reported
            result.fail(root, exc)
    if change.renames_id:
        candidates.update(str(root / name) for name in ("summary.md", "agent_port/boot.md"))
    for name in sorted(candidates):
        path = Path(name)
        relative = path.relative_to(root).as_posix() if root in path.parents else ""
        _rewrite_file(path, lambda text, relative=relative: rewriter.file_text(text, relative), result)
    if change.moves:
        _fix_git_links(root, rewriter, result)
        _fix_symlinks(change, rewriter, result)
        _fix_provider_state(change, rewriter, residents_paths, result)
    return result


def _fix_git_links(root: Path, rewriter: Rewriter, result: FileChanges) -> None:
    """Worktrees and their repositories name each other by absolute path."""
    try:
        completed = subprocess.run(
            ["find", str(root), "-name", ".git", "-print0", "-prune"],
            capture_output=True, timeout=SCAN_TIMEOUT_SECONDS, check=False,
        )
    except Exception as exc:  # noqa: BLE001 - reported
        result.fail(root, exc)
        return
    backlinks: set[Path] = set()
    for item in completed.stdout.split(b"\0"):
        if not item:
            continue
        git = Path(item.decode("utf-8", "surrogateescape"))
        if git.is_file():
            # A worktree: its repository's gitdir file points back here.
            try:
                pointer = git.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if pointer.startswith("gitdir:"):
                gitdir = Path(pointer[len("gitdir:"):].strip())
                gitdir = gitdir if gitdir.is_absolute() else (git.parent / gitdir)
                backlinks.add(gitdir / "gitdir")
        elif git.is_dir():
            # A repository: each of its worktrees has a .git file pointing here.
            for link in (git / "worktrees").glob("*/gitdir"):
                try:
                    worktree_git = Path(link.read_text(encoding="utf-8").strip())
                except (OSError, UnicodeDecodeError):
                    continue
                backlinks.add(worktree_git)
    for path in sorted(backlinks):
        if root == path or root in path.parents:
            continue  # rewritten with the directory's own files
        _rewrite_file(path, rewriter.path, result, result.links)


def _fix_symlinks(change: ProjectRename, rewriter: Rewriter, result: FileChanges) -> None:
    deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
    def links():
        for root in SYMLINK_ROOTS:
            for directory, folders, files in os.walk(root, followlinks=False, onerror=lambda exc: result.fail(root, exc)):
                if time.monotonic() >= deadline:
                    result.fail(root, TimeoutError("Project symlink scan timed out"))
                    return
                for name in (*folders, *files):
                    link = Path(directory) / name
                    if link.is_symlink():
                        yield link
    for link in links():
        temporary = None
        try:
            target = os.readlink(link)
            replacement = rewriter.path(target)
            if replacement == target:
                continue
            temporary = link.with_name(f".{link.name}.rename-{os.getpid()}")
            directory_link = link.is_dir() or bool(getattr(link.lstat(), "st_file_attributes", 0) & 0x10)
            os.symlink(replacement, temporary, target_is_directory=directory_link)
            os.replace(temporary, link)
            result.links.append(str(link))
        except OSError as exc:
            result.fail(link, exc)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _claude_project_key(path: str) -> str:
    try:
        from claude_agent_sdk import project_key_for_directory

        return project_key_for_directory(path)
    except ImportError:  # no Claude SDK in this process: the CLI's own rule for short paths
        return re.sub(r"[^a-zA-Z0-9]", "-", path)
    except Exception:  # noqa: BLE001 - the same rule when the SDK cannot tell
        LOGGER.warning("Claude SDK project key unavailable; the short-path rule is used", exc_info=True)
        return re.sub(r"[^a-zA-Z0-9]", "-", path)


def _fix_provider_state(
    change: ProjectRename, rewriter: Rewriter, residents_paths: list[tuple[str, str]], result: FileChanges,
) -> None:
    # Codex trusts working directories by path.
    # The same account can also be reached through a link, so each real file is visited once.
    for config in sorted({path.resolve() for path in (
            *HOME.glob(".codex/config.toml"), *HOME.glob(".codex-accounts/*/config.toml"),
            *ACCOUNTS_ROOT.glob("codex/*/config.toml"))}):
        _rewrite_file(config, rewriter.path, result, result.provider_state)
    # Claude Code keeps each working directory's sessions and memory in a folder named after it.
    directories = [*residents_paths, (str(change.old_root), str(change.new_root))]
    for projects in sorted({path.resolve() for path in (
            *HOME.glob(".claude/projects"), *HOME.glob(".claude-accounts/*/projects"),
            *ACCOUNTS_ROOT.glob("claude/*/projects"))}):
        for old, new in directories:
            source = projects / _claude_project_key(old)
            target = projects / _claude_project_key(new)
            if source == target or not source.is_dir():
                continue
            if target.exists():
                result.errors.append({"path": str(source), "error": f"{target} 已存在，未合并"})
                continue
            try:
                source.rename(target)
                result.provider_state.append(str(target))
            except OSError as exc:
                result.fail(source, exc)
    # Kimi sessions keep the directory they started in.
    if KIMI_HOME.is_dir():
        for path in sorted({
            KIMI_HOME / "workspaces.json", KIMI_HOME / "session_index.jsonl", *KIMI_HOME.glob("sessions/*/*/state.json"),
        }):
            if path.is_file():
                _rewrite_file(path, rewriter.path, result, result.provider_state)


def _resident_paths(change: ProjectRename, resident_ids: list[str]) -> list[tuple[str, str]]:
    """Old and new working directories of the Residents under the moved directory."""
    if not resident_ids or not change.moves:
        return []
    root_spellings = ((str(change.old_root), str(change.new_root)),
                      (change.old_root.as_posix(), change.new_root.as_posix()))
    separators = ("/", "\\") if change.new_root.drive else ("/",)
    pairs: set[tuple[str, str]] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str):
            for old_root, new_root in root_spellings:
                if value == new_root or value.startswith(tuple(new_root + separator for separator in separators)):
                    pairs.add((old_root + value[len(new_root):], value))
                    break

    with portal_database(schema="lineage").snapshot() as connection:
        from management_portal.society_service.resource_client import workspace_paths_many
        rows = _current_resident_workspaces(connection, resident_ids)
        for path in workspace_paths_many([row[1] for row in rows], connection=connection).values():
            collect(path)
    return sorted(pairs)


# -- the rename -------------------------------------------------------------------


@contextmanager
def _rename_lock() -> Iterator[None]:
    """One rename at a time across Portal instances."""
    database = portal_database(schema="lineage")
    digest = hashlib.sha256(f"{database.schema}:project-rename".encode()).digest()
    with database.advisory_lock(int.from_bytes(digest[:8], "big", signed=True)):
        yield


def _announce(changes: DatabaseChanges) -> None:
    from management_portal.resources.registry_store import announce_registry_changes

    project_registry.reset_cache_for_tests()
    for collection, document_ids in sorted(changes.registry.items()):
        announce_registry_changes(collection, {document_id: "updated" for document_id in document_ids})


def rename_project(
    project_id: str,
    new_project_id: str,
    actor: str,
    *,
    dry_run: bool = False,
    entry: str = "system",
    residents: Residents | None = None,
) -> dict[str, Any]:
    # Papers and the other objects of a feature's shared library live outside
    # the project folder; their relations to the project follow the new ID.
    from management_portal import portal_features

    def relate(write: bool) -> dict[str, list[str]]:
        return {feature: relate_project(change.old_id, change.new_id, write=write)
                for feature, relate_project in portal_features.contributions("project.relations")}

    control = residents or Residents(actor, entry)
    with _rename_lock():
        change = plan(project_id, new_project_id)
        rewriter = Rewriter(change)
        resident_ids = affected_residents(change, rewriter)
        blockers = external_blockers(change, rewriter)
        report: dict[str, Any] = {
            "project_id": change.new_id,
            "previous_project_id": change.old_id,
            "path": str(change.new_root),
            "previous_path": str(change.old_root),
            "dry_run": bool(dry_run),
            "resident_ids": resident_ids,
            "external_references": external_references(change),
        }
        if dry_run:
            database = rewrite_database(change, rewriter, apply=False)
            patterns = _file_patterns(change)
            files = _grep_files(patterns, [str(change.old_root)]) if patterns else []
            return {
                **report,
                "database": database.report(),
                "files": {"rewritten_count": len(files), "rewritten": files[:200]},
                "relations": relate(write=False),
                "busy_resident_ids": control.busy(resident_ids),
                "blockers": blockers,
            }
        if blockers:
            raise RenameBlocked("Project 目录被 Portal 之外的挂载或服务使用，先迁移它们再改名", blockers=blockers)
        closed = control.close_intake(resident_ids)
        try:
            busy = control.busy(resident_ids)
            if busy:
                raise RenameBlocked("这些 Resident 正在执行，等它们完成后再改名", busy_resident_ids=busy)
            if change.moves:
                os.rename(change.old_root, change.new_root)
            try:
                database = rewrite_database(change, rewriter, apply=True)
            except BaseException:
                if change.moves:
                    os.rename(change.new_root, change.old_root)
                raise
            files = fix_files(change, rewriter, _resident_paths(change, resident_ids))
            relations = relate(write=True)
            from management_portal.avatar_store import scope_avatar_store, scope_subject

            scope_avatar_store().move(scope_subject("project", change.old_id), scope_subject("project", change.new_id))
        finally:
            control.open_intake(closed)
        _announce(database)
        return {**report, "database": database.report(), "files": files.report(), "relations": relations}
