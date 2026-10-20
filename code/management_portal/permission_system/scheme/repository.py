"""The rules of every permission scheme, and which scheme is active (decision_b820faf42218 §3.5).

Built-in rules ship as JSON files next to this module and are never changed
in place; a built-in scheme is used as it is (its instance takes edits like
any other), and rules built on it start as a copy.  Custom rules are stored
in ``permission.schemes`` with a version for compare-and-set updates.
``permission.active_scheme`` names the scheme the permission system uses
(the default scheme until another one is activated): with the permission
system on, decisions read its instance; with it off, its instance is
brought up to date when the permission system is turned on.  The instance
saved with each scheme lives in the Store (``engine/store.py``).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from ..engine.store import DEFAULT_SCHEME, Store
from management_portal.hatg.declarations import decision_elements

from .compiler import CompiledScheme, SchemeError, compile_scheme, declared_decisions

BUILTIN_DIRECTORY = Path(__file__).with_name("builtin")
LOGGER = logging.getLogger(__name__)


class SchemeNotFound(KeyError):
    pass


class SchemeConflict(ValueError):
    def __init__(self, message: str, current_version: int | None = None, *, object_id: str = "",
                 expected_version: int | None = None) -> None:
        super().__init__(message)
        self.current_version = current_version
        self.object_id = object_id
        self.expected_version = expected_version


def source_digest(document: Any) -> str:
    """The digest a copy compares its source on: the rules without their scheme ID and title (§5.11)."""
    material = {key: value for key, value in dict(document).items() if key not in ("scheme_id", "title")}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def builtin_documents() -> dict[str, dict[str, Any]]:
    documents = {}
    for path in sorted(BUILTIN_DIRECTORY.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        # A shipped template must classify every installed decision slot. A
        # missing mapping is a template error, never a runtime permission level.
        # Filter omitted features just as when loading saved rules; custom
        # schemes retain their own default-node policy.
        compiled = compile_scheme(installed(document), instructions=declared_decisions())
        if missing := compiled.unmapped():
            raise SchemeError([
                {"path": f"instructions.{element}", "message": "内置权限模板必须显式分类此判定槽"}
                for element in missing
            ])
        documents[document["scheme_id"]] = document
    return documents


_REPORTED: set[tuple[str, tuple[str, ...]]] = set()


def installed(document: dict[str, Any], *, before: bool = False) -> dict[str, Any]:
    """The rules with their classification limited to the A elements of the instructions this Portal installs.

    A feature left out of the package takes its instructions with it, while the
    built-in and saved rules still classify their elements; the rules apply to
    the rest.  An R node given to a slot that is an argument slot now (a
    release turned it from fixed) is dropped too, unless ``before``.  New
    rules are still checked against the installed declarations
    (``Schemes.compile``).
    """
    mapped = document.get("instructions")
    if not isinstance(mapped, dict):
        return document
    declared = declared_decisions()
    slots = {element: slot for name, decides in declared.items() for element, slot in decision_elements(name, decides)}
    elements = set(slots)
    unfixed = () if before else tuple(sorted(
        element for element, item in mapped.items()
        if element in slots and slots[element].argument and isinstance(item, dict) and "r" in item))
    if unfixed:
        if (key := (str(document.get("scheme_id")), ("r",) + unfixed)) not in _REPORTED:
            _REPORTED.add(key)
            LOGGER.warning("permission scheme %s gives an R node to argument slots; dropped: %s", key[0], ", ".join(unfixed))
        mapped = {element: ({k: v for k, v in item.items() if k != "r"} if element in unfixed else item)
                  for element, item in mapped.items()}
        document = {**document, "instructions": mapped}
    missing = tuple(sorted(set(mapped) - elements))
    if not missing:
        return document
    if (key := (str(document.get("scheme_id")), missing)) not in _REPORTED:
        _REPORTED.add(key)
        # An instruction that gained or lost slots renamed its elements: say what they are now.
        renamed = sorted({name for name in (element.split(":", 1)[0] for element in missing) if name in declared})
        now = "".join(f"; {name} now has {', '.join(element for element, _slot in decision_elements(name, declared[name]))}"
                      for name in renamed)
        LOGGER.warning("permission scheme %s classifies A elements this Portal does not declare; skipped: %s%s",
                       key[0], ", ".join(missing), now)
    return {**document, "instructions": {element: item for element, item in mapped.items() if element in elements}}


class Schemes:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._builtin = builtin_documents()
        self._compiled: dict[tuple[str, str], CompiledScheme] = {}
        self._lock = threading.Lock()

    def builtin(self, scheme_id: str) -> bool:
        return scheme_id in self._builtin

    def builtin_ids(self) -> list[str]:
        return list(self._builtin)

    def compile(self, document: Any, *, saved: bool = False, before: bool = False) -> CompiledScheme:
        """Compile rules against the instructions this release has.

        ``saved`` rules (built-in or stored) may still classify an element this
        Portal lacks: a later release removed the instruction or its slot, or
        the package left its feature out.  Nobody can call it, so loading skips
        that classification (``installed``) instead of failing the whole
        scheme; saving the rules again still reports it.  They may also give
        an R node to a slot a release turned from fixed into an argument slot:
        loading drops that node, while ``before`` (the basis a release is
        compared with) keeps the slot fixed on it, as it was.
        """
        if saved and isinstance(document, dict):
            document = installed(document, before=before)
        return compile_scheme(document, instructions=declared_decisions(), before=before)

    def _compiled_for(self, document: dict[str, Any], version: Any) -> CompiledScheme:
        key = (document["scheme_id"], str(version))
        with self._lock:
            compiled = self._compiled.get(key)
        if compiled is None:
            compiled = self.compile(document, saved=True)
            with self._lock:
                self._compiled[key] = compiled
        return compiled

    def get(self, scheme_id: str, connection=None) -> dict[str, Any]:
        """``{rules, builtin, version}`` of one scheme; a custom scheme adds created_by, updated_by, updated_at.

        The rules map only installed instructions (``installed``): a copy or an
        edit saves them without the instructions of features left out.
        """
        if scheme_id in self._builtin:
            return {"rules": copy.deepcopy(installed(self._builtin[scheme_id])), "builtin": True, "version": 0}
        with self.store.read(connection) as connection:
            row = self.store.execute(
                connection, "SELECT document, version, created_by, updated_by, extract(epoch FROM updated_at) AS updated_at"
                " FROM {s}.schemes WHERE scheme_id = %s", (scheme_id,)).fetchone()
        if row is None:
            raise SchemeNotFound(scheme_id)
        return {"rules": installed(dict(row["document"])), "builtin": False, "version": int(row["version"]),
                "created_by": row["created_by"], "updated_by": row["updated_by"], "updated_at": float(row["updated_at"])}

    def compiled(self, scheme_id: str, connection=None) -> CompiledScheme:
        if scheme_id in self._builtin:
            return self._compiled_for(self._builtin[scheme_id], "builtin")
        found = self.get(scheme_id, connection)
        return self._compiled_for(found["rules"], found["version"])

    def list(self) -> list[dict[str, Any]]:
        schemes = [{"scheme_id": scheme_id, "title": document.get("title", scheme_id),
                    "description": document.get("description", ""), "builtin": True, "version": 0}
                   for scheme_id, document in self._builtin.items()]
        with self.store.read() as connection:
            rows = self.store.execute(
                connection, "SELECT scheme_id, document->>'title' AS title, document->>'description' AS description, version,"
                " updated_by, extract(epoch FROM updated_at) AS updated_at FROM {s}.schemes ORDER BY scheme_id").fetchall()
            counts = {row["scheme_id"]: int(row["count"]) for row in self.store.execute(
                connection, "SELECT scheme_id, count(*) FROM {s}.scheme_entries WHERE expires_at IS NULL GROUP BY scheme_id").fetchall()}
        schemes += [{"scheme_id": row["scheme_id"], "title": row["title"] or row["scheme_id"],
                     "description": row["description"] or "", "builtin": False, "version": int(row["version"]),
                     "updated_by": row["updated_by"], "updated_at": float(row["updated_at"])} for row in rows]
        return [{**item, "entry_count": counts.get(item["scheme_id"], 0)} for item in schemes]

    def active(self, connection=None) -> str:
        """The scheme the permission system uses; the default scheme until another one is activated."""
        with self.store.read(connection) as connection:
            row = self.store.execute(connection, "SELECT scheme_id FROM {s}.active_scheme").fetchone()
        return row["scheme_id"] if row is not None else DEFAULT_SCHEME

    def version(self, scheme_id: str, connection=None) -> int:
        """The rules version of a scheme (built-in schemes have version 0)."""
        if scheme_id in self._builtin:
            return 0
        try:
            return self.get(scheme_id, connection)["version"]
        except SchemeNotFound:
            return 0

    # -- where a copy came from (§5.11) ----------------------------------------------------

    def _current_digest(self, source_id: str, connection=None) -> str | None:
        try:
            return source_digest(self.get(source_id, connection)["rules"])
        except SchemeNotFound:
            return None

    def source(self, scheme_id: str, connection=None) -> dict[str, Any] | None:
        """Where a custom scheme was copied from: the source's ID and its rules when copied or last merged.

        ``current_digest`` is the source's digest now (None once it is
        deleted); ``outdated`` says the source changed since.
        """
        if scheme_id in self._builtin:
            return None
        with self.store.read(connection) as connection:
            row = self.store.execute(
                connection, "SELECT source_scheme_id, rules, digest, extract(epoch FROM recorded_at) AS recorded_at"
                " FROM {s}.scheme_sources WHERE scheme_id = %s", (scheme_id,)).fetchone()
            if row is None:
                return None
            current = self._current_digest(row["source_scheme_id"], connection)
            merged = self.last_merge(scheme_id, connection)
        source = {"scheme_id": row["source_scheme_id"], "rules": dict(row["rules"]), "digest": row["digest"],
                  "recorded_at": float(row["recorded_at"]), "current_digest": current,
                  "outdated": current is not None and current != row["digest"]}
        if merged is not None:
            source["last_merge"] = merged
        return source

    def last_merge(self, scheme_id: str, connection=None) -> dict[str, Any] | None:
        """The last merge recorded for ``scheme_id``: of its source's changes into a copy, or of a release's rules
        into a built-in scheme's own instance (``source_scheme_id`` is then the scheme itself)."""
        with self.store.read(connection) as connection:
            merged = self.store.execute(
                connection, "SELECT source_scheme_id, from_digest, to_digest, conflicts, unrestored, deleted_again, merged_by,"
                " auth_diff, regenerated, extract(epoch FROM merged_at) AS merged_at FROM {s}.scheme_merges WHERE scheme_id = %s",
                (scheme_id,)).fetchone()
        if merged is None:
            return None
        return {"source_scheme_id": merged["source_scheme_id"], "from_digest": merged["from_digest"],
                "to_digest": merged["to_digest"], "conflicts": list(merged["conflicts"]),
                "unrestored": list(merged["unrestored"]), "deleted_again": list(merged["deleted_again"]),
                "merged_by": merged["merged_by"], "merged_at": float(merged["merged_at"]),
                # Merges recorded before the report existed have none.
                "auth_diff": merged["auth_diff"],
                # False when only instruction classifications changed: the instance stayed as it was.
                "regenerated": merged["regenerated"]}

    def locked_source(self, scheme_id: str, connection) -> dict[str, Any] | None:
        """The source record of ``scheme_id`` locked until the caller's transaction ends: one merge at a time."""
        row = self.store.execute(connection, "SELECT source_scheme_id, rules, digest FROM {s}.scheme_sources"
                                             " WHERE scheme_id = %s FOR UPDATE", (scheme_id,)).fetchone()
        return None if row is None else {"scheme_id": row["source_scheme_id"], "rules": dict(row["rules"]), "digest": row["digest"]}

    def record_merge(self, scheme_id: str, source_id: str, *, from_digest: str, to_digest: str,
                     conflicts: list[dict[str, Any]], unrestored: list[dict[str, Any]], deleted_again: list[dict[str, Any]],
                     merged_by: str, auth_diff: dict[str, Any], regenerated: bool, connection) -> None:
        """Keep what the last merge of ``scheme_id`` left to a person: its conflicts, edits not written back, rows left deleted,
        what it granted and took away (``auth_diff``) and whether the instance was generated again.  A built-in scheme
        records a release's rules as its own merge."""
        self.store.execute(
            connection, "INSERT INTO {s}.scheme_merges (scheme_id, source_scheme_id, from_digest, to_digest, conflicts, unrestored,"
            " deleted_again, merged_by, auth_diff, regenerated, merged_at)"
            " VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, now())"
            " ON CONFLICT (scheme_id) DO UPDATE SET"
            " source_scheme_id = EXCLUDED.source_scheme_id, from_digest = EXCLUDED.from_digest, to_digest = EXCLUDED.to_digest,"
            " conflicts = EXCLUDED.conflicts, unrestored = EXCLUDED.unrestored, deleted_again = EXCLUDED.deleted_again,"
            " merged_by = EXCLUDED.merged_by, auth_diff = EXCLUDED.auth_diff, regenerated = EXCLUDED.regenerated,"
            " merged_at = EXCLUDED.merged_at",
            (scheme_id, source_id, from_digest, to_digest, json.dumps(conflicts, ensure_ascii=False),
             json.dumps(unrestored, ensure_ascii=False), json.dumps(deleted_again, ensure_ascii=False), merged_by,
             json.dumps(auth_diff, ensure_ascii=False), regenerated))

    def sources(self) -> dict[str, dict[str, Any]]:
        """``{scheme_id: {scheme_id of its source, outdated}}`` for every copy that records its source."""
        with self.store.read() as connection:
            rows = self.store.execute(connection, "SELECT scheme_id, source_scheme_id, digest FROM {s}.scheme_sources").fetchall()
            current = {source: self._current_digest(source, connection) for source in {row["source_scheme_id"] for row in rows}}
        return {row["scheme_id"]: {"scheme_id": row["source_scheme_id"],
                                   "outdated": current[row["source_scheme_id"]] not in (None, row["digest"])} for row in rows}

    def record_source(self, scheme_id: str, source_id: str, *, connection=None) -> str:
        """Record the rules ``source_id`` has now as the source of ``scheme_id``; returns their digest."""
        with self.store.transaction(connection) as connection:
            rules = self.get(source_id, connection)["rules"]
            digest = source_digest(rules)
            self.store.execute(
                connection, "INSERT INTO {s}.scheme_sources (scheme_id, source_scheme_id, rules, digest, recorded_at)"
                " VALUES (%s, %s, %s::jsonb, %s, now()) ON CONFLICT (scheme_id) DO UPDATE SET"
                " source_scheme_id = EXCLUDED.source_scheme_id, rules = EXCLUDED.rules, digest = EXCLUDED.digest,"
                " recorded_at = EXCLUDED.recorded_at", (scheme_id, source_id, json.dumps(rules, ensure_ascii=False), digest))
        return digest

    def activate(self, scheme_id: str, *, actor: str, connection=None) -> None:
        with self.store.transaction(connection) as connection:
            self.store.execute(
                connection, "INSERT INTO {s}.active_scheme (singleton, scheme_id, selected_by, selected_at) VALUES (TRUE, %s, %s, now())"
                " ON CONFLICT (singleton) DO UPDATE SET scheme_id = EXCLUDED.scheme_id,"
                " selected_by = EXCLUDED.selected_by, selected_at = EXCLUDED.selected_at", (scheme_id, actor))
            self.store.bump(connection)

    def create(self, document: dict[str, Any], *, actor: str, connection=None) -> CompiledScheme:
        compiled = self.compile(document)
        scheme_id = document["scheme_id"]
        if scheme_id in self._builtin:
            raise SchemeConflict(f"{scheme_id} 是内置方案的 ID")
        with self.store.transaction(connection) as connection:
            row = self.store.execute(
                connection, "INSERT INTO {s}.schemes (scheme_id, document, version, created_by, updated_by)"
                " VALUES (%s, %s::jsonb, 1, %s, %s) ON CONFLICT DO NOTHING RETURNING version",
                (scheme_id, json.dumps(document, ensure_ascii=False), actor, actor)).fetchone()
        if row is None:
            raise SchemeConflict(f"方案 {scheme_id} 已存在")
        return compiled

    def update(self, scheme_id: str, document: dict[str, Any], *, expected_version: int | None, actor: str,
               connection=None) -> int:
        """Replace a custom scheme's rules, compared with ``expected_version`` when given; returns the new version."""
        if scheme_id in self._builtin:
            raise SchemeConflict("内置方案不可修改；请复制为新方案")
        if not isinstance(document, dict) or document.get("scheme_id") != scheme_id:
            raise SchemeError([{"path": "scheme_id", "message": "方案 ID 不能修改"}])
        self.compile(document)
        with self.store.transaction(connection) as connection:
            row = self.store.execute(
                connection, "UPDATE {s}.schemes SET document = %s::jsonb, version = version + 1, updated_by = %s, updated_at = now()"
                " WHERE scheme_id = %s AND (%s::int IS NULL OR version = %s) RETURNING version",
                (json.dumps(document, ensure_ascii=False), actor, scheme_id, expected_version, expected_version)).fetchone()
            if row is None:
                self._conflict(connection, scheme_id, expected_version)
            # The instruction map may have changed with no row: processes load the rules again.
            self.store.bump(connection)
        return int(row["version"])

    def delete(self, scheme_id: str, *, expected_version: int | None, connection=None) -> None:
        if scheme_id in self._builtin:
            raise SchemeConflict("内置方案不可删除")
        with self.store.transaction(connection) as connection:
            if self.active(connection) == scheme_id:
                raise SchemeConflict("当前启用的方案不能删除；先启用其他方案")
            row = self.store.execute(
                connection, "DELETE FROM {s}.schemes WHERE scheme_id = %s AND (%s::int IS NULL OR version = %s) RETURNING version",
                (scheme_id, expected_version, expected_version)).fetchone()
            if row is None:
                self._conflict(connection, scheme_id, expected_version)
            self.store.execute(connection, "DELETE FROM {s}.scheme_sources WHERE scheme_id = %s", (scheme_id,))
            self.store.execute(connection, "DELETE FROM {s}.scheme_merges WHERE scheme_id = %s", (scheme_id,))
            self.store.scoped(scheme_id).drop(connection)

    def _conflict(self, connection, scheme_id: str, expected_version: int | None) -> None:
        current = self.store.execute(connection, "SELECT version FROM {s}.schemes WHERE scheme_id = %s", (scheme_id,)).fetchone()
        if current is None:
            raise SchemeNotFound(scheme_id)
        raise SchemeConflict("方案已被修改", int(current["version"]), object_id=scheme_id, expected_version=expected_version)
