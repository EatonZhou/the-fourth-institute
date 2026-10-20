"""One-time migration of explicitly planned schema fields, never a reader alias.

Business text, prompts and identity fields are opaque. JSON token replacement
preserves all other source bytes; callers handle owner-specific digests.
"""
from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path


_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_SCHEMA_PATH = ('strict $.** ? (@.type() == "object").keyvalue() '
                '? (@.key == "schema" && @.value.type() == "string" '
                '&& @.value == $planned[*])')


def rewrite_environment(value, names: Mapping[str, str]):
    """Rename only dictionary keys; reject a changed executor or other conflict."""
    if isinstance(value, list):
        return [rewrite_environment(item, names) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    result = {}
    for key, item in value.items():
        canonical = names.get(key, key)
        migrated = rewrite_environment(item, names)
        if canonical in result and result[canonical] != migrated:
            raise ValueError("Conflicting canonical environment fields")
        result[canonical] = migrated
    return result


def rewrite_schemas(value, names: Mapping[str, str]):
    if isinstance(value, list):
        return [rewrite_schemas(item, names) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: names.get(item, item) if key == "schema" and isinstance(item, str)
            else rewrite_schemas(item, names) for key, item in value.items()}


def rewrite_json(raw: str, schemas: Mapping[str, str]) -> str:
    def reject_constant(value):
        raise ValueError("Invalid JSON constant: " + value)

    before = json.loads(raw, parse_constant=reject_constant)
    expected = rewrite_schemas(before, schemas)
    # Match complete string tokens, including their escape sequences. In valid
    # JSON only an object key can be followed by a colon and another string.
    # Decoding the key/value handles Unicode escapes without rewriting keys,
    # whitespace, business strings or any other original bytes.
    parts = []
    end = 0
    previous = None
    previous_value = None
    for token in _JSON_STRING.finditer(raw):
        value = json.loads(token[0])
        if (previous is not None and previous_value == "schema"
                and raw[previous.end():token.start()].strip() == ":"
                and value in schemas):
            parts.extend((raw[end:token.start()], json.dumps(schemas[value], ensure_ascii=False)))
            end = token.end()
        previous, previous_value = token, value
    after = "".join((*parts, raw[end:]))
    if json.loads(after, parse_constant=reject_constant) != expected:
        raise ValueError("Schema token migration changed unrelated JSON content")
    return after


def _validate_schema_plan(schemas: Mapping[str, str]):
    """Accept only an explicit same-version namespace-only plan."""
    if not schemas:
        raise ValueError("An explicit non-empty schema plan is required")
    prefixes = set()
    for old, new in schemas.items():
        if not isinstance(old, str) or not isinstance(new, str) or "." not in old:
            raise ValueError("Invalid schema plan")
        prefix, tail = old.split(".", 1)
        if new != "tfi." + tail or prefix == "tfi":
            raise ValueError("Database migration only changes the namespace")
        prefixes.add(prefix)
    if len(prefixes) != 1:
        raise ValueError("One source namespace is required per database pass")


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_private_directory(path):
    """Persist each newly created directory's entry in its parent, in order."""
    path = Path(path).absolute()
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if not directory.is_dir():
                raise
        _fsync_directory(directory)
        _fsync_directory(directory.parent)
    if not path.is_dir():
        raise NotADirectoryError(path)
    return path


def rewrite_admission(raw, original_digest, schemas):
    from management_portal.execution.submission_identity import submission_digest
    if submission_digest(json.loads(raw)) != original_digest:
        raise ValueError("Original admission preimage is unavailable")
    after = rewrite_json(raw, schemas)
    return after, submission_digest(json.loads(after))


def migrate_column(connection, namespace, table, column, schemas, backup_root, *, batch_size=5000):
    """Migrate one owned column in bounded transactions with durable row backups.

    A primary-key cursor avoids repeatedly scanning already migrated history.
    Locked writers are skipped; the returned remaining count must be checked.
    Every batch is backed up before UPDATE and uses the locked row's xmin.
    Only this column changes. A caller owns collection revisions and digests.
    """
    import gzip
    import tempfile
    from psycopg import sql

    if not connection.autocommit:
        raise ValueError("A dedicated autocommit migration connection is required")
    if batch_size < 1:
        raise ValueError("Positive batch size required")
    _validate_schema_plan(schemas)
    variables = json.dumps({"planned": list(schemas)})
    row = connection.execute(
        "SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name=%s",
        (namespace, table, column)).fetchone()
    if not row or row[0] not in {"text", "json", "jsonb"}:
        raise ValueError("Only a reviewed text/JSON column may be migrated")
    data_type = row[0]
    keys = [r[0] for r in connection.execute(
        "SELECT a.attname FROM pg_index i "
        "CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum,n) "
        "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum "
        "WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY k.n",
        (namespace + "." + table,)).fetchall()]
    if not keys:
        raise ValueError("A stable primary key is required for backup and recovery")
    owner = ".".join((namespace, table, column))
    lock_name = "tfi-schema-name-migration:" + owner
    if not connection.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (lock_name,)).fetchone()[0]:
        raise RuntimeError("This migration owner is already active")
    target = sql.Identifier(namespace, table)
    key_sql = sql.SQL(",").join(map(sql.Identifier, keys))
    updated = batches = 0
    cursor = None
    try:
        root = durable_private_directory(backup_root)
        root.chmod(0o700)
        folder = Path(tempfile.mkdtemp(prefix=owner + "-", dir=root))
        _fsync_directory(root)
        while True:
            with connection.transaction():
                connection.execute("SET LOCAL lock_timeout='2s'")
                query = sql.SQL("SELECT {keys},xmin::text,{column}::text FROM {target} "
                                "WHERE jsonb_path_exists({column}::jsonb,%s::jsonpath,%s::jsonb)").format(
                    keys=key_sql, column=sql.Identifier(column), target=target)
                params = [_SCHEMA_PATH, variables]
                if cursor is not None:
                    query += sql.SQL(" AND ({keys}) > ({slots})").format(
                        keys=key_sql, slots=sql.SQL(",").join(sql.Placeholder() for _ in keys))
                    params.extend(cursor)
                query += sql.SQL(" ORDER BY {keys} LIMIT %s FOR UPDATE SKIP LOCKED").format(keys=key_sql)
                params.append(batch_size)
                rows = connection.execute(query, params).fetchall()
                if not rows:
                    break
                cursor = tuple(rows[-1][:len(keys)])
                # Validate every selected row before backup or UPDATE. Stage the
                # actual verified text; SQL must not interpret or rewrite JSON.
                after_values = [rewrite_json(row[-1], schemas) for row in rows]
                if any(after == row[-1] for row, after in zip(rows, after_values)):
                    raise ValueError("Structured candidate did not change")
                filename = folder / f"batch-{batches:06d}.jsonl.gz"
                descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as backup:
                        for row in rows:
                            backup.write((json.dumps({"keys":dict(zip(keys,row[:len(keys)])),
                                "revision":row[-2],"before":row[-1]}, ensure_ascii=False, default=str) + "\n").encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                _fsync_directory(folder)
                connection.execute("CREATE TEMP TABLE tfi_name_migration_batch (keys jsonb,revision text,after_value text) ON COMMIT DROP")
                with connection.cursor().copy("COPY tfi_name_migration_batch FROM STDIN") as writer:
                    for row, after in zip(rows, after_values):
                        writer.write_row((json.dumps(dict(zip(keys,row[:len(keys)])), default=str),row[-2],after))
                predicates = [sql.SQL("t.{}=(jsonb_populate_record(NULL::{},b.keys)).{}").format(
                    sql.Identifier(key),target,sql.Identifier(key)) for key in keys]
                predicates.append(sql.SQL("t.xmin::text=b.revision"))
                result = connection.execute(sql.SQL(
                    "UPDATE {target} AS t SET {column}=b.after_value::{type} "
                    "FROM tfi_name_migration_batch b WHERE {predicates}").format(
                        target=target,column=sql.Identifier(column),type=sql.SQL(data_type),
                        predicates=sql.SQL(" AND ").join(predicates)))
                if result.rowcount != len(rows):
                    raise ValueError("Concurrent row modification; batch rolled back")
            updated += len(rows)
            batches += 1
        remaining = connection.execute(sql.SQL("SELECT count(*) FROM {} "
            "WHERE jsonb_path_exists({}::jsonb,%s::jsonpath,%s::jsonb)").format(
            target,sql.Identifier(column)),(_SCHEMA_PATH,variables)).fetchone()[0]
        report = {"column":owner,"updated":updated,"batches":batches,"remaining":remaining,
                  "backup":str(folder),"primary_key":keys,"schemas":dict(schemas)}
        manifest = folder / "manifest.json"
        descriptor = os.open(manifest,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
        with os.fdopen(descriptor,"w") as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(folder)
        return report
    finally:
        connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))",(lock_name,))
