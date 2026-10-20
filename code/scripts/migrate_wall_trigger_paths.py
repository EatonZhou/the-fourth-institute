"""One-time WALL receipt cutover; the caller backs up WALL before committing."""
from psycopg import sql
from psycopg.rows import dict_row


def migrate(connection, *, wall_schema="portal_wall", lineage_schema="lineage"):
    jobs = sql.Identifier(wall_schema, "wall_jobs")
    paths = sql.Identifier(lineage_schema, "trigger_paths")
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET LOCAL lock_timeout='10s'")
        cursor.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(jobs))
        columns = {row["column_name"] for row in cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='wall_jobs'",
            (wall_schema,))}
        if "invocation_id" not in columns or "trigger_path_id" in columns:
            raise ValueError("WALL migration requires the legacy workflow receipt column")
        rows = cursor.execute(sql.SQL(
            "SELECT j.id,j.event_id,p.trigger_path_id FROM {} j LEFT JOIN {} p "
            "ON p.lineage_id=j.lineage_id AND p.occurrence_id=j.event_id").format(jobs, paths)).fetchall()
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError("A WALL event must identify one Trigger Path")
        cursor.execute(sql.SQL("ALTER TABLE {} RENAME COLUMN invocation_id TO trigger_path_id").format(jobs))
        for row in rows:
            # An unavailable historical path stays absent. Preserve the original
            # event/input and business result; never turn an invocation ID into a path ID.
            cursor.execute(sql.SQL("UPDATE {} SET trigger_path_id=%s WHERE id=%s").format(jobs),
                           (row["trigger_path_id"], row["id"]))
        return {"jobs": len(rows), "trigger_path_links": sum(bool(row["trigger_path_id"]) for row in rows),
                "events_without_path": sum(bool(row["event_id"]) and not row["trigger_path_id"] for row in rows)}
