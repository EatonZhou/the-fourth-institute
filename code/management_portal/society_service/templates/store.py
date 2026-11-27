"""The templates domain and its one-time builtin installation ledger."""

from __future__ import annotations

import secrets
from copy import deepcopy

from ..effect_script_candidate import reconcile, template_items
from .persistence import (  # noqa: F401  (the rows of the templates domain are read and written through this module)
    CONTENT, DDL, FIELDS, content_hash, database, get_persisted, read_rows, resolve_id, store_items,
    write_rows as _write, write_store_items,
)
from .registry import now


def ensure_builtins():
    from .kinds import normalize
    from .builtins import definitions

    with database().transaction("templates") as connection:
        rows = {row["template_id"]: row for row in read_rows(connection, "templates")}
        installed = {
            row["builtin_key"] for row in read_rows(connection, "installed_builtins")
        }
        for raw in definitions():
            template = normalize(raw)
            key = raw["builtin_key"]
            template_id = raw["template_id"]
            source = content_hash(template)
            previous = rows.get(template_id)
            if key not in installed:
                connection.execute(
                    "INSERT INTO installed_builtins(builtin_key,installed_at) VALUES (%s,%s)",
                    (key, now()),
                )
            elif previous is None:
                continue
            if previous is not None and (
                previous["builtin_source"] != content_hash(previous)
                or previous["builtin_source"] == source
            ):
                continue
            rows[template_id] = {
                **template,
                "template_id": template_id,
                "builtin_key": key,
                "builtin_source": source,
                "version": int(previous["version"]) + 1 if previous else 1,
                "created_at": previous["created_at"] if previous else now(),
                "created_by": previous["created_by"] if previous else "portal",
                "updated_at": now(),
                "updated_by": "portal",
            }
        from .consolidation import family_definition
        family_definition(connection, rows, installed)
        _write(connection, rows.values())


def migrate_instruction_names():
    from ..isa_definition_normalization import normalize_template
    from .structure import migrate_fork_modes

    with database().transaction("templates") as connection:
        rows = {row["template_id"]: row for row in read_rows(connection, "templates")}
        changed = False
        for template_id, previous in rows.items():
            current = migrate_fork_modes(normalize_template(previous))
            if current == previous:
                continue
            previous_hash = content_hash(previous)
            current_hash = content_hash(current)
            for field in ("builtin_source", "installed_store_hash"):
                if previous[field] == previous_hash:
                    current[field] = current_hash
            current.update(version=previous["version"] + 1, updated_at=now(), updated_by="portal")
            rows[template_id] = current
            changed = True
        if changed:
            _write(connection, rows.values())
        # Published snapshots follow their recipes: their content identity changes with them.
        current = store_items(connection)
        migrated = [migrate_fork_modes(item) for item in current]
        if migrated != current:
            for previous, item in zip(current, migrated, strict=True):
                if item != previous:
                    item["content_hash"] = content_hash(item)
            write_store_items(connection, migrated)


def all_templates():
    ensure_builtins()
    with database().read() as connection:
        return sorted(
            read_rows(connection, "templates"),
            key=lambda row: (row["category"], row["title"], row["template_id"]),
        )


def get(template_id):
    ensure_builtins()
    return get_persisted(template_id)


def save(template, actor, *, template_id=None, transform=None, reviewed_effect_id=None):
    ensure_builtins()
    with database().transaction("templates") as connection:
        rows = {row["template_id"]: row for row in read_rows(connection, "templates")}
        template_id = resolve_id(connection, template_id) if template_id else None
        previous = rows.get(template_id)
        if template_id and previous is None:
            raise KeyError(template_id)
        if transform:
            template = transform(deepcopy(previous))
        reconcile(
            template_items(template), template_items(previous), actor,
            key='key', reviewed_effect_id=reviewed_effect_id,
        )
        identity = template_id or "template_" + secrets.token_hex(6)
        value = {
            **deepcopy(template),
            **{key: previous.get(key) if previous else None for key in
               ("installed_from_store_id", "installed_store_version", "installed_store_hash")},
            "template_id": identity,
            "builtin_key": previous.get("builtin_key") if previous else None,
            "builtin_source": previous.get("builtin_source") if previous else None,
            "version": int(previous["version"]) + 1 if previous else 1,
            "created_at": previous["created_at"] if previous else now(),
            "created_by": previous["created_by"] if previous else actor,
            "updated_at": now(),
            "updated_by": actor,
        }
        rows[identity] = value
        _write(connection, rows.values())
        return value


def delete(template_id):
    ensure_builtins()
    with database().transaction("templates") as connection:
        template_id = resolve_id(connection, template_id)
        if not connection.execute(
            "DELETE FROM templates WHERE template_id=%s RETURNING template_id",
            (template_id,),
        ).fetchone():
            raise KeyError(template_id)


def restore(template_id, actor):
    from .kinds import normalize
    from .builtins import definitions

    ensure_builtins()
    with database().read() as connection:
        template_id = resolve_id(connection, template_id)
    raw = next(
        (item for item in definitions() if item["template_id"] == template_id), None
    )
    if raw is None:
        from .registry import TemplateError

        raise TemplateError("只能恢复内置模板")
    template = normalize(raw)
    ensure_builtins()
    with database().transaction("templates") as connection:
        rows = {row["template_id"]: row for row in read_rows(connection, "templates")}
        previous = rows.get(template_id)
        value = {
            **template,
            "builtin_source": content_hash(template),
            "version": int(previous["version"]) + 1 if previous else 1,
            "created_at": previous["created_at"] if previous else now(),
            "created_by": previous["created_by"] if previous else actor,
            "updated_at": now(),
            "updated_by": actor,
        }
        rows[template_id] = value
        _write(connection, rows.values())
        return value
