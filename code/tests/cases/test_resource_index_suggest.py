""":module: spec.design-governance"""
from __future__ import annotations

from management_portal.resource_index_backend import IndexStore
from management_portal.resource_index_records import resource_record


def _record(number: int, *, title: str = "Match", summary: str = "Summary term") -> dict:
    return resource_record(
        resource_id=f"skill_{number:012x}",
        resource_type="skill",
        title=f"{title} {number:02d}",
        summary=summary,
        adapter="skill",
        search_terms=("Extra term", summary),
    )


def test_suggest_matches_only_id_and_title(isolated_portal_case):
    """资源联想只按 ID 和标题匹配，私有 Resident 只对所有者出现"""
    with isolated_portal_case():
        store = IndexStore()
        record = _record(1)
        assert record["search_terms"] == [record["resource_id"], record["title"], "Extra term"]
        store.publish_snapshot("skill", [record], {})

        assert store.suggest(actor="test-actor", q="Summary term")["resources"] == []
        assert store.suggest(actor="test-actor", q="Extra term")["resources"] == []
        legacy = {**record, "search_terms": [*record["search_terms"], "Summary term"]}
        store.publish_snapshot("skill", [legacy], {})
        assert store.query(actor="test-actor", q="Summary term")["resources"] == []
        assert [item["resource_id"] for item in store.suggest(actor="test-actor", q="Match")["resources"]] == [record["resource_id"]]
        assert [item["resource_id"] for item in store.suggest(actor="test-actor", q=record["resource_id"])["resources"]] == [record["resource_id"]]
        private = resource_record(
            resource_id="resident_abcdef012345",
            resource_type="resident",
            title="Private match",
            adapter="collaboration",
            attributes={},
        )
        store.publish_snapshot("collaboration", [private], {})
        assert store.suggest(actor="", q="Private match")["resources"] == []
        assert [item["resource_id"] for item in store.suggest(actor="test-actor", q="Private match")["resources"]] == [private["resource_id"]]
        assert store.suggest(actor="test-actor", q="Match", resource_type="resident")["resources"] == [
            item for item in store.suggest(actor="test-actor", q="Match")["resources"]
            if item["resource_type"] == "resident"
        ]
        assert store.suggest(actor="test-actor", q="Private match", resource_type="paper")["resources"] == []


def test_suggest_projects_and_authorizes_only_needed_candidates(isolated_portal_case, monkeypatch):
    """资源联想只对凑满结果所需的候选做投影和权限检查，并跳过已删除项"""
    with isolated_portal_case():
        store = IndexStore()
        records = [_record(number) for number in range(6)]
        store.publish_snapshot("skill", records, {})
        projected_ids: list[str] = []
        authorized_ids: list[str] = []
        original_filter = store._filter_private_records

        def authorize(batch, actor):
            authorized_ids.extend(record["resource_id"] for record in batch)
            return original_filter(batch, actor)

        monkeypatch.setattr(store, "_filter_private_records", authorize)

        def project(batch):
            result = []
            for record in batch:
                projected_ids.append(record["resource_id"])
                attributes = {**record["attributes"]}
                if record["resource_id"] in {records[0]["resource_id"], records[1]["resource_id"]}:
                    attributes["status"] = "deleted"
                result.append({**record, "attributes": attributes})
            return result

        found = store.suggest(actor="test-actor", q="Match", limit=2, records_projector=project)
        assert [item["resource_id"] for item in found["resources"]] == [records[2]["resource_id"], records[3]["resource_id"]]
        assert projected_ids == [record["resource_id"] for record in records[:4]]
        assert authorized_ids == [record["resource_id"] for record in records[2:4]]
