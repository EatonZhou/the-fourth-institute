"""PostgreSQL schema archives with dependency closure and private credentials."""

def _postgres_environment(dsn):
    import os
    from psycopg.conninfo import conninfo_to_dict
    from tests.cases.postgres_test_database import _postgres_environment as environment
    result = environment()
    for key in list(result):
        if key.startswith("PG") and key not in {"PGTZ", "PGAPPNAME"}:
            result.pop(key)
    for name, value in conninfo_to_dict(dsn).items():
        result["PGDATABASE" if name == "dbname" else "PG" + name.upper().replace("_", "")] = value
    # The packaged PostgreSQL server has no LLVM runtime. Bulk restore and
    # migration must also work when its query planner crosses the JIT threshold.
    result["PGOPTIONS"] = (result.get("PGOPTIONS", "") + " -c jit=off").strip()
    return result


def _archive(dsn, archive, schemas):
    import subprocess
    import psycopg
    from tests.cases.postgres_test_database import _binary
    # A schema archive must include referenced owners to be restorable into
    # an empty database (Resident-owned Workspaces have cross-schema FKs).
    schemas = set(schemas)
    with psycopg.connect(dsn) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        while True:
            referenced = {row[0] for row in connection.execute(
                "SELECT DISTINCT target.relnamespace::regnamespace::text "
                "FROM pg_constraint constraint_row "
                "JOIN pg_class source ON source.oid=constraint_row.conrelid "
                "JOIN pg_class target ON target.oid=constraint_row.confrelid "
                "WHERE constraint_row.contype='f' AND source.relnamespace::regnamespace::text=ANY(%s)",
                (sorted(schemas),)).fetchall()}
            if referenced <= schemas:
                break
            schemas.update(referenced)
    archive.touch(mode=0o600, exist_ok=False)
    command = [str(_binary("pg_dump")), "--format=custom", "--compress=1", "--no-owner", "--no-acl", "--file", str(archive)]
    for schema in sorted(schemas):
        command.extend(["--schema", schema])
    environment = _postgres_environment(dsn)
    subprocess.run(command, check=True, env=environment, capture_output=True)
    archive.chmod(0o600)
    subprocess.run([str(_binary("pg_restore")), "--list", str(archive)],
                   check=True, env=environment, capture_output=True)

