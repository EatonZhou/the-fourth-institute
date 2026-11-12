"""Round-two graph and Paper history regressions, using synthetic data only.

:design: decision_3ece26686464, decision_528bf9985705
"""
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge_board_lan import knowledge_backend as knowledge
from management_portal import paper_assets, paper_store, resource_views, web_resources


def graph_service(tmp_path, monkeypatch, *, count=6):
    records = []
    for index in range(count):
        resource_id = f"idea_{index:012x}"
        path = tmp_path / f"{index}.md"
        path.write_text(f"---\nlinks:\n  related: [idea_{max(0, index - 1):012x}]\n---\n", encoding="utf-8")
        records.append({"resource_id": resource_id, "resource_type": "idea",
                        "title": f"Item {index}", "summary": f"Preview {index}",
                        "source": {"path": str(path)},
                        "attributes": {"type": "idea", "importance": index,
                                       "project_ids": ["fixture"], "project_status": "active"}})
    visible = {"alice": records, "bob": records[:1]}
    def query(*, actor, offset=0, limit=5000, **kwargs):
        rows = visible[actor]
        return {"resources": rows[offset:offset + limit], "total": len(rows)}
    service = Mock()
    service.query.side_effect = query
    service.projection_state.return_value = {}
    service.status.return_value = {}
    monkeypatch.setattr(knowledge, "get_resource_service", lambda: service)
    monkeypatch.setattr(knowledge, "_KNOWLEDGE_GRAPH_CACHE", {})
    return service, visible


def test_graph_paths_follow_selected_importance_order(tmp_path, monkeypatch):
    service, _ = graph_service(tmp_path, monkeypatch)
    graph = knowledge.build_knowledge_graph(actor="alice", max_nodes=2)
    assert {node["kb_id"] for node in graph["nodes"]} == {"idea_000000000005", "idea_000000000004"}
    assert graph["edges"] == [{"source": "idea_000000000005", "target": "idea_000000000004", "relation": "related"}]
    assert graph["unresolved_links"] == 1
    assert all("path" not in node for node in graph["nodes"])


def test_graph_reuses_one_visible_query_and_preserves_actor_cache(tmp_path, monkeypatch):
    service, visible = graph_service(tmp_path, monkeypatch)
    alice = knowledge.build_knowledge_graph(actor="alice")
    bob = knowledge.build_knowledge_graph(actor="bob")
    assert len(alice["nodes"]) == 6 and len(bob["nodes"]) == 1
    assert knowledge.build_knowledge_graph(actor="alice") == alice
    visible["alice"] = visible["alice"][:1]
    refreshed = knowledge.build_knowledge_graph(actor="alice", refresh=True)
    assert len(refreshed["nodes"]) == 1
    assert [call.kwargs["actor"] for call in service.query.call_args_list] == ["alice", "bob", "alice"]


def test_graph_keeps_late_page_high_priority_node(tmp_path, monkeypatch):
    service, visible = graph_service(tmp_path, monkeypatch, count=2)
    tail = visible["alice"][1]
    # Only two actual files; 5,000 low-priority index entries precede the tail.
    visible["alice"] = [{**visible["alice"][0], "resource_id": f"idea_{index+10:012x}"}
                        for index in range(5000)] + [tail]
    graph = knowledge.build_knowledge_graph(actor="alice", max_nodes=1)
    assert [node["kb_id"] for node in graph["nodes"]] == [tail["resource_id"]]
    assert graph["unresolved_links"] == 1
    assert [call.kwargs.get("offset", 0) for call in service.query.call_args_list] == [0, 5000]


def test_graph_selection_matches_index_metadata_and_order(tmp_path, monkeypatch):
    service, visible = graph_service(tmp_path, monkeypatch)
    for record in visible["alice"]:
        record["attributes"]["importance"] = 1
        record["attributes"]["created_at"] = "2026-11-12"
        record["attributes"]["mtime"] = int(record["resource_id"].split("_")[1], 16)
    index = resource_views.knowledge_index(active_only=False, limit=2, actor="alice", service=service)
    graph = knowledge.build_knowledge_graph(actor="alice", max_nodes=2)
    expected = {item["kb_id"]: item for item in index["items"]}
    assert set(expected) == {node["kb_id"] for node in graph["nodes"]}
    for node in graph["nodes"]:
        for field in ("title", "preview", "type", "project", "importance", "mtime"):
            assert node[field] == expected[node["kb_id"]][field]


