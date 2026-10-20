"""One-time Workbench workflow receipt cutover after Lineage v48.

Use an existing database transaction and archive workbenches before committing.
The runtime stores only Trigger Path IDs; it never interprets legacy Run IDs.
"""
from psycopg import sql
from psycopg.rows import dict_row


def migrate(connection, *, workbench_schema="workbenches", lineage_schema="lineage"):
    generations = sql.Identifier(workbench_schema, "generations")
    paths = sql.Identifier(lineage_schema, "trigger_paths")
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET LOCAL lock_timeout='10s'")
        cursor.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(generations))
        columns = {r["column_name"] for r in cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='generations'",
            (workbench_schema,))}
        if not {"run_id", "invocation_id"} <= columns or "trigger_path_id" in columns:
            raise ValueError("Workbench migration requires the legacy workflow receipt columns")
        rows = cursor.execute(sql.SQL(
            "SELECT g.id,g.run_id,p.trigger_path_id FROM {} g LEFT JOIN {} p "
            "ON p.lineage_id=g.lineage_id AND p.occurrence_id=g.event_id").format(generations, paths)).fetchall()
        if len({r["id"] for r in rows}) != len(rows):
            raise ValueError("A generation event must identify one Trigger Path")
        if any(r["run_id"] and not r["trigger_path_id"] for r in rows):
            raise ValueError("A historical generation Run has no migrated event Trigger Path")
        cursor.execute(sql.SQL("ALTER TABLE {} RENAME COLUMN run_id TO trigger_path_id").format(generations))
        for row in rows:
            cursor.execute(sql.SQL("UPDATE {} SET trigger_path_id=%s WHERE id=%s").format(generations),
                           (row["trigger_path_id"], row["id"]))
        cursor.execute(sql.SQL("ALTER TABLE {} DROP COLUMN invocation_id").format(generations))
        return {"generations": len(rows), "trigger_path_links": sum(bool(r["trigger_path_id"]) for r in rows)}
