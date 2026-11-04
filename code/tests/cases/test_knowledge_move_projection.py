"""Knowledge moves publish one live batch and fence older source observations."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest

from management_portal import resource_index_knowledge as knowledge_index
from management_portal.resource_index_refresh import AdapterLayer
from management_portal.resource_index_store import IndexStore
from management_portal.resources.locking import mutation_projection_scope
from tests.cases.test_resource_index_backend import _record


def test_move_batch_is_live_durable_and_fences_an_older_scan(isolated_portal_case, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    ids = [f"concept_{i:012x}" for i in range(17)]
    old = [_record(rid, "concept", adapter="project_knowledge", title="Before") for rid in ids]
    new = [_record(rid, "concept", adapter="project_knowledge", title="After",
                   attributes={"project_ids": ["Target"]}) for rid in ids]

    class Adapter:
        name = "project_knowledge"
        resource_types = {"concept"}
        def fingerprint(self):
            return "old"
        def scan(self):
            entered.set()
            assert release.wait(5)
            return old, {}
        def project_records(self, records):
            assert [r["resource_id"] for r in records] == ids
            return new

    with isolated_portal_case():
        store = IndexStore()
        layer = AdapterLayer(store, [Adapter()], start_worker=False)
        applied, persisted, emitted = Mock(wraps=store.apply), Mock(wraps=store._persist), Mock()
        monkeypatch.setattr(store, "apply", applied)
        monkeypatch.setattr(store, "_persist", persisted)
        monkeypatch.setattr(layer, "_completed_refresh", emitted)
        try:
            with ThreadPoolExecutor(1) as pool:
                scanning = pool.submit(layer.refresh_now, "project_knowledge", force=True)
                assert entered.wait(3)
                try:
                    result = layer.project_relocated([
                        {"resource_id": rid, "resource_type": "concept", "path": f"/Target/{rid}.md"}
                        for rid in ids])
                    assert set(result["upserted_ids"]) == set(ids)
                    assert applied.call_count == persisted.call_count == emitted.call_count == 1
                    assert applied.call_args.kwargs["direct"] is True
                    for rid in ids:
                        assert store.internal_get(rid)["title"] == "After"
                finally:
                    release.set()
                scanning.result()
            reopened = IndexStore()
            for rid in ids:
                assert store.internal_get(rid)["title"] == "After"
                assert reopened.internal_get(rid)["title"] == "After"
        finally:
            release.set()
            layer.stop()


def test_move_projection_and_later_edit_share_the_mutation_boundary(isolated_portal_case):
    rid = "concept_0123456789ab"
    entered, release, started = threading.Event(), threading.Event(), threading.Event()
    current = {"title": "A"}

    class Adapter:
        name = "project_knowledge"
        resource_types = {"concept"}
        def project_records(self, records):
            title = current["title"]
            if title == "A":
                entered.set()
                assert release.wait(5)
            return [_record(rid, "concept", adapter=self.name, title=title)]

    item = {"resource_id": rid, "resource_type": "concept"}
    with isolated_portal_case():
        store = IndexStore()
        layer = AdapterLayer(store, [Adapter()], start_worker=False)
        def edit_b():
            started.set()
            with mutation_projection_scope(rid):
                current["title"] = "B"
                layer.project_relocated([item])
        try:
            with ThreadPoolExecutor(2) as pool:
                first = pool.submit(layer.project_relocated, [item])
                assert entered.wait(3)
                second = pool.submit(edit_b)
                assert started.wait(3)
                try:
                    assert current["title"] == "A"
                finally:
                    release.set()
                first.result()
                second.result()
            assert store.internal_get(rid)["title"] == "B"
            assert IndexStore().internal_get(rid)["title"] == "B"
        finally:
            release.set()
            layer.stop()


def test_real_knowledge_batch_reads_new_paths_and_loads_project_list_once(tmp_path, monkeypatch):
    root = tmp_path / "Target" / "knowledge_base" / "concept"
    root.mkdir(parents=True)
    records = []
    for i in range(17):
        rid = f"concept_{i:012x}"
        path = root / f"20261103__concept__item__{rid}.md"
        path.write_text(f"---\nkb_id: {rid}\ntype: concept\nproject: Target\nimportance: 5\n---\n\n# Item {i}\n\nBody\n")
        records.append({"resource_id": rid, "resource_type": "concept", "path": str(path), "project": "Target"})
    projects = Mock(return_value=[{"id": "Target", "path": str(root.parents[1]), "status": "active"}])
    monkeypatch.setattr(knowledge_index.knowledge_source, "_parse_projects_yaml", projects)
    result = knowledge_index.ProjectKnowledgeAdapter().project_records(records)
    assert projects.call_count == 1
    assert {row["resource_id"] for row in result} == {row["resource_id"] for row in records}
    assert all(Path(row["source"]["path"]).parent == root for row in result)
    assert all(row["attributes"]["project_ids"] == ["Target"] for row in result)


def test_projection_rejects_identity_loss_before_publishing(isolated_portal_case):
    rid = "concept_0123456789ab"
    class Adapter:
        name = "project_knowledge"
        resource_types = {"concept"}
        def project_records(self, records):
            return []
    with isolated_portal_case():
        store = IndexStore()
        layer = AdapterLayer(store, [Adapter()], start_worker=False)
        try:
            with pytest.raises(ValueError, match="identities"):
                layer.project_relocated([{"resource_id": rid, "resource_type": "concept"}])
            with pytest.raises(KeyError):
                store.internal_get(rid)
        finally:
            layer.stop()


def test_mixed_move_is_atomic_durable_and_fences_both_scans(isolated_portal_case, monkeypatch):
    from management_portal.resource_index_records import ChangeSet
    ids = ['concept_0123456789ab', 'skill_0123456789ab']
    class Adapter:
        def __init__(self, name, kind, rid):
            self.name, self.resource_types, self.rid = name, {kind}, rid
        def project_records(self, records):
            return [_record(self.rid, next(iter(self.resource_types)), adapter=self.name, title='Moved')]
    adapters = [Adapter('project_knowledge', 'concept', ids[0]), Adapter('skill', 'skill', ids[1])]
    with isolated_portal_case():
        store = IndexStore()
        layer = AdapterLayer(store, adapters, start_worker=False)
        saved = Mock(wraps=store._persist)
        monkeypatch.setattr(store, '_persist', saved)
        before = store.generation()
        try:
            result = layer.project_relocated([
                {'resource_id': rid, 'resource_type': rid.split('_')[0]} for rid in ids])
            assert result['upserted_ids'] == sorted(ids)
            assert saved.call_count == 1
            for adapter in adapters:
                store.apply(ChangeSet(adapter.name, upserts=(
                    _record(adapter.rid, next(iter(adapter.resource_types)), adapter=adapter.name, title='Stale'),)),
                    scan_started_generation=before)
            reopened = IndexStore()
            assert all(reopened.internal_get(rid)['title'] == 'Moved' for rid in ids)
            # No partially published first adapter when the second is invalid.
            with pytest.raises(ValueError, match='invalid'):
                store.apply_batch([
                    ChangeSet('project_knowledge', upserts=(_record(ids[0], 'concept', adapter='project_knowledge', title='Partial'),)),
                    ChangeSet('skill', upserts=({'resource_id': ids[1]},)),
                ], direct=True)
            assert store.internal_get(ids[0])['title'] == 'Moved'
        finally:
            layer.stop()


@pytest.mark.parametrize('single_file', [False, True])
def test_skill_batch_reads_registered_new_path(tmp_path, monkeypatch, single_file):
    root = tmp_path / 'Target' / 'knowledge_base' / 'skill'
    root.mkdir(parents=True)
    rid = 'skill_0123456789ab'
    package = root if single_file else root / 'package'
    package.mkdir(exist_ok=True)
    path = package / (rid + '.md' if single_file else 'SKILL.md')
    path.write_text(f'---\nname: Fixture\ndescription: Test relocation\nmetadata:\n  id: {rid}\n---\n# Fixture\nBody\n')
    projects = Mock(return_value=[{'id': 'Target', 'path': str(root.parents[1]), 'status': 'active'}])
    monkeypatch.setattr(knowledge_index.skill_backend, '_parse_projects', projects)
    result, = knowledge_index.SkillPackageAdapter().project_records([{
        'resource_id': rid, 'resource_type': 'skill', 'path': str(path),
        'content_root': str(package), 'single_file': single_file, 'project': 'Target',
    }])
    assert result['resource_id'] == rid
    assert result['source']['path'] == str(path)
    assert result['attributes']['single_file'] is single_file
    assert result['attributes']['project_ids'] == ['Target']
    assert projects.call_count == 1
