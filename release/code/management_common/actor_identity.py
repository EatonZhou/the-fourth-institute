from __future__ import annotations


def canonical_actor_id(value: object) -> str:
    actor_id = str(value or "").strip()
    while actor_id.startswith(("@", "＠")):
        actor_id = actor_id[1:].strip()
    return actor_id


def same_actor(left: object, right: object) -> bool:
    left_id = canonical_actor_id(left)
    right_id = canonical_actor_id(right)
    return bool(left_id and right_id and left_id.casefold() == right_id.casefold())
