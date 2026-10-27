"""Authored program wires accept only the current binding kinds and ports."""
import pytest

from management_portal.hatg.catalog.lineage_definition import WIRE_KINDS
from management_portal.society_service import graph_storage, lineage_program
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
