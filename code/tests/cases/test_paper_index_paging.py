"""Bounded Paper SQL reads use the existing PostgreSQL projection and personal favorites."""
from unittest import mock

from management_portal import paper_library, paper_search, presentation_store
from management_portal.resource_index_store import IndexStore
from management_portal.resource_index_records import ChangeSet, resource_record


def seed(store, count=60):
    records = [resource_record(
        resource_id=f"paper_{index:012x}", resource_type="paper", title=f"Title {index % 3}",
        adapter="paper", summary=f"Abstract {index}",
        attributes={"year": "2026" if index % 2 else "2025", "venue": "期刊" if index % 3 else "A",
                    "has_analysis": bool(index % 2), "has_pdf": False, "has_summary": False},
    ) for index in range(count)]
    store.apply(ChangeSet(adapter="paper", upserts=tuple(records)))
    return records


def test_sql_filters_pages_and_explicit_ids(monkeypatch):
    store = IndexStore()
    records = seed(store)
    batches = []
    def readable(items, actor):
        batches.append(len(items))
        return [int(item["resource_id"].split("_")[1], 16) % 5 != 0 for item in items]
    monkeypatch.setattr("management_portal.resource_index_store.readable_flags", readable)
    for filters in ({}, {"year": "2026"}, {"analysis": "missing"},
                    {"year": "2025", "analysis": "ready"},
                    {"ids": [records[1]["resource_id"], records[7]["resource_id"], "paper_ffffffffffff"]}):
        expected = [r for r in records if
                    (not filters.get("year") or r["attributes"]["year"] == filters["year"]) and
                    (not filters.get("analysis") or r["attributes"]["has_analysis"] == (filters["analysis"] == "ready")) and
                    ("ids" not in filters or r["resource_id"] in filters["ids"]) and
                    int(r["resource_id"].split("_")[1], 16) % 5 != 0]
        expected.sort(key=lambda r: (r["attributes"]["year"], r["attributes"]["venue"], r["title"], r["resource_id"]), reverse=True)
        actual = []
        for offset in range(0, len(expected) + 1, 7):
            result = store.query(resource_types=["paper"], paper_rows=filters, actor="alice", offset=offset, limit=7)
            actual.extend(r["resource_id"] for r in result["resources"])
        assert actual == [r["resource_id"] for r in expected]
    assert max(batches) <= 7
    assert store.query(resource_types=["paper"], paper_rows={"ids": []}) == {"resources": []}


def test_library_favorites_are_personal_and_read_access_precedes_offset(monkeypatch):
    store = IndexStore()
    records = seed(store)
    alice_ids = [r["resource_id"] for r in records[:4]]
    bob_ids = [r["resource_id"] for r in records[4:8]]
    for actor, identifiers in (("alice", alice_ids), ("bob", bob_ids)):
        for identifier in identifiers:
            presentation_store.set_favorite(actor, "paper", identifier, True)
    monkeypatch.setattr(paper_search, "get_resource_service", lambda: store)
    monkeypatch.setattr("management_portal.resource_index_store.readable_flags",
                        lambda items, actor: [r["resource_id"] != alice_ids[0] for r in items])
    with mock.patch.object(paper_library, "resource_index_papers", side_effect=AssertionError("full library")), \
         mock.patch("management_portal.paper_store._all_papers", side_effect=AssertionError("manifests")), \
         mock.patch("management_portal.paper_store.PaperStore.manifest_paths", side_effect=AssertionError("scan")):
        for actor, allowed in (("alice", set(alice_ids[1:])), ("bob", set(bob_ids))):
            params = {"compact": "1", "rows_only": "1", "favorite": "1", "limit": "2"}
            first = paper_library.list_library(params, actor=actor)["papers"]
            second = paper_library.list_library({**params, "offset": "2"}, actor=actor)["papers"]
            assert {row["id"] for row in first + second} == allowed
            assert len(first) == 2
    assert paper_library.list_library({"compact": "1", "rows_only": "1", "favorite": "1"}, actor="") == {"papers": []}


def test_small_page_reads_only_bounded_index_rows(monkeypatch):
    store = IndexStore()
    seed(store)
    observed = []
    monkeypatch.setattr("management_portal.resource_index_store.readable_flags",
                        lambda rows, actor: observed.append(len(rows)) or [True] * len(rows))
    result = store.query(resource_types=["paper"], paper_rows={}, actor="alice", limit=3)
    assert len(result["resources"]) == 3
    assert observed == [3]


def test_batch_ids_preserve_order_and_cross_500_without_full_library(monkeypatch):
    store = IndexStore()
    records = seed(store, 600)
    monkeypatch.setattr(paper_search, "get_resource_service", lambda: store)
    observed = []
    def readable(rows, actor):
        observed.append(len(rows))
        return [r["resource_id"] != records[3]["resource_id"] for r in rows]
    monkeypatch.setattr("management_portal.resource_index_store.readable_flags", readable)
    identifiers = [r["resource_id"] for r in reversed(records[:501])]
    requested = identifiers + [identifiers[0], "paper_ffffffffffff"]
    with mock.patch.object(paper_search, "resource_index_papers", side_effect=AssertionError("all rows")), \
         mock.patch("management_portal.paper_store.PaperStore.manifest_paths", side_effect=AssertionError("scan")):
        rows = paper_search.resource_index_papers_by_ids(requested, actor="alice")
        assert [r["id"] for r in rows] == [i for i in identifiers if i != records[3]["resource_id"]]
        assert paper_search.resource_index_papers_by_ids([], actor="alice") == []
    assert max(observed) <= 500
    assert sum(observed) == 501


def test_paging_observes_committed_incremental_changes_without_reloading_cache(monkeypatch):
    writer = IndexStore()
    records = seed(writer)
    reader = IndexStore()
    monkeypatch.setattr("management_portal.resource_index_store.readable_flags",
                        lambda rows, actor: [True] * len(rows))
    target = records[0]["resource_id"]
    writer.apply(ChangeSet(adapter="paper", upserts=({**records[0], "title": "Changed"},)))
    options = {"ids": [target]}
    assert reader.query(resource_types=["paper"], paper_rows=options)["resources"][0]["title"] == "Changed"
    writer.apply(ChangeSet(adapter="paper", deletes=(target,)))
    assert reader.query(resource_types=["paper"], paper_rows=options)["resources"] == []
