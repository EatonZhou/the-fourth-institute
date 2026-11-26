"""Classify the blackboard instructions in saved permission schemes (plan_786a2906c941).

The default scheme classifies them in its built-in document.  A saved scheme (a copy, an instance or
a source) lists instruction elements it was written with, and an element it lacks sits under the
"unclassified" default, which only administrators pass.  This migration gives each saved scheme the
blackboard elements the Lineage state instructions already have in it: reading, watching and moving a
read position follow ``GetSharedState`` and writing and retracting follow ``UpdateSharedState``,
because decision_ed2c1ed5c245 asks the same levels of them (@viewer and @contributor on the Lineage).

It only adds missing elements and never changes one a scheme already classifies.  Grants written for
one instruction element (temporary credentials included) are not widened to the new elements; a grant on
an A class node such as @contributor covers them through the class.
"""
from __future__ import annotations

import copy
import json

MIGRATION = "blackboard-20261127"

# New element -> the existing element whose classification it takes.
FOLLOWS = {
    "ListBoardEntries": "GetSharedState",
    "WatchBoard": "GetSharedState",
    "SetBoardReadPosition": "GetSharedState",
    "CreateBoardEntry": "UpdateSharedState",
    "DeleteBoardEntry": "UpdateSharedState",
}


def rules(document):
    """``document`` with the missing blackboard elements added, or the same document when nothing is missing."""

    instructions = document.get("instructions") if isinstance(document, dict) else None
    if not isinstance(instructions, dict):
        return document
    added = {new: copy.deepcopy(instructions[old]) for new, old in FOLLOWS.items()
             if new not in instructions and old in instructions}
    if not added:
        return document
    return {**document, "instructions": {**instructions, **added}}


def migrate(store):
    from .compiler import rules_digest
    from .repository import source_digest

    with store.read() as con:
        if store.execute(con, "SELECT 1 FROM {s}.scheme_migrations WHERE migration_id=%s", (MIGRATION,)).fetchone():
            return False
    with store.transaction() as con:
        store.execute(con, "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (store.schema + ":" + MIGRATION,))
        if store.execute(con, "SELECT 1 FROM {s}.scheme_migrations WHERE migration_id=%s", (MIGRATION,)).fetchone():
            return False
        store.execute(con, "LOCK TABLE {s}.schemes,{s}.scheme_instances,{s}.scheme_sources,{s}.scheme_nodes"
                      " IN SHARE ROW EXCLUSIVE MODE")
        history = []
        for table, column in (("schemes", "document"), ("scheme_instances", "rules"), ("scheme_sources", "rules")):
            for row in store.execute(con, f"SELECT * FROM {{s}}.{table} WHERE {column} IS NOT NULL").fetchall():
                document = rules(row[column])
                if document == row[column]:
                    continue
                history.append({"table": table, "scheme_id": row["scheme_id"],
                                "added": sorted(set(document["instructions"]) - set(row[column]["instructions"]))})
                encoded = json.dumps(document, ensure_ascii=False)
                if table == "schemes":
                    store.execute(con, "UPDATE {s}.schemes SET document=%s::jsonb,version=version+1 WHERE scheme_id=%s",
                                  (encoded, row["scheme_id"]))
                elif table == "scheme_instances":
                    digest = (rules_digest(document) if rules_digest(row[column]) == row["rules_digest"]
                              else row["rules_digest"])
                    store.execute(con, "UPDATE {s}.scheme_instances SET rules=%s::jsonb,rules_digest=%s WHERE scheme_id=%s",
                                  (encoded, digest, row["scheme_id"]))
                else:
                    store.execute(con, "UPDATE {s}.scheme_sources SET rules=%s::jsonb,digest=%s WHERE scheme_id=%s",
                                  (encoded, source_digest(document), row["scheme_id"]))
        # An A element a scheme places on its own subtree is placed beside the element it follows.
        nodes = [dict(row) for row in store.execute(con, "SELECT * FROM {s}.scheme_nodes WHERE tree='A'").fetchall()]
        present = {(row["scheme_id"], row["id"]) for row in nodes}
        for row in nodes:
            for new, old in FOLLOWS.items():
                if row["id"] != old or (row["scheme_id"], new) in present:
                    continue
                present.add((row["scheme_id"], new))
                store.execute(
                    con, "INSERT INTO {s}.scheme_nodes(scheme_id,tree,id,parent,kind,path) VALUES (%s,%s,%s,%s,%s,%s)",
                    (row["scheme_id"], "A", new, row["parent"], row["kind"], [*row["path"][:-1], new]))
                history.append({"table": "scheme_nodes", "scheme_id": row["scheme_id"], "added": [new]})
        if history:
            store.bump(con)
        store.execute(con, "INSERT INTO {s}.scheme_migration_history(migration_id,records) VALUES (%s,%s::jsonb)",
                      (MIGRATION, json.dumps(history, ensure_ascii=False, default=str)))
        store.execute(con, "INSERT INTO {s}.scheme_migrations(migration_id) VALUES (%s)", (MIGRATION,))
    return True
