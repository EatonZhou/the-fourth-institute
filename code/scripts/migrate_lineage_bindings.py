"""One-time v50 -> v51 binding cutover, followed by a SocietyService reload.

Watch is observer -> object. Binding targets have no ports. Graph identities,
event checkpoints, pending deliveries and execution history remain unchanged.
The transaction locks graph tables; new constraints reject legacy binding writes.
"""
from __future__ import annotations

from management_common.format_tags import schema_matches

import argparse
import copy
import json

from psycopg import connect, sql
from psycopg.rows import dict_row

from management_common.json_columns import json_value

OLD_SCHEMA = "tfi.lineage-data.v50"
NEW_SCHEMA = "tfi.lineage-data.v51"


def migrate_graph(value):
    """Transform authored graph collections, never arbitrary config/payload data."""
    result = copy.deepcopy(value)

    def visit(item):
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for field, entries in item.items():
                if field in {"wires", "edges", "match"} and isinstance(entries, list):
                    for edge in entries:
                        if not isinstance(edge, dict):
                            continue
                        kind = edge.get("kind", edge.get("type"))
                        if kind == "watch":
                            # Rewrite patterns may leave either endpoint unspecified.
                            source, target = edge.pop("source", None), edge.pop("target", None)
                            if target is not None:
                                edge["source"] = target
                            if source is not None:
                                edge["target"] = source
                            if field != "match" or "source_port" in edge or "target_port" in edge:
                                edge["source_port"] = "watch"
                        if kind in {"watch", "target"}:
                            edge.pop("target_port", None)
                if field not in {"config", "payload", "parameters", "settings"}:
                    visit(entries)

    visit(result)
    return result


def migrate(connection, *, schema="lineage", template_schema="templates"):
    connection.execute(sql.SQL("SET LOCAL search_path TO {},public").format(sql.Identifier(schema)))
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init',0))")
    marker = connection.execute("SELECT value FROM lineage_meta WHERE key='schema' FOR UPDATE").fetchone()["value"]
    if schema_matches(marker, NEW_SCHEMA):
        return {"already_applied": True}
    if not schema_matches(marker, OLD_SCHEMA):
        raise ValueError("Unexpected Lineage schema: " + marker)
    connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
    connection.execute("LOCK TABLE lineage_edges,lineage_observation_targets,lineage_drafts IN ACCESS EXCLUSIVE MODE")
    changed = connection.execute("SELECT edge_id,lineage_id,edge_type FROM lineage_edges WHERE edge_type IN ('watch','target')").fetchall()
    # Deployed v50 databases may retain the earlier source-presence CHECK too.
    checks = connection.execute("SELECT conname FROM pg_constraint WHERE conrelid='lineage_edges'::regclass "
                                "AND contype='c' AND (pg_get_constraintdef(oid) LIKE '%%source_node_id%%' "
                                "OR pg_get_constraintdef(oid) LIKE '%%target_node_id%%')").fetchall()
    if not checks:
        raise ValueError("Missing predecessor edge endpoint constraints")
    for row in checks:
        connection.execute(sql.SQL("ALTER TABLE lineage_edges DROP CONSTRAINT {}").format(sql.Identifier(row["conname"])))
    connection.execute("UPDATE lineage_edges SET source_node_id=target_node_id,target_node_id=source_node_id,"
                       "source_port='watch',target_port=NULL WHERE edge_type='watch'")
    connection.execute("UPDATE lineage_edges SET target_port=NULL WHERE edge_type='target'")
    connection.execute("ALTER TABLE lineage_observation_targets RENAME COLUMN source_edge_id TO target_edge_id")
    connection.execute("ALTER TABLE lineage_edges ADD CONSTRAINT lineage_edges_endpoints_check "
                       "CHECK(deleted_at IS NOT NULL OR (edge_type IS NOT NULL AND source_node_id IS NOT NULL "
                       "AND (target_node_id IS NOT NULL OR edge_type='watch')))")
    connection.execute("ALTER TABLE lineage_edges ADD CONSTRAINT lineage_edges_binding_ports_check "
                       "CHECK(edge_type NOT IN ('watch','target') OR (target_port IS NULL AND source_port IS NOT NULL "
                       "AND (edge_type<>'watch' OR source_port='watch')))")
    counts = {"watch": sum(row["edge_type"] == "watch" for row in changed),
              "target": sum(row["edge_type"] == "target" for row in changed), "drafts": 0, "templates": 0}
    for row in connection.execute("SELECT lineage_id,draft_id,draft_json FROM lineage_drafts FOR UPDATE").fetchall():
        original = json_value(row["draft_json"])
        updated = migrate_graph(original)
        if updated != original:
            connection.execute("UPDATE lineage_drafts SET draft_json=%s WHERE lineage_id=%s AND draft_id=%s",
                               (json.dumps(updated, ensure_ascii=False), row["lineage_id"], row["draft_id"]))
            counts["drafts"] += 1
    templates = sql.Identifier(template_schema, "templates")
    if connection.execute("SELECT to_regclass(%s) AS name", (template_schema + ".templates",)).fetchone()["name"]:
        for row in connection.execute(sql.SQL("SELECT template_id,body FROM {} FOR UPDATE").format(templates)).fetchall():
            updated = migrate_graph(row["body"])
            if updated != row["body"]:
                connection.execute(sql.SQL("UPDATE {} SET body=%s::jsonb,version=version+1,updated_at=now(),"
                                           "updated_by='system:binding-cutover' WHERE template_id=%s").format(templates),
                                   (json.dumps(updated, ensure_ascii=False), row["template_id"]))
                counts["templates"] += 1
    lineage_ids = sorted({row["lineage_id"] for row in changed})
    connection.execute("UPDATE lineages SET revision=revision+1,updated_at=now(),graph_updated_at=now() "
                       "WHERE lineage_id=ANY(%s)", (lineage_ids,))
    connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (NEW_SCHEMA,))
    return {**counts, "lineages": len(lineage_ids)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", default="lineage")
    parser.add_argument("--template-schema", default="templates")
    args = parser.parse_args()
    from scripts.migrate_domain_records import source_url
    from management_portal.society_service.state_store import lineage_data_schema_matches
    with connect(source_url(), row_factory=dict_row) as connection:
        result = migrate(connection, schema=args.schema, template_schema=args.template_schema)
        if not lineage_data_schema_matches(connection):
            raise RuntimeError("Migrated Lineage schema does not match the current contract")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
