from copy import deepcopy
from pathlib import Path
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from management_portal.society_service import (
    changes,
    lineage_domain,
    lineage_placement,
    lineage_program,
)
from management_portal.society_service import lineage_aggregate
from tests.cases.society_service_test_support import install_resource_boundary
from tests.cases.effect_script_support import effect_config, lower_effect_call


ACTOR = "wall07-fixture"
LINEAGE = "lineage_070606070606"
ROOT = Path(__file__).resolve().parents[2]
KINDS = ("event_source", "effect", "filter", "selector", "reducer", "seed")


def component(kind, index=1, **fields):
    config = (
        {"event_type": "manual.triggered"}
        if kind == "event_source"
        else effect_config("DeliverMessage", {"resident_id": {"from": "endpoint.target"}}, ["target"])
        if kind == "effect"
        else {"operator_implementation": "rules"}
        if kind in ("filter", "selector", "reducer")
        else {}
    )
    return {"id": f"{kind}_{index:012x}", "kind": kind, "title": kind, "config": config, **fields}


@pytest.fixture()
def isolated_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(lineage_aggregate, "DATA_ROOT", tmp_path / "lineages")
    monkeypatch.setattr(lineage_domain, "LINEAGE_FOLDER_ROOT", tmp_path / "folders")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(tmp_path / "folders"))
    install_resource_boundary(monkeypatch)
    monkeypatch.setattr(lineage_domain, "_actor_can_manage_lineage", lambda lineage, actor, **kwargs: actor == ACTOR and lineage["lineage_id"] == LINEAGE)
    monkeypatch.setattr(lineage_aggregate, "_actor_can_manage_lineage", lambda lineage, actor, **kwargs: actor == ACTOR and lineage["lineage_id"] == LINEAGE)
    monkeypatch.setattr(
        lineage_aggregate,
        "read_lineage",
        lambda lineage_id, **_kwargs: lineage_aggregate.read_lineage_state(lineage_id),
    )
    monkeypatch.setattr(lineage_domain, "get_lineage", lambda lineage_id, actor, **kwargs: lineage_aggregate.read_lineage_state(lineage_id))
    monkeypatch.setattr(changes, "lineage_changed", lambda *args, **kwargs: None)
    persist = lineage_domain._persist_lineage_state

    def isolated_persist(previous, lineage, reason, **kwargs):
        kwargs.update(synchronize_resource_identities=False, synchronize_resident_identities=False)
        return persist(previous, lineage, reason, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Lifecycle/AI side effect is forbidden in the helper fixture")

    monkeypatch.setattr(lineage_domain, "_persist_lineage_state", isolated_persist)
    monkeypatch.setattr(lineage_domain, "_create_packet_operator_executor", forbidden)
    monkeypatch.setattr(lineage_domain, "_execute_program_contract_cleanup", lambda *args, **kwargs: [])
    base = {"lineage_id": LINEAGE, "lineage_membership": "formal", "title": "wall07 isolated", "nodes": [], "edges": []}
    state = lineage_domain._normalize_lineage_payload(base, base, ACTOR)
    isolated_persist(None, state, "test fixture initial facts")
    return lineage_aggregate.read_lineage_state


def test_resident_anchor_priority_without_lifecycle(subtests, isolated_portal_case):
    """新 Resident 节点依次取显式坐标、near_resident_id、Leader 或首个节点作锚点"""
    for anchor in ["near", "leader", "first", "empty", "explicit", "fork_source"]:
        with subtests.test(anchor=anchor), isolated_portal_case() as case_path:
            nodes = [
                {"id": "resident_010101010101", "x": -200, "y": -200},
                {"id": "resident_020202020202", "x": 200, "y": 200},
                {"id": "resident_030303030303", "x": 600, "y": 600},
            ]
            lineage = {"nodes": nodes, "leader_resident_id": nodes[1]["id"], "edges": []}
            before = deepcopy(lineage)
            payload = {}
            selected = nodes[1]
            if anchor == "near":
                payload["near_resident_id"] = nodes[2]["id"]
                selected = nodes[2]
            elif anchor == "first":
                lineage["leader_resident_id"] = ""
                selected = nodes[0]
            elif anchor == "empty":
                lineage["nodes"] = []
                selected = {"x": 20, "y": 35}
            elif anchor == "explicit":
                payload = {"x": 1000, "y": -1000, "near_resident_id": nodes[2]["id"]}
                selected = payload
            if anchor == "fork_source":
                selected = nodes[0]
                actual = lineage_placement.node_position(lineage, selected, payload)
            else:
                actual = lineage_domain._new_lineage_node_position(lineage, payload)
            if anchor in ("empty", "explicit"):
                assert actual == (selected["x"], selected["y"])
            else:
                assert actual[0] == selected["x"]
                assert abs(actual[1] - selected["y"]) == pytest.approx(13.12)
            assert lineage["edges"] == before["edges"]
            assert nodes == before["nodes"]


@pytest.mark.parametrize("anchor", ["near_over_neighbor", "neighbor_over_creator", "creator_over_leader", "leader", "first", "empty"])
def test_program_unconnected_and_competing_anchors(anchor):
    """新组件位置依次取 near_resident_id、连线邻居、创建者、Leader 或首个 Resident 作锚点"""
    residents = [{"id": f"resident_{index:012x}", "x": index * 200, "y": index * 200} for index in range(1, 5)]
    old_component = component("event_source", 99, x=1000, y=1000)
    previous = {"nodes": residents, "edges": [], "leader_resident_id": residents[1]["id"], "program": {"components": [old_component], "wires": []}}
    candidate = deepcopy(previous)
    new_component = component("effect", 7)
    candidate["program"]["components"].append(new_component)
    request = {}
    selected = residents[1]
    if anchor in ("near_over_neighbor", "neighbor_over_creator"):
        candidate["program"]["wires"] = [{"source": old_component["id"], "target": new_component["id"]}]
        new_component["created_by_principal_id"] = residents[2]["id"]
        selected = old_component
    if anchor == "near_over_neighbor":
        request["near_resident_id"] = residents[3]["id"]
        selected = residents[3]
    if anchor == "creator_over_leader":
        new_component["created_by_principal_id"] = residents[2]["id"]
        selected = residents[2]
    if anchor == "first":
        candidate["leader_resident_id"] = ""
        selected = residents[0]
    if anchor == "empty":
        candidate["nodes"] = []
        candidate["leader_resident_id"] = ""
        candidate["program"]["components"] = [new_component]
        selected = {"x": 20, "y": 35}
    before = deepcopy(candidate)
    lineage_placement.place_new_components(previous, candidate, request)
    assert new_component["x"] == selected["x"]
    assert abs(new_component["y"] - selected["y"]) < 13
    assert candidate["nodes"] == before["nodes"]
    assert candidate["edges"] == before["edges"]
    assert {key: value for key, value in new_component.items() if key not in ("x", "y")} == {key: value for key, value in before["program"]["components"][-1].items() if key not in ("x", "y")}


@pytest.mark.parametrize("kind", KINDS)
def test_single_create_real_owner_assigns_and_preserves_free_coordinates(isolated_owner, kind):
    """单个创建任一类组件时保留请求给出的空闲坐标并提升 revision"""
    previous = isolated_owner(LINEAGE)
    created = lineage_domain.create_lineage_program_component(LINEAGE, {**component(kind), "expected_revision": previous["revision"], "x": 70, "y": 65}, ACTOR)
    committed = isolated_owner(LINEAGE)
    assert committed["revision"] > previous["revision"]
    actual = committed["program"]["components"][-1]
    assert actual["kind"] == kind
    assert (actual["x"], actual["y"]) == (70, 65)
    assert created["lineage"]["program"] == committed["program"]
    assert committed["nodes"] == []


@pytest.mark.parametrize("entry", ["initial", "patch", "batch", "clone"])
def test_program_commit_paths_assign_missing_or_occupied_positions(isolated_owner, entry):
    """初始写入、patch、批量变更和克隆提交的组件缺坐标或坐标重叠时各自得到不重复位置"""
    previous = isolated_owner(LINEAGE)
    components = [component(kind, index + 10, x=30, y=40) for index, kind in enumerate(KINDS)]
    if entry == "initial":
        normalized = lineage_program.normalize_program({"components": [component(kind, index + 10) for index, kind in enumerate(KINDS)], "wires": []}, lineage_nodes=[])
        assert all(item.get("x") is None and item.get("y") is None for item in normalized["components"])
        candidate = deepcopy(previous)
        candidate["program"] = normalized
        lineage_domain._persist_lineage_state(None, candidate, "initial program fixture")
    elif entry == "patch":
        lineage_domain._apply_lineage_graph_patch(LINEAGE, {"expected_revision": previous["revision"], "patch": {"program": {"components": components, "wires": []}}}, ACTOR, synchronize_external_projections=False)
    else:
        operations = [{"op": "add", "kind": "component", "value": item} for item in components]
        lineage_domain.apply_lineage_changes(LINEAGE, {"expected_revision": previous["revision"], "program_operations": operations}, ACTOR)
        if entry == "clone":
            current = isolated_owner(LINEAGE)
            lineage_domain.apply_lineage_changes(LINEAGE, {"expected_revision": current["revision"], "program_operations": [{"op": "clone", "kind": "component", "id": item["id"], "new_id": f"{item['kind']}_{index + 100:012x}"} for index, item in enumerate(components)]}, ACTOR)
    committed = isolated_owner(LINEAGE)
    positions = [(item["x"], item["y"]) for item in committed["program"]["components"]]
    assert len(positions) == (12 if entry == "clone" else 6)
    assert len(set(positions)) == len(positions)
    assert all(isinstance(value, (int, float)) for position in positions for value in position)
    assert committed["nodes"] == []


def test_layout_persists_without_program_compile_or_semantic_conflict(isolated_owner, monkeypatch):
    """布局更新只提升 layout_revision，不编译程序也不与程序 revision 冲突"""
    created = lineage_domain.create_lineage_program_component(LINEAGE, {**component("event_source"), "expected_revision": isolated_owner(LINEAGE)["revision"]}, ACTOR)
    previous = isolated_owner(LINEAGE)
    identity = created["component"]["id"]
    compile_calls = []
    real_compile = lineage_program.compile_program

    def observe_compile(*args, **kwargs):
        compile_calls.append(True)
        return real_compile(*args, **kwargs)

    monkeypatch.setattr(lineage_program, "compile_program", observe_compile)
    result = lineage_domain.update_lineage_layout(LINEAGE, {"expected_revision": -999, "positions": [{"object_id": identity, "x": -50, "y": 125}]}, ACTOR)
    assert compile_calls == []
    reloaded = isolated_owner(LINEAGE)
    assert reloaded["revision"] == previous["revision"]
    assert reloaded["layout_revision"] > previous["layout_revision"]
    assert result["layout_revision"] == reloaded["layout_revision"]
    assert (reloaded["program"]["components"][0]["x"], reloaded["program"]["components"][0]["y"]) == (-50, 125)
    lineage_domain.create_lineage_program_component(LINEAGE, {**component("seed"), "expected_revision": previous["revision"]}, ACTOR)
    assert isolated_owner(LINEAGE)["revision"] > previous["revision"]


@pytest.fixture()
def browser_owner_server(isolated_owner):
    lineage_domain.create_lineage_program_component(LINEAGE, {**component("event_source"), "expected_revision": isolated_owner(LINEAGE)["revision"], "x": 30, "y": 40}, ACTOR)
    released = threading.Event()
    released.set()
    received = []
    html = """<!doctype html><style>body{margin:0}.root{position:relative;width:1000px;height:700px;overflow:hidden}.world{position:absolute;width:1280px;height:900px;transform-origin:0 0}.layer{position:absolute;inset:0;width:100%;height:100%}.hatgNode{position:absolute;width:154px;height:94px;transform:translate(-50%,-50%)}</style><div id="root" class="root"><div id="world" class="world"><svg id="edges" class="layer"></svg><svg id="effects" class="layer"></svg><svg id="labels" class="layer"></svg><div id="nodes" class="layer"></div></div></div>"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def send_value(self, value, status=200):
            encoded = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())
            elif self.path == "/state":
                self.send_value(isolated_owner(LINEAGE))
            elif self.path == "/observations":
                self.send_value(received)
            else:
                self.send_value({"error": "unexpected fixture URL"}, 404)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/hold":
                released.clear()
                self.send_value({"held": True})
                return
            if self.path == "/release":
                released.set()
                self.send_value({"released": True})
                return
            before = isolated_owner(LINEAGE)
            event = {"path": self.path, "request": request, "layout_held": not released.is_set(), "before_positions": [[item["id"], item["x"], item["y"]] for item in before["program"]["components"]]}
            received.append(event)
            try:
                if self.path == "/lineage/api/program/components/create":
                    assert request["kind"] in ("event_source", "seed")
                    response = lineage_domain.create_lineage_program_component(LINEAGE, request, ACTOR)
                elif self.path == "/lineage/api/layout/positions":
                    assert released.wait(20), "Fixture layout gate was not released"
                    response = lineage_domain.update_lineage_layout(LINEAGE, request, ACTOR)
                else:
                    raise AssertionError(self.path)
                event["response"] = response
                self.send_value(response)
            except Exception as error:
                event["error"] = str(error)
                self.send_value({"error": str(error)}, 500)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        released.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
