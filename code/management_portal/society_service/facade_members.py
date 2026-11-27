"""Authoritative recipients of a facade's relayed inputs."""
from __future__ import annotations

from typing import Any

from .identity_contract import validate_id


def ensure_schema(connection: Any) -> None:
    """Install and backfill once, under the Lineage schema initialization lock."""
    exists = connection.execute(
        "SELECT to_regclass(quote_ident(current_schema()) || '.facade_members') AS relation"
    ).fetchone()["relation"]
    if exists is not None:
        return
    connection.execute("""
        CREATE TABLE facade_members (
            facade_id TEXT NOT NULL REFERENCES residents(resident_id)
                ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
            member_id TEXT NOT NULL REFERENCES residents(resident_id)
                ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
            PRIMARY KEY (facade_id, member_id),
            CHECK (facade_id <> member_id))
    """)
    connection.execute("CREATE INDEX facade_members_member_idx ON facade_members(member_id)")
    connection.execute("""
        INSERT INTO facade_members(facade_id,member_id)
        SELECT facade.resident_id, target.resident_id
        FROM family_residents facade
        JOIN families f USING(family_id)
        JOIN family_residents target USING(family_id)
        WHERE f.deleted_at IS NULL AND facade.role='facade'
          AND target.role IN ('member','steward')
    """)


def read(connection: Any, resident_id: str) -> list[str]:
    return [row["member_id"] for row in connection.execute(
        "SELECT member_id FROM facade_members WHERE facade_id=%s ORDER BY member_id",
        (resident_id,),
    ).fetchall()]


def read_many(connection: Any, resident_ids: list[str]) -> dict[str, list[str]]:
    members: dict[str, list[str]] = {resident_id: [] for resident_id in resident_ids}
    for row in connection.execute(
        "SELECT facade_id, member_id FROM facade_members WHERE facade_id=ANY(%s) ORDER BY facade_id, member_id",
        (resident_ids,),
    ).fetchall():
        members[row["facade_id"]].append(row["member_id"])
    return members


def normalize(resident_id: str, member_ids: list[str]) -> list[str]:
    facade_id = validate_id(resident_id, "resident").resource_id
    if not isinstance(member_ids, list):
        raise ValueError("member_ids 必须为数组")
    members = sorted({validate_id(member_id, "resident").resource_id for member_id in member_ids})
    if facade_id in members:
        raise ValueError("门面不能将自己设为转交成员")
    return members


def replace(connection: Any, resident_id: str, member_ids: list[str]) -> list[str]:
    """Replace the complete target set in the enclosing write transaction."""
    members = normalize(resident_id, member_ids)
    # Serialize replacements for every facade: two separately acyclic edits
    # must not introduce a cycle when they commit together.
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':facade-members', 0))"
    )
    if members:
        cycle = connection.execute("""
            WITH RECURSIVE reachable(resident_id) AS (
                SELECT unnest(%s::text[])
                UNION
                SELECT edge.member_id FROM facade_members edge
                JOIN reachable path ON path.resident_id=edge.facade_id
            )
            SELECT 1 AS found FROM reachable WHERE resident_id=%s
        """, (members, resident_id)).fetchone()
        if cycle:
            raise ValueError("门面转交成员不能形成闭环")
    connection.execute(
        "DELETE FROM facade_members WHERE facade_id=%s AND NOT (member_id=ANY(%s))",
        (resident_id, members),
    )
    if members:
        connection.execute("""
            INSERT INTO facade_members(facade_id,member_id)
            SELECT %s, unnest(%s::text[]) ON CONFLICT DO NOTHING
        """, (resident_id, members))
    return members