def test_cached_graph_still_checks_current_visibility_before_output(tmp_path, monkeypatch):
    service, visible = graph_service(tmp_path, monkeypatch, count=2)
    monkeypatch.setattr(web_resources, "get_resource_service", lambda: service)
    cached = knowledge.build_knowledge_graph(actor="alice")
    visible["alice"] = visible["alice"][:1]
    result = web_resources._filter_resource_graph(knowledge.build_knowledge_graph(actor="alice"), "alice")
    assert [node["kb_id"] for node in result["nodes"]] == ["idea_000000000000"]
    assert all(edge["source"] == edge["target"] == "idea_000000000000" for edge in result["edges"])
    assert len(cached["nodes"]) == 2
    assert len(service.query.call_args_list) == 2


@pytest.fixture
def runs(tmp_path, monkeypatch):
    bundle = tmp_path / "paper"
    bundle.mkdir()
    (bundle / "runs").mkdir()
    monkeypatch.setattr(paper_store.PAPER_STORE, "manifest_for_paper_id",
                        lambda _: (bundle / "manifest.json", {"paper_id": "paper_000000000001"}))
    monkeypatch.setattr(paper_store.PAPER_STORE, "paper_dir_from_manifest", lambda *_: bundle)
    return bundle / "runs"


def write_run(runs, name, **fields):
    path = runs / f"{name}.json"
    path.write_text(json.dumps({"job_id": name, "kind": "assets", **fields}), encoding="utf-8")
    return path


@pytest.mark.parametrize("count", [32, 256])
def test_assets_latest_bounds_retained_records_without_stat_sort(runs, monkeypatch, count, record_property):
    for index in range(count):
        write_run(runs, f"asset-{index}", created_at_ns=index + 1)
    metrics = {"live": 0, "peak": 0, "loads": 0, "stats": 0}
    class Tracked(dict):
        def __init__(self, value):
            super().__init__(value)
            metrics["live"] += 1
            metrics["peak"] = max(metrics["peak"], metrics["live"])
        def __del__(self):
            metrics["live"] -= 1
    load, stat = paper_store._load_json, Path.stat
    def tracked_load(path, default):
        metrics["loads"] += 1
        return Tracked(load(path, default))
    def tracked_stat(path, *args, **kwargs):
        if path.parent == runs and path.suffix == ".json":
            metrics["stats"] += 1
        return stat(path, *args, **kwargs)
    monkeypatch.setattr(paper_store, "_load_json", tracked_load)
    monkeypatch.setattr(Path, "stat", tracked_stat)
    job = paper_assets._latest_assets_job("paper_000000000001")
    for key, value in metrics.items():
        record_property(key, value)
    assert job["job_id"] == f"asset-{count-1}"
    assert metrics["loads"] == count
    assert metrics["peak"] <= 4, metrics
    assert metrics["stats"] == 0, metrics


def test_assets_keep_creation_order_ties_and_legacy_fallback(runs):
    old = write_run(runs, "old-pdf", created_at_ns=10**9, updated_at=999999)
    write_run(runs, "new-a", queued_at=2)
    write_run(runs, "new-z", created_at_ns=2 * 10**9, attempt_id="current")
    write_run(runs, "other-task", kind="summary", created_at_ns=9 * 10**9)
    os.utime(old, (9999999999, 9999999999))
    (runs / "invalid.json").write_text("not json", encoding="utf-8")
    current = paper_assets._latest_assets_job("paper_000000000001")
    assert current["job_id"] == "new-z"
    assert paper_assets._current_attempt("paper_000000000001", "new-z", "current")
    assert not paper_assets._current_attempt("paper_000000000001", "old-pdf", "old")


def test_assets_status_does_not_recreate_deleted_bundle(runs, monkeypatch):
    import shutil
    bundle = runs.parent
    shutil.rmtree(bundle)
    monkeypatch.setattr(paper_store.PAPER_STORE, "manifest_for_paper_id", lambda _: None)
    assert paper_assets.assets_job_status("paper_000000000001") == {}
    assert not bundle.exists()


def test_history_page_keeps_mtime_selection_and_updated_order(runs):
    for name, mtime, updated in [("a", 1, 300), ("b", 2, 200), ("c", 3, 100)]:
        path = write_run(runs, name, updated_at=updated)
        os.utime(path, (mtime, mtime))
    rows = paper_store._paper_run_records("paper_000000000001", kind="assets", limit=2)
    assert [row["job_id"] for row in rows] == ["b", "c"]
