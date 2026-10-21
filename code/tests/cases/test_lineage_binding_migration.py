"""Binding storage, one-time cutover and authored graph contracts."""
import json

import pytest

from management_common.json_columns import json_value
from management_portal.hatg.catalog.lineage_definition import WIRE_KINDS
from management_portal.society_service import graph_storage, lineage_program
from management_portal.society_service.state_store import get_lineage_data_store, lineage_data_schema_matches
from scripts.migrate_lineage_bindings import migrate, migrate_graph, OLD_SCHEMA, NEW_SCHEMA
from tests.cases.test_family_familize import lineage, future_creation_store, resident  # noqa: F401


def test_binding_contract_rejects_retired_edges_and_reference_ports(lineage):
    """程序连线拒绝已退役的 inspect 连线、反向 watch 和 reference 端口"""
    a = resident(lineage, "Subject")
    nodes = [{"id": a}]
    event = {"id": "event_source_observer", "kind": "event_source", "config": {"event_type": "resident.work_finished"}}
    assert set(WIRE_KINDS) == set(graph_storage.WIRE_TYPES) == lineage_program.PROGRAM_WIRE_KINDS
    for wire in (
        {"kind": "inspect", "source": a, "target": a},
        {"kind": "watch", "source": a, "target": event["id"]},
        {"kind": "watch", "source": event["id"], "target": a, "target_port": "reference"},
    ):
        with pytest.raises(ValueError):
            lineage_program.normalize_program({"components": [event], "wires": [{"id": "wire_test", **wire}]}, lineage_nodes=nodes)


def test_cutover_preserves_identity_progress_and_observed_edges(lineage):
    """绑定迁移翻转 watch 连线方向并去掉目标端口，保留事件源进度和草稿，可重复执行"""
    store = get_lineage_data_store()
    a, b = resident(lineage, "A"), resident(lineage, "B")

    def run(c):
        schema = c.execute("SELECT current_schema() AS name").fetchone()["name"]
        # Reconstruct the predecessor storage contract; runtime never accepts it.
        c.execute("ALTER TABLE lineage_edges DROP CONSTRAINT lineage_edges_endpoints_check")
        c.execute("ALTER TABLE lineage_edges DROP CONSTRAINT lineage_edges_binding_ports_check")
        c.execute("ALTER TABLE lineage_edges ADD CHECK(deleted_at IS NOT NULL OR (edge_type IS NOT NULL AND target_node_id IS NOT NULL AND (source_node_id IS NOT NULL OR edge_type='watch')))")
        c.execute("ALTER TABLE lineage_edges ADD CHECK(source_node_id IS NOT NULL OR edge_type='watch')")
        c.execute("ALTER TABLE lineage_observation_targets RENAME COLUMN target_edge_id TO source_edge_id")
        c.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (OLD_SCHEMA,))
        c.execute("INSERT INTO lineage_nodes(node_id,lineage_id,node_type) VALUES ('event_source_observer',%s,'event_source')", (lineage,))
        c.execute("INSERT INTO lineage_edges(edge_id,lineage_id,edge_type,source_node_id,target_node_id,source_port,target_port) VALUES "
                  "('wire_subject',%s,'route','event_source_observer',%s,'event','invoke'),"
                  "('wire_watch_node',%s,'watch',%s,'event_source_observer','subject','reference'),"
                  "('wire_watch_edge',%s,'watch',NULL,'event_source_observer','subject','reference'),"
                  "('wire_target',%s,'target',%s,%s,'worker','reference'),"
                  "('edge_origin',%s,'inheritance',%s,%s,NULL,NULL)",
                  (lineage,a,lineage,a,lineage,lineage,a,b,lineage,a,b))
        c.execute("INSERT INTO lineage_observation_targets VALUES ('wire_watch_edge','wire_subject')")
        c.execute("INSERT INTO event_source_progress(lineage_id,source_id,checkpoint_json) VALUES (%s,'event_source_observer',%s)", (lineage, '{"last_event":"retained"}'))
        original = {"program": {"wires": [{"id": "wire_watch_node", "kind": "watch", "source": a, "target": "event_source_observer", "target_port": "reference"}]}}
        c.execute("INSERT INTO lineage_drafts(lineage_id,draft_id,draft_json) VALUES (%s,'draft_binding',%s)", (lineage,json.dumps(original)))
        result = migrate(c,schema=schema,template_schema=schema)
        assert (result["watch"], result["target"], result["drafts"]) == (2,1,1)
        assert c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()["value"] == NEW_SCHEMA
        graph = graph_storage.read_many(c,[lineage])[lineage]
        wires = {w["id"]: w for w in graph["program"]["wires"]}
        assert wires["wire_watch_node"]["source"] == "event_source_observer"
        assert wires["wire_watch_node"]["target"] == a
        assert wires["wire_watch_edge"]["target"] == "wire_subject"
        assert wires["wire_watch_edge"]["source_port"] == "watch"
        assert all("target_port" not in w for w in wires.values() if w["kind"] in {"watch","target"})
        assert graph["edges"][0]["source"] == a
        assert json_value(c.execute("SELECT checkpoint_json FROM event_source_progress WHERE source_id='event_source_observer'").fetchone()["checkpoint_json"]) == {"last_event": "retained"}
        assert migrate(c,schema=schema)["already_applied"]
        graph_storage.put(c,lineage,graph)
        assert graph_storage.read_many(c,[lineage])[lineage] == graph

    with store.database.transaction() as connection, connection.raw.transaction(force_rollback=True):
        run(connection)
    with store.database.read() as connection:
        assert lineage_data_schema_matches(connection)


def test_template_graph_and_match_migrate_without_rewriting_payloads():
    """模板图和匹配规则迁移 watch 连线方向，不改写 config 和 parameters 内容"""
    original = {"wires": [{"key":"w", "kind":"watch", "source":"subject", "target":"observer", "source_port":"subject", "target_port":"reference", "config":{"wires":[{"kind":"watch","source":"literal","target":"data"}]}}],
                "match":[{"kind":"watch","source":"subject"}],
                "parameters":[{"kind":"watch","source":"literal","target":"data"}]}
    updated = migrate_graph(original)
    assert updated["wires"][0]["source"] == "observer"
    assert updated["wires"][0]["target"] == "subject"
    assert updated["wires"][0]["source_port"] == "watch"
    assert "target_port" not in updated["wires"][0]
    assert updated["match"] == [{"kind":"watch","target":"subject"}]
    assert updated["wires"][0]["config"] == original["wires"][0]["config"]
    assert updated["parameters"] == original["parameters"]
