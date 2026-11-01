"""Project rename enlists ID references and declared directory owners atomically.

The common move journal prepares files, commits registrations and old-directory
cleanup, then reconciles Provider cwd and Git worktrees. Cross-filesystem copies
first terminate affected executions. User-authored text is not a path registry.
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
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from management_common.portal_database import _database_schema, portal_database
from management_portal import permission_system, project_registry

LOGGER = logging.getLogger(__name__)

PROJECTS_ROOT = portal_path("projects")
HOME = Path.home()
# Places outside Portal whose configuration may run code in a Project.
SYSTEMD_UNIT_DIRS = (HOME / ".config/systemd/user", Path("/etc/systemd/system"))
SERVICE_SCRIPT_ROOTS = (Path(__file__).resolve().parents[1] / "scripts",)
MOUNTINFO = Path("/proc/self/mountinfo")
SCAN_TIMEOUT_SECONDS = 1800

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
HISTORY_SCHEMAS = frozenset({"harness_effectiveness", "directory_lifecycle"})
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
    projects_root: Path = PROJECTS_ROOT

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
            old_relative = self.old_root.relative_to(self.projects_root).as_posix()
            new_relative = self.new_root.relative_to(self.projects_root).as_posix()
        except ValueError:
            return pairs
        # Current registrations use area references, including absent paths.
        # Rewrite them alongside physical paths; IDs and bindings stay stable.
        pairs.append(("projects:" + old_relative, "projects:" + new_relative))
        # Older records may still use this spelling during conversion.
        pairs.append(("/Projects/" + old_relative, "/Projects/" + new_relative))
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
            r"(?<![\w.~-])(?:" + "|".join(
                (r"(?<!:)" if old.startswith("projects:") else "") + re.escape(old)
                for old, _new in pairs) + r")(?![\w@+-]|\.\w)"
        ) if pairs else None
        self._segment = re.compile(
            r"(^|:)project:" + re.escape(change.old_id) + r"(?=$|:)"
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
    old_root = project_registry.runtime_path(record.get("path"))
    if old_root is None:
        raise RenameBlocked(f"Project 登记缺少路径：{old_id}")
    if root not in old_root.parents:
        raise RenameBlocked(f"Project 目录不在 {root} 中：{old_root}", path=str(old_root))
    new_root = (root / new_id).resolve(strict=False)
    change = ProjectRename(old_id, new_id, old_root, new_root, projects_root=root)
    if not change.renames_id and not change.moves:
        raise ValueError("新的 Project ID 与目录都和现在相同")
    if not old_root.is_dir():
        raise RenameBlocked(f"Project 目录不存在：{old_root}", path=str(old_root))
    if change.moves and (new_root.exists() or new_root.is_symlink()):
        raise RenameBlocked(f"目标目录已存在：{new_root}", path=str(new_root))
    if change.moves and new_root.is_relative_to(old_root):
        raise ValueError("Project 目录不能移到自己里面")
    return change


def _schema_prefix() -> str:
    return _database_schema("x")[:-1]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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
    # Project identity references are distinct from directory registrations.
    # Only the declared directory owners below may rewrite stored paths.
    if change.renames_id:
        rewriter = Rewriter(ProjectRename(change.old_id, change.new_id, change.old_root, change.old_root,
                                         projects_root=change.projects_root))
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
        if change.moves and change.renames_id and apply:
            from management_portal.directory_move_owner import relocate_nested
            from management_common.instance_locations import stored_location
            from management_portal.resources.registry_store import get_registry_store
            relocate_nested(connection, change.old_root, change.new_root)
            with get_registry_store().writer(project_registry.COLLECTION, connection=connection) as writer:
                document = writer.get(project_registry.COLLECTION, project_registry.DOCUMENT_ID)
                for record in document['projects']:
                    if record['id'] == change.new_id:
                        record['path'] = stored_location(change.new_root)
                        if record.get('directory'):
                            record['directory']['owner_id'] = change.new_id
                writer.put(project_registry.COLLECTION, project_registry.DOCUMENT_ID, document)
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


def fix_files(change: ProjectRename, rewriter: Rewriter) -> FileChanges:
    """Only declared runtime dependencies follow a move; user prose stays intact."""
    result = FileChanges()
    if change.moves:
        from management_portal.directory_move_owner import reconcile_dependencies
        reconcile_dependencies(change.old_root, change.new_root)
    return result

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
    execution_control,
) -> dict[str, Any]:
    # Papers and the other objects of a feature's shared library live outside
    # the project folder; their relations to the project follow the new ID.
    from management_portal import portal_features

    def relate(write: bool) -> dict[str, list[str]]:
        return {feature: relate_project(change.old_id, change.new_id, write=write)
                for feature, relate_project in portal_features.contributions("project.relations")}

    control = execution_control
    with _rename_lock():
        change = plan(project_id, new_project_id)
        rewriter = Rewriter(change)
        resident_ids = control.affected_residents(change)
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
            return {
                **report,
                "database": database.report(),
                "files": FileChanges().report(),
                "relations": relate(write=False),
                "busy_resident_ids": control.busy(resident_ids),
                "blockers": blockers,
            }
        if blockers:
            raise RenameBlocked("Project 目录被 Portal 之外的挂载或服务使用，先迁移它们再改名", blockers=blockers)
        with control.move_scope(resident_ids) as terminate:
            from management_common.directory_move_store import DirectoryMoveStore
            record = project_registry.project(project_id)
            directory = (record or {}).get('directory')
            if not directory:
                raise RuntimeError('Project directory origin requires the offline migration')
            if change.moves:
                _operation, database = DirectoryMoveStore().move(change.old_root, change.new_root,
                    owner_id=change.new_id, origin=directory['origin'], actor=actor,
                    terminate=terminate,
                    database=portal_database(schema='lineage'),
                    update=lambda connection, source, target: rewrite_database(change, rewriter, apply=True),
                    dependencies=lambda source, target: fix_files(change, rewriter))
            else:
                database = rewrite_database(change, rewriter, apply=True)
            files = FileChanges()
            relations = relate(write=True)
            from management_portal.avatar_store import scope_avatar_store, scope_subject

            scope_avatar_store().move(scope_subject("project", change.old_id), scope_subject("project", change.new_id))
        _announce(database)
        return {**report, "database": database.report(), "files": files.report(), "relations": relations}
