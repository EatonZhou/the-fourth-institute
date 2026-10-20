"""One-time removal of Chatroom ordering and installation of ownership FKs."""
from psycopg import sql


def migrate(connection, *, schema="chatrooms"):
    with connection.cursor() as c:
        c.execute(sql.SQL("SET LOCAL search_path TO {},public").format(sql.Identifier(schema)))
        c.execute("LOCK TABLE rooms,members,called_residents,viewers,deliveries IN ACCESS EXCLUSIVE MODE")
        for table in ("members", "called_residents", "viewers", "deliveries"):
            orphan = c.execute(sql.SQL(
                "SELECT count(*) FROM {} r LEFT JOIN rooms USING(chatroom_id) WHERE rooms.chatroom_id IS NULL"
            ).format(sql.Identifier(table))).fetchone()[0]
            if orphan:
                raise ValueError(f"Chatroom {table} contains orphan relationships")
            c.execute(sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} FOREIGN KEY(chatroom_id) "
                              "REFERENCES rooms(chatroom_id) ON DELETE CASCADE").format(
                                  sql.Identifier(table), sql.Identifier(table + "_room_fk")))
        for table in ("members", "called_residents", "deliveries"):
            c.execute(sql.SQL("ALTER TABLE {} DROP COLUMN position").format(sql.Identifier(table)))
        for column in ("created_at", "updated_at"):
            c.execute(sql.SQL("ALTER TABLE rooms ALTER COLUMN {} TYPE TIMESTAMPTZ "
                              "USING NULLIF({},'')::timestamptz").format(
                                  sql.Identifier(column), sql.Identifier(column)))
        c.execute("CREATE INDEX room_member_identity_idx ON members(lower(actor),chatroom_id)")
        return {"rooms": c.execute("SELECT count(*) FROM rooms").fetchone()[0]}
