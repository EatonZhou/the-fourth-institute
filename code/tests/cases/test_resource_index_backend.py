from __future__ import annotations


from management_portal.society_service import business_fact_views

import json
import os
import queue
import time
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

from tests.cases.registry_support import remove_registry_document

from management_portal import resource_index_backend as backend
from management_portal import serve_management_portal
from management_portal.resources import query as resource_query
from management_portal.resources.collaboration_registry import (
    CollaborationDescriptorStore,
)
from management_portal.resources.errors import ResourceServiceUnavailable
from management_portal.resources.contracts import ResourceQuery


def test_projection_fingerprint_changes_with_knowledge_parser(tmp_path, monkeypatch):
    """知识解析器内容改变时投影指纹随之变化"""
    parser = tmp_path / "knowledge_parser.py"
    parser.write_text("title = folder_name\n")
    monkeypatch.setattr(backend.knowledge_backend, "__file__", str(parser))
    previous = backend._current_projection_code_fingerprint()
    parser.write_text("title = document_heading\n")
    assert backend._current_projection_code_fingerprint() != previous


def test_index_times_are_utc_iso(tmp_path):
    """资源索引时间写 UTC ISO

    索引更新时间、文件修改时间和时间戳换算都输出带 Z 的 UTC ISO，不随服务时区变化。

    :design: decision_86aed2f0e8c7
    """
    source = tmp_path / "source.md"
    source.write_text("body", encoding="utf-8")
    os.utime(source, (0, 86400))
    assert backend._path_modified_at(source) == "1970-01-02T00:00:00Z"
    assert backend._timestamp_iso(86400) == "1970-01-02T00:00:00Z"
    assert backend._now_iso().endswith("Z")


def test_workbench_scan_reads_the_database_without_watching_files():
    """工作台适配器读取数据库且不监听文件"""
    # Workbench apps live in the workbenches database schema; the adapter has no
    # file sources to watch and scanning an empty store yields nothing.
    from management_portal.workbench_projections import store as workbench_store

    workbench_store.Store()
    adapter = backend.WorkbenchAdapter()
    assert adapter.source_paths() == []
    assert adapter.scan() == ([], {"workbench_count": 0})


def test_workbench_scan_reads_committed_apps_without_writing(isolated_portal_case):
    """工作台扫描读取已提交应用且不修改数据库"""
    from management_portal.workbench_projections import store as workbench_store

    with isolated_portal_case():
        source = workbench_store.Store()
        adapter = backend.WorkbenchAdapter()
        app = {"workbench_id": "workbench_111111111111", "title": "已提交的工作台", "bindings": {}}
        before_write = adapter.fingerprint()
        with source.connect() as connection:
            connection.execute("INSERT INTO apps VALUES (%s,'alice','',1,NULL,'now','now','')", (app["workbench_id"],))
            connection.execute("INSERT INTO versions VALUES (%s,1,%s,'','','','now')", (app["workbench_id"], app["title"]))
            connection.commit()
        before_read = adapter.fingerprint()
        assert before_write != before_read
        records, state = adapter.scan()
        assert state["workbench_count"] == 1
        assert records[0]["title"] == app["title"]
        assert adapter.fingerprint() == before_read


def test_workspace_index_follows_typed_lifecycle_without_registry_revision(
    isolated_portal_case, monkeypatch,
):
    """Workspace 索引随创建迁移删除变化且不依赖登记版本"""
    from management_portal.resources.workspace_store import get_workspace_store

    with isolated_portal_case() as root:
        registry = backend.get_registry_store()
        revisions = registry.revisions

        def current_revisions(collections):
            assert "workspace" not in collections
            return revisions(collections)

        monkeypatch.setattr(registry, "revisions", current_revisions)
        adapter = backend.CollaborationRegistryAdapter()
        workspace = get_workspace_store()
        wid = "workspace_123456789abc"
        before = adapter.fingerprint()
        workspace.ensure(wid, str(root / "before"))
        created = adapter.fingerprint()
        assert created != before
        records, _ = adapter.scan()
        assert [(item["resource_id"], item["attributes"]["path"]) for item in records] == [
            (wid, str(root / "before")),
        ]
        workspace.ensure(wid, str(root / "after"), move=True)
        moved = adapter.fingerprint()
        assert moved != created
        records, _ = adapter.scan()
        assert records[0]["attributes"]["path"] == str(root / "after")
        with workspace.database.transaction() as connection:
            workspace.mark_deleted(wid, deleted_at="2026-10-10T00:00:00Z",
                                   deleted_by="test", connection=connection)
        assert adapter.fingerprint() != moved
        records, _ = adapter.scan()
        assert records[0]["attributes"]["status"] == "deleted"
        assert registry.get("workspace", wid) is None


def _record(
    resource_id: str,
    resource_type: str,
    *,
    adapter: str = "test",
    title: str = "Resource",
    facets: tuple[str, ...] = (),
    attributes: dict | None = None,
    terms: tuple[str, ...] = (),
) -> dict:
    return backend.resource_record(
        resource_id=resource_id,
        resource_type=resource_type,
        title=title,
        summary="Summary",
        adapter=adapter,
        path=f"/tmp/{resource_id}.json",
        modified_at="2026-07-22T12:00:00+08:00",
        facets=facets,
        attributes=attributes or {},
        search_terms=terms,
        url=f"/{resource_id}",
    )


def test_get_evaluates_resource_read_access_once(monkeypatch, isolated_portal_case):
    """获取资源时只评估一次读取权限"""
    with isolated_portal_case():
        store = backend.IndexStore()
        resource_id = "skill_0123456789ab"
        store.publish_snapshot("test", [_record(resource_id, "skill")], {})
        original = backend.resource_is_readable
        actors = []

        def tracked(record, actor, **kwargs):
            actors.append(actor)
            return original(record, actor, **kwargs)

        monkeypatch.setattr(backend, "resource_is_readable", tracked)
        assert store.get(resource_id, actor="alice")["capabilities"]["read"]
        assert actors == ["alice"]
        with pytest.raises(KeyError):
            store.get(resource_id, actor="")
        assert actors == ["alice", ""]


class FakeAdapter:
    def __init__(self, name: str, records: list[dict], fingerprint: str = "one") -> None:
        self.name = name
        self.resource_types = frozenset({name})
        self.records = records
        self.current_fingerprint = fingerprint
        self.scan_count = 0
        self.error: Exception | None = None

    def fingerprint(self) -> str:
        return self.current_fingerprint

    def scan(self):
        self.scan_count += 1
        if self.error:
            raise self.error
        return list(self.records), {"marker": self.current_fingerprint}

    def owns_path(self, path: Path) -> bool:
        return path.suffix == ".fake"


def test_unrelated_domain_projections_preserve_readonly_bodies_and_detach_query_results():
    """无关领域投影保留只读正文且查询结果可独立修改"""
    from management_portal.society_service.business_fact_views import project_resources as lineage

    class ReadonlyBody(dict):
        def __deepcopy__(self, memo):
            raise AssertionError("Unrelated domain must not traverse the paper body")

    paper = _record("paper_0123456789ab", "paper", facets=("paper",))
    paper["attributes"] = ReadonlyBody({"abstract": "long body", "tags": ["retained"]})
    records = lineage([paper])
    result = resource_query.query_records(records, ResourceQuery(actor="CASE-A", include_attributes=True))
    result["resources"][0]["attributes"]["tags"].append("client edit")
    assert paper["attributes"]["tags"] == ["retained"]


def test_record_has_one_identity_and_registered_project_exception_contracts(subtests, isolated_portal_case):
    """索引拒绝无效资源身份并原子发布有效快照"""
    with subtests.test(contract='test_record_has_one_identity_and_registered_project_exception'), isolated_portal_case() as case_path:
        skill = _record('skill_3b1feeb182a3', 'skill', title='TFI unified ID', terms=('SKILL.md',))
        assert skill['resource_id'] == 'skill_3b1feeb182a3'
        assert skill['search_terms'] == ['skill_3b1feeb182a3', 'TFI unified ID', 'SKILL.md']
        assert 'aliases' not in skill
        project = _record('DRAM Stacking', 'project')
        assert project['resource_id'] == 'DRAM Stacking'
        user = _record('sample_admin', 'user')
        assert user['resource_id'] == 'sample_admin'
        store = backend.IndexStore()
        result = store.publish_snapshot('test', [_record('paper_0123456789ab', 'skill'), _record('unknown_0123456789ab', 'unknown'), skill], {})
        assert result['upserted'] == 1
        assert result['resource_error_count'] == 2
        assert store.internal_get(skill['resource_id'])['title'] == skill['title']
    with subtests.test(contract='test_public_index_reads_project_current_urls_without_rewriting_cached_records'), isolated_portal_case() as case_path:
        record = _record('paper_0123456789ab', 'paper', adapter='paper')
        record['url'] = '/obsolete-paper-page'
        store = backend.IndexStore()
        store.publish_snapshot('paper', [record], {})
        assert store.get(record['resource_id'], actor='test-actor')['url'] == '/paper?paper=paper_0123456789ab'
        assert store.query(actor='test-actor')['resources'][0]['url'] == '/paper?paper=paper_0123456789ab'
        assert store.internal_get(record['resource_id'])['url'] == '/obsolete-paper-page'
    with subtests.test(contract='test_snapshot_resource_error_preserves_only_the_failed_previous_record'), isolated_portal_case():
        healthy = _record('paper_0123456789ab', 'paper', adapter='paper', title='Old healthy')
        broken = _record('paper_abcdef012345', 'paper', adapter='paper', title='Last good broken')
        store = backend.IndexStore()
        store.publish_snapshot('paper', [healthy, broken], {})
        updated = _record(healthy['resource_id'], 'paper', adapter='paper', title='New healthy')
        result = store.publish_snapshot('paper', [updated], {'resource_errors': [{'path': broken['source']['path'], 'error': 'invalid manifest'}]})
        assert result['resource_error_count'] == 1
        assert store.internal_get(healthy['resource_id'])['title'] == 'New healthy'
        assert store.internal_get(broken['resource_id'])['title'] == 'Last good broken'
        error = store.adapter_source_state('paper')['resource_errors'][0]
        assert error['resource_id'] == broken['resource_id']
    with subtests.test(contract='test_store_applies_adapter_snapshots_atomically_and_removes_deleted_resources'), isolated_portal_case():
        store = backend.IndexStore()
        first = _record('skill_0123456789ab', 'skill', adapter='skill')
        second = _record('skill_fedcba987654', 'skill', adapter='skill')
        result = store.publish_snapshot('skill', [first, second], {'fingerprint': 'a'})
        assert result['upserted'] == 2
        assert result['upserted_ids'] == [first['resource_id'], second['resource_id']]
        assert result['deleted_ids'] == []
        assert store.status()['resource_count'] == 2
        updated = _record('skill_0123456789ab', 'skill', adapter='skill', title='Updated')
        result = store.publish_snapshot('skill', [updated], {'fingerprint': 'b'})
        assert result['upserted'] == 1
        assert result['deleted'] == 1
        assert result['upserted_ids'] == [updated['resource_id']]
        assert result['deleted_ids'] == [second['resource_id']]
        changes = store.status()['adapters']['skill']['last_changes']
        assert changes == {'added': [], 'deleted': ['skill_fedcba987654'], 'added_count': 0, 'updated_count': 1, 'deleted_count': 1}
        assert store.internal_get('skill_0123456789ab')['title'] == 'Updated'
        with pytest.raises(KeyError):
            store.internal_get('skill_fedcba987654')
    with subtests.test(contract='test_cross_adapter_collision_isolated_to_the_bad_resource'), isolated_portal_case():
        store = backend.IndexStore()
        original = _record('skill_0123456789ab', 'skill', adapter='skill')
        store.publish_snapshot('skill', [original], {})
        collision = _record('skill_0123456789ab', 'skill', adapter='other', title='Wrong')
        result = store.publish_snapshot('other', [collision], {})
        assert result['resource_error_count'] == 1
        assert store.internal_get('skill_0123456789ab')['title'] == 'Resource'
        assert store.status()['resource_count'] == 1
    with subtests.test(contract='test_query_uses_facets_filters_search_terms_and_authenticated_read_access'), isolated_portal_case():
        store = backend.IndexStore()
        public = _record('skill_0123456789ab', 'skill', facets=('knowledge',), title='GPU servers', attributes={'project_ids': ['RobotCompany', 'AlignMe'], 'status': 'active', 'year': 2026, 'tags': ['GPU', 'runtime']}, terms=('A100',))
        private = _record('resident_0123456789ab', 'resident', facets=('collaboration',), title='Private branch')
        store.publish_snapshot('test', [public, private], {})
        result = store.query(q='A100', facets=['knowledge'], projects=['RobotCompany'], actor='test-actor')
        assert [item['resource_id'] for item in result['resources']] == [public['resource_id']]
        assert store.query(projects=['AlignMe'], actor='test-actor')['total'] == 1
        assert store.query(attribute_filters={'year': ['2026']}, actor='test-actor')['total'] == 1
        assert store.query(attribute_filters={'tags': ['runtime']}, actor='test-actor')['total'] == 1
        assert store.query(attribute_filters={'tags': ['missing']}, actor='test-actor')['total'] == 0
        assert 'attributes' not in result['resources'][0]
        # Open mode grants supported resource reads to every authenticated
        # user; only an anonymous request remains ineligible.
        assert store.query(q=private['resource_id'], actor='bob')['total'] == 1
        assert store.query(q=private['resource_id'], actor='')['total'] == 0
        assert store.query(q=private['resource_id'], actor='alice')['total'] == 1


def test_store_get_supports_plain_project_ids_contracts(tmp_path, subtests, isolated_portal_case):
    """索引支持普通项目标识与派生 Resident 地址"""
    with subtests.test(contract='test_store_get_supports_plain_project_ids'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = backend.IndexStore()
        project = _record('2HourResearch', 'project', adapter='project')
        store.publish_snapshot('project', [project], {})
        result = store.get('2HourResearch', actor='test-actor')
        assert result['resource_id'] == '2HourResearch'
        assert result['resource_type'] == 'project'
    with subtests.test(contract='test_collaboration_adapter_indexes_formal_derived_resident_url'), isolated_portal_case() as case_path:
        tmp_path = case_path
        registry_root = tmp_path / 'resources'
        store = CollaborationDescriptorStore()
        descriptor = store.put({'schema': 'tfi.collaboration-descriptor.v2', 'resource_id': 'resident_fedcba987654', 'title': 'Derived resident', 'summary': '', 'status': 'active', 'project_ids': [], 'relations': [{'kind': 'root', 'target_id': 'resident_0123456789ab'}], 'attributes': {}, 'created_at': '', 'updated_at': '', 'deleted_at': ''})
        adapter = backend.CollaborationRegistryAdapter()
        adapter.registry_root = registry_root
        record = adapter._index_record(descriptor, store)
        assert record['url'] == '/lineage?resident=resident_fedcba987654'
    with subtests.test(contract='test_file_change_detector_watches_nested_directories'), isolated_portal_case() as case_path:
        tmp_path = case_path
        nested = tmp_path / 'one' / 'two'
        nested.mkdir(parents=True)
        changes: queue.Queue[str] = queue.Queue()
        detector = backend.RecursiveFileChangeWatcher([tmp_path], changes.put, lambda: None)
        detector.start()
        try:
            deadline = time.monotonic() + 2
            while not detector.status()['ready'] and time.monotonic() < deadline:
                time.sleep(0.01)
            if not detector.status()['ready']:
                pytest.skip('inotify is unavailable')
            target = nested / 'changed.txt'
            target.write_text('changed', encoding='utf-8')
            observed = changes.get(timeout=2)
            assert observed == str(target.resolve())
        finally:
            detector.stop()
    with subtests.test(contract='test_file_change_detector_can_watch_only_explicit_directories'), isolated_portal_case() as case_path:
        tmp_path = case_path
        nested = tmp_path / 'nested'
        nested.mkdir()
        changes: queue.Queue[str] = queue.Queue()
        detector = backend.RecursiveFileChangeWatcher([tmp_path], changes.put, lambda: None, recursive=False)
        detector.start()
        try:
            deadline = time.monotonic() + 2
            while not detector.status()['ready'] and time.monotonic() < deadline:
                time.sleep(0.01)
            if not detector.status()['ready']:
                pytest.skip('inotify is unavailable')
            assert detector.status()['recursive'] is False
            assert detector.status()['watch_count'] == 1
            created = tmp_path / 'created'
            created.mkdir()
            assert changes.get(timeout=2) == str(created.resolve())
            time.sleep(0.05)
            assert detector.status()['watch_count'] == 1
            target = tmp_path / 'changed.txt'
            target.write_text('changed', encoding='utf-8')
            assert changes.get(timeout=2) == str(target.resolve())
        finally:
            detector.stop()
    with subtests.test(contract='test_file_change_detector_tracks_new_subdirectory_trees_without_recursive_startup'), isolated_portal_case() as case_path:
        tmp_path = case_path
        changes: queue.Queue[str] = queue.Queue()
        detector = backend.RecursiveFileChangeWatcher([tmp_path], changes.put, lambda: None, recursive=False, track_new_directories=True)
        detector.start()
        try:
            deadline = time.monotonic() + 2
            while not detector.status()['ready'] and time.monotonic() < deadline:
                time.sleep(0.01)
            if not detector.status()['ready']:
                pytest.skip('inotify is unavailable')
            created = tmp_path / 'created'
            created.mkdir()
            assert changes.get(timeout=2) == str(created.resolve())
            deadline = time.monotonic() + 2
            while detector.status()['watch_count'] < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert detector.status()['watch_count'] >= 2
            target = created / 'changed.txt'
            target.write_text('changed', encoding='utf-8')
            assert changes.get(timeout=2) == str(target.resolve())
        finally:
            detector.stop()
    with subtests.test(contract='test_explicit_watch_directories_retain_nested_roots'), isolated_portal_case() as case_path:
        tmp_path = case_path
        parent = tmp_path / 'root'
        child = parent / 'one' / 'two'
        sibling = tmp_path / 'sibling'
        child.mkdir(parents=True)
        sibling.mkdir()
        assert backend._watch_directories([child, parent, child.parent, sibling]) == sorted([parent.resolve(), child.parent.resolve(), child.resolve(), sibling.resolve()], key=str)


def test_query_exposes_all_private_resources_to_resident_contracts(subtests, isolated_portal_case):
    """Resident 可查询私有资源且删除项默认隐藏"""
    with subtests.test(contract='test_query_exposes_all_private_resources_to_resident'), isolated_portal_case() as case_path:
        store = backend.IndexStore()
        private_information = _record('information_0123456789ab', 'information', facets=('knowledge',))
        private_lineage = _record('lineage_0123456789ab', 'lineage', facets=('collaboration',))
        store.publish_snapshot('test', [private_information, private_lineage], {})
        result = store.query(actor='resident_abcdefabcdef')
        assert [item['resource_id'] for item in result['resources']] == [private_information['resource_id'], private_lineage['resource_id']]
    with subtests.test(contract='test_resource_query_excludes_deleted_records_unless_explicitly_requested'), isolated_portal_case() as case_path:
        active = _record('resident_0123456789ab', 'resident', attributes={'status': 'active'})
        deleted = _record('resident_fedcba987654', 'resident', attributes={'status': 'deleted'})
        store = backend.IndexStore()
        store.publish_snapshot('test', [active, deleted], {})
        default_result = store.query(resource_types=['resident'], actor='test-actor')
        deleted_result = store.query(resource_types=['resident'], statuses=['deleted'], actor='test-actor')
        assert [item['resource_id'] for item in default_result['resources']] == [active['resource_id']]
        assert [item['resource_id'] for item in deleted_result['resources']] == [deleted['resource_id']]
    with subtests.test(contract='test_adapter_uses_fingerprint_and_preserves_last_good_snapshot_on_failure'), isolated_portal_case():
        record = _record('paper_0123456789ab', 'paper', adapter='paper')
        adapter = FakeAdapter('paper', [record])
        store = backend.IndexStore()
        layer = backend.AdapterLayer(store, [adapter], start_worker=False)
        first = layer.refresh_now('paper')
        assert first['changed'] is True
        assert first['scanned'] is True
        assert adapter.scan_count == 1
        second = layer.refresh_now('paper')
        assert second['changed'] is False
        assert adapter.scan_count == 1
        adapter.current_fingerprint = 'two'
        adapter.error = RuntimeError('broken source')
        with pytest.raises(RuntimeError, match='broken source'):
            layer.refresh_now('paper')
        assert store.internal_get(record['resource_id'])['title'] == record['title']
        assert store.status()['adapters']['paper']['last_error'] == 'broken source'
    with subtests.test(contract='test_wait_reports_failed_target_refresh_as_unsuccessful'), isolated_portal_case():
        adapter = FakeAdapter('paper', [])
        adapter.error = RuntimeError('broken source')
        store = backend.IndexStore()
        with mock.patch.object(backend, 'WATCH_FILESYSTEM', False):
            layer = backend.AdapterLayer(store, [adapter])
        try:
            assert layer.wait(['paper'], timeout=1) is False
            assert store.status()['adapters']['paper']['last_error'] == 'broken source'
        finally:
            layer.stop()
    with subtests.test(contract='test_adapter_worker_refreshes_only_when_work_is_queued'), isolated_portal_case():
        record = _record('paper_0123456789ab', 'paper', adapter='paper')
        adapter = FakeAdapter('paper', [record])
        store = backend.IndexStore()
        with mock.patch.object(backend, 'WATCH_FILESYSTEM', False):
            layer = backend.AdapterLayer(store, [adapter])
        try:
            assert layer.wait(timeout=1)
            assert adapter.scan_count == 1
            time.sleep(0.1)
            assert adapter.scan_count == 1
            adapter.current_fingerprint = 'two'
            layer.request_refresh(['paper'])
            assert layer.wait(timeout=1)
            assert adapter.scan_count == 2
        finally:
            layer.stop()
    with subtests.test(contract='test_low_frequency_reconcile_recovers_a_missed_source_change'), isolated_portal_case():
        record = _record('paper_0123456789ab', 'paper', adapter='paper')
        adapter = FakeAdapter('paper', [record])
        store = backend.IndexStore()
        with mock.patch.object(backend, 'WATCH_FILESYSTEM', False):
            layer = backend.AdapterLayer(store, [adapter], reconcile_seconds=0.05)
        try:
            assert layer.wait(timeout=1)
            assert adapter.scan_count == 1
            adapter.current_fingerprint = 'missed-change'
            deadline = time.monotonic() + 1
            while adapter.scan_count < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert adapter.scan_count == 2
        finally:
            layer.stop()


def test_query_filters_candidates_before_permission_projection(monkeypatch) -> None:
    """资源查询先缩小候选再执行描述符与权限投影"""
    store = backend.IndexStore()
    records = [
        _record(f"skill_{index:012x}", "skill", title=f"Skill {index}")
        for index in range(20)
    ]
    residents = [
        _record(
            f"resident_{index:012x}",
            "resident",
            title=f"Target Resident {index}",
            facets=("collaboration",),
        )
        for index in range(2)
    ]
    store.publish_snapshot("test", [*records, *residents], {})
    projected_ids: list[str] = []
    descriptor_projected_ids: list[str] = []
    projection_calls = 0
    original = store._filter_private_records

    def project(records):
        nonlocal projection_calls
        projection_calls += 1
        items = list(records)
        descriptor_projected_ids.extend(item["resource_id"] for item in items)
        return items

    def capture(candidates, actor):
        rows = list(candidates)
        projected_ids.extend(item["resource_id"] for item in rows)
        return original(rows, actor)

    monkeypatch.setattr(store, "_filter_private_records", capture)

    result = store.query(
        actor="alice",
        q="Target",
        resource_types=["resident"],
        facets=["collaboration"],
        records_projector=project,
    )

    assert result["total"] == 2
    assert projection_calls == 1
    assert descriptor_projected_ids == [item["resource_id"] for item in residents]
    assert projected_ids == [item["resource_id"] for item in residents]


def test_type_filtered_query_tracks_snapshot_changes_without_scanning_other_types() -> None:
    """按类型查询随快照删除更新且不扫描其他类型"""
    store = backend.IndexStore()
    skill = _record("skill_111111111111", "skill")
    paper = _record("paper_222222222222", "paper")
    store.publish_snapshot("test", [skill, paper], {})
    assert list(store._type_ids["skill"]) == [skill["resource_id"]]
    assert [item["resource_id"] for item in store.query(actor="alice", resource_types=["skill"])["resources"]] == [skill["resource_id"]]
    store.publish_snapshot("test", [paper], {})
    assert store.query(actor="alice", resource_types=["skill"])["total"] == 0
    assert store._type_ids["skill"] == {}
    assert store.query(actor="alice")["total"] == 1


def test_static_type_query_skips_unneeded_dynamic_projections(monkeypatch) -> None:
    """静态类型查询跳过无关动态事实与描述符投影"""
    store = backend.IndexStore()
    skill = _record("skill_333333333333", "skill")
    store.publish_snapshot("test", [skill], {})
    monkeypatch.setattr(store, "_project_business_facts", lambda records: pytest.fail("dynamic facts"))

    def unexpected_descriptor(records):
        pytest.fail("collaboration descriptor")

    result = store.query(
        actor="alice", resource_types=["skill"],
        records_projector=unexpected_descriptor,
        records_projector_types=frozenset({"resident", "lineage"}),
    )
    assert result["total"] == 1
    assert result["resources"][0]["resource_id"] == skill["resource_id"]
    assert "capabilities" not in store._records[skill["resource_id"]]


def test_query_applies_dynamic_filters_after_descriptor_projection() -> None:
    """资源查询在描述符投影后按当前项目和状态过滤"""
    store = backend.IndexStore()
    residents = [
        _record(
            f"resident_{index:012x}",
            "resident",
            title=f"Target Resident {index}",
            facets=("collaboration",),
        )
        for index in range(2)
    ]
    store.publish_snapshot("test", residents, {})

    def project(records):
        return [
            {
                **record,
                "attributes": {
                    **record.get("attributes", {}),
                    "project_ids": ["Current"] if record["resource_id"] == residents[0]["resource_id"] else ["Other"],
                    "status": "active" if record["resource_id"] == residents[0]["resource_id"] else "deleted",
                },
            }
            for record in records
        ]

    result = store.query(
        actor="alice",
        q="Target",
        resource_types=["resident"],
        facets=["collaboration"],
        projects=["Current"],
        statuses=["active"],
        records_projector=project,
    )

    assert [item["resource_id"] for item in result["resources"]] == [
        residents[0]["resource_id"]
    ]


def test_query_reuses_one_access_policy_for_all_candidates(monkeypatch) -> None:
    """一次查询的所有候选复用同一读取策略实例"""
    records = [
        _record(f"skill_{index:012x}", "skill", title=f"Skill {index}")
        for index in range(3)
    ]
    constructed: list[object] = []
    checked: list[str] = []

    class CountingPolicy:
        def __init__(self):
            constructed.append(self)

        def can_read(self, record, *, actor=""):
            checked.append(record["resource_id"])
            return True

    monkeypatch.setattr(resource_query, "ResourceAccessPolicy", CountingPolicy)

    result = resource_query.query_records(
        records,
        ResourceQuery.from_kwargs({"actor": "alice", "q": "Skill"}),
    )

    assert len(constructed) == 1
    assert checked == [item["resource_id"] for item in records]
    assert result["total"] == 3


def test_collaboration_query_cold_loads_only_requested_descriptors(tmp_path, monkeypatch) -> None:
    """协作查询冷加载仅读取所请求描述符并按变更刷新"""
    writer = CollaborationDescriptorStore()
    identifiers = [f"resident_{index:012x}" for index in range(5)]
    for resource_id in identifiers:
        writer.put({
            "schema": "tfi.collaboration-descriptor.v2",
            "resource_id": resource_id,
            "title": resource_id,
            "summary": "",
            "status": "active",
            "project_ids": [],
            "relations": [],
            "attributes": {"marker": resource_id},
            "created_at": "",
            "updated_at": "",
            "deleted_at": "",
        })

    cold = CollaborationDescriptorStore()
    reads: list[str] = []
    get_many = cold._registry.get_many

    def tracked_read(collection, document_ids):
        document_ids = list(document_ids)
        reads.extend(document_ids)
        return get_many(collection, document_ids)

    monkeypatch.setattr(cold._registry, "get_many", tracked_read)
    monkeypatch.setattr(cold._registry, "snapshot", lambda *_: pytest.fail("cold reads load only requested descriptors"))
    records = [{"resource_id": resource_id, "resource_type": "resident"} for resource_id in identifiers]
    projected = cold.attributes_for_records(records[:2])

    assert set(projected) == set(identifiers[:2])
    assert reads == identifiers[:2]
    projected[identifiers[0]]["marker"] = "caller edit"
    repeated = cold.attributes_for_records(records[:2])
    assert repeated[identifiers[0]]["marker"] == identifiers[0]
    assert reads == identifiers[:2]

    updated = writer.get(identifiers[1])
    updated["attributes"]["marker"] = "updated"
    writer.put(updated)
    cold.attributes_for_records(records[:1])
    assert reads == identifiers[:2]
    refreshed = cold.attributes_for_records(records[1:2])

    assert refreshed[identifiers[1]]["marker"] == "updated"
    assert reads[-1] == identifiers[1]


def test_collaboration_adapter_lets_authenticated_actors_read(
    tmp_path: Path,
) -> None:
    """协作资源索引允许已认证主体读取且拒绝匿名查询"""
    from management_portal.resources.workspace_store import get_workspace_store

    get_workspace_store().ensure(
        "workspace_0123456789ab", str(tmp_path / "workspace"), title="Private workspace",
    )
    records = {
        "resident": [],
        "lineage": [
            {
                "resource_id": "lineage_0123456789ab",
                "title": "Private lineage",
            }
        ],
    }
    descriptors = CollaborationDescriptorStore()
    for items in records.values():
        for item in items:
            descriptors.put({
                "schema": "tfi.collaboration-descriptor.v2",
                **item,
                "summary": "",
                "status": "active",
                "project_ids": [],
                "relations": [],
                "attributes": {},
                "created_at": "",
                "updated_at": "",
                "deleted_at": "",
            })
    adapter = backend.CollaborationRegistryAdapter()
    indexed, _state = adapter.scan()
    store = backend.IndexStore()
    store.publish_snapshot("collaboration", indexed, {})

    assert store.query(actor="bob", facets=["collaboration"])["total"] == 2
    assert store.query(actor="", facets=["collaboration"])["total"] == 0
    assert store.query(actor="alice", facets=["collaboration"])["total"] == 2


def test_incremental_retry_merges_each_path_resource_error() -> None:
    """增量批量扫描失败后逐路径重试并合并各项错误"""
    paths = ("/tmp/first.fake", "/tmp/second.fake")

    def scan(changed_paths, _previous, _state):
        if len(changed_paths) > 1:
            raise RuntimeError("batch failed")
        path = changed_paths[0]
        return backend.ChangeSet(
            adapter="paper",
            source_state={
                "resource_errors": [
                    {"path": path, "error": f"broken {Path(path).stem}"}
                ]
            },
        )

    changes = backend.AdapterLayer._retry_incremental_paths(
        "paper",
        scan,
        paths,
        [],
        {},
    )

    assert changes is not None
    assert [item["path"] for item in changes.source_state["resource_errors"]] == list(paths)


def test_existing_projection_does_not_scan_during_service_startup_contracts(subtests, isolated_portal_case):
    """已有投影启动时跳过扫描且恢复停机期间变更"""
    with subtests.test(contract='test_existing_projection_does_not_scan_during_service_startup'), isolated_portal_case() as case_path:
        record = _record('paper_0123456789ab', 'paper', adapter='paper')
        adapter = FakeAdapter('paper', [record], fingerprint='existing')
        store = backend.IndexStore()
        store.publish_snapshot('paper', [record], {'fingerprint': 'existing'})
        with mock.patch.object(backend, 'WATCH_FILESYSTEM', False):
            layer = backend.AdapterLayer(store, [adapter], reconcile_seconds=60)
        try:
            time.sleep(0.1)
            assert adapter.scan_count == 0
        finally:
            layer.stop()
    with subtests.test(contract='test_service_startup_recovers_changes_made_while_it_was_stopped'), isolated_portal_case() as case_path:
        record = _record('paper_0123456789ab', 'paper', adapter='paper')
        adapter = FakeAdapter('paper', [record], fingerprint='changed')
        store = backend.IndexStore()
        store.publish_snapshot('paper', [record], {'fingerprint': 'existing'})
        with mock.patch.object(backend, 'WATCH_FILESYSTEM', False):
            layer = backend.AdapterLayer(store, [adapter], reconcile_seconds=60)
        try:
            assert layer.wait(timeout=1)
            assert adapter.scan_count == 1
            assert store.adapter_source_state('paper')['fingerprint'] == 'changed'
        finally:
            layer.stop()
    with subtests.test(contract='test_query_rejects_a_projection_that_has_never_published'), isolated_portal_case():
        adapter = FakeAdapter('paper', [])
        adapter.error = RuntimeError('broken source')
        store = backend.IndexStore()
        with mock.patch.object(backend, 'WATCH_FILESYSTEM', False):
            runtime = backend.ResourceIndexRuntime(store=store, adapters=[adapter], adapter_layer_factory=backend.AdapterLayer)
        try:
            with pytest.raises(ResourceServiceUnavailable, match='resource projection unavailable: paper'):
                runtime.query(resource_types=['paper'])
            failed_scan_count = adapter.scan_count
            adapter.error = None
            assert runtime.query(resource_types=['paper'])['resources'] == []
            assert adapter.scan_count == failed_scan_count + 1
            assert store.adapter_has_snapshot('paper') is True
        finally:
            runtime.adapter_layer.stop()
    with subtests.test(contract='test_unready_adapter_does_not_block_unrelated_reads'), isolated_portal_case():
        skill_id = 'skill_0123456789ab'
        paper = FakeAdapter('paper', [])
        paper.error = RuntimeError('broken source')
        skill = FakeAdapter('skill', [_record(skill_id, 'skill', adapter='skill')])
        project = FakeAdapter('project', [])
        store = backend.IndexStore()
        with mock.patch.object(backend, 'WATCH_FILESYSTEM', False):
            runtime = backend.ResourceIndexRuntime(store=store, adapters=[paper, skill, project], adapter_layer_factory=backend.AdapterLayer)
        try:
            assert runtime.status()['resource_types'] == ['paper', 'project', 'skill']
            result = runtime.query(resource_types=['skill'], actor='test-actor')
            assert [item['resource_id'] for item in result['resources']] == [skill_id]
            suggestions = runtime.suggest(q=skill_id, resource_type='skill', actor='test-actor')
            assert [item['resource_id'] for item in suggestions['resources']] == [skill_id]
            assert runtime.get_resource(skill_id, actor='test-actor')['resource_id'] == skill_id
            with pytest.raises(KeyError):
                runtime.get_resource('skill_abcdef012345', resource_types=['skill'])
            with pytest.raises(KeyError):
                runtime.get_resource('Missing Project', resource_types=['project', 'user'])
            with pytest.raises(ResourceServiceUnavailable, match='paper'):
                runtime.get_resource('paper_0123456789ab', resource_types=['paper'])
            with pytest.raises(ResourceServiceUnavailable, match='paper'):
                runtime.query()
        finally:
            runtime.adapter_layer.stop()
    with subtests.test(contract='test_projection_notification_follows_a_changed_or_forced_refresh'), isolated_portal_case():
        record = _record('paper_0123456789ab', 'paper', adapter='paper')
        adapter = FakeAdapter('paper', [record])
        observed: list[tuple[str, dict]] = []
        layer = backend.AdapterLayer(backend.IndexStore(), [adapter], start_worker=False, on_projection_changed=lambda name, result: observed.append((name, result)))
        layer.refresh_now('paper')
        layer.refresh_now('paper')
        layer.refresh_now('paper', force=True)
        assert [name for name, _result in observed] == ['paper', 'paper']
        assert observed[0][1]['changed'] is True
        assert observed[0][1]['upserted_ids'] == [record['resource_id']]
        assert observed[1][1]['changed'] is False
    with subtests.test(contract='test_resource_get_rejects_nested_address'), isolated_portal_case():
        server = ThreadingHTTPServer(('127.0.0.1', 0), _QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            with mock.patch.object(serve_management_portal, '_check_session', return_value=True):
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(f'{base}/api/resource-index/resources/repo_0123456789ab/file/README.md')
                # The route takes one resource ID segment; a nested address is no resource route.
                assert error.value.code == 404
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_watch_tree_stops_at_the_declared_depth_contracts(tmp_path, subtests, isolated_portal_case):
    """文件监听遵循深度限制且协作描述符按键增量刷新"""
    with subtests.test(contract='test_watch_tree_stops_at_the_declared_depth'), isolated_portal_case() as case_path:
        tmp_path = case_path
        first = tmp_path / 'first'
        second = first / 'second'
        third = second / 'third'
        third.mkdir(parents=True)
        assert backend._watch_tree(tmp_path, max_depth=2) == [tmp_path.resolve(), first.resolve(), second.resolve()]
    with subtests.test(contract='test_adapter_watch_directories_do_not_enumerate_source_paths'), isolated_portal_case() as case_path:
        tmp_path = case_path
        root = tmp_path / 'facts'
        root.mkdir()

        class RootOnlyAdapter(backend._PathsAdapter):
            roots = (root,)

            def source_paths(self) -> list[Path]:
                raise AssertionError('watch setup must not enumerate source paths')
        assert RootOnlyAdapter().watch_directories() == [root.resolve()]
    with subtests.test(contract='test_collaboration_adapter_watches_no_files'), isolated_portal_case() as case_path:
        adapter = backend.CollaborationRegistryAdapter()
        assert adapter.watch_directories() == []
        assert not adapter.owns_path(case_path / 'residents' / 'resident_0123456789ab.yaml')
        assert not adapter.owns_path(case_path / 'repos' / 'repo_0123456789ab.yaml')
    with subtests.test(contract='test_collaboration_adapter_scans_changed_descriptors_incrementally'), isolated_portal_case() as case_path:
        resource_id = 'resident_0123456789ab'
        payload = {'schema': 'tfi.collaboration-descriptor.v2', 'resource_id': resource_id, 'title': 'Before', 'summary': '', 'status': 'active', 'project_ids': [], 'relations': [], 'attributes': {}, 'created_at': '', 'updated_at': '2026-08-04T00:00:00Z', 'deleted_at': ''}
        descriptors = CollaborationDescriptorStore()
        descriptors.put(payload)
        adapter = backend.CollaborationRegistryAdapter()
        previous, state = adapter.scan()
        assert [item['title'] for item in previous] == ['Before']
        fingerprint = adapter.fingerprint()
        descriptors.put({**payload, 'title': 'After'})
        assert adapter.fingerprint() != fingerprint
        key = descriptors.change_key(resource_id)
        changed = adapter.scan_changes([key, '/share/Projects/unrelated.md'], previous, state)
        assert [item['title'] for item in changed.upserts] == ['After']
        assert changed.deletes == ()
        assert changed.source_state['last_incremental_path_count'] == 1
        remove_registry_document('resident', resource_id)
        removed = adapter.scan_changes([key], previous, state)
        assert removed.upserts == ()
        assert removed.deletes == (resource_id,)
    with subtests.test(contract='test_project_skill_article_uses_its_kb_id_as_the_resource_address'), isolated_portal_case() as case_path:
        tmp_path = case_path
        article = tmp_path / 'knowledge_base' / 'skill' / 'article.md'
        article.parent.mkdir(parents=True)
        article.write_text('---\nkb_id: skill_abcdef123456\ntype: skill\nproject: Example\n---\n\n# Skill notes\n', encoding='utf-8')
        raw = backend.skill_backend._get_item_cached(root=article, primary_path=article, files=[article], project='Example', source='project', source_label='Project')
        record = backend.SkillPackageAdapter()._record_for_item(raw, statuses={'Example': 'active'}, order=0)
        assert raw['skill_id'] == 'skill_abcdef123456'
        assert record['resource_id'] == 'skill_abcdef123456'
        assert record['attributes']['single_file'] is True
        assert record['attributes']['read_scope'] == 'project'
    with subtests.test(contract='test_global_skill_sources_are_project_independent_for_reading'), isolated_portal_case() as case_path:
        tmp_path = case_path
        package = tmp_path / 'shared-skill'
        primary = package / 'SKILL.md'
        package.mkdir()
        primary.write_text('---\nname: shared-skill\nid: skill_0123456789ab\n---\n\n# Shared skill\n', encoding='utf-8')
        raw = backend.skill_backend._get_item_cached(root=package, primary_path=primary, files=[primary], project='Shared Skills', source='shared', source_label='Shared')
        record = backend.SkillPackageAdapter()._record_for_item(raw, statuses={}, order=0)
        assert record['attributes']['project_ids'] == []
        assert record['attributes']['read_scope'] == 'global'


def test_existing_source_parse_failure_cannot_delete_last_good_record(tmp_path: Path) -> None:
    """源文件仍在但解析失败时保留最后有效索引记录"""
    source = tmp_path / "manifest.json"
    source.write_text("{}", encoding="utf-8")
    record = backend.resource_record(
        resource_id="paper_0123456789ab",
        resource_type="paper",
        title="Paper",
        adapter="protected",
        path=source,
    )

    class ProtectedAdapter(backend._PathsAdapter):
        name = "protected"
        protect_existing_sources = True

        def __init__(self) -> None:
            self.records = [record]
            self.marker = "one"

        def fingerprint(self) -> str:
            return self.marker

        def scan(self):
            return list(self.records), {}

    adapter = ProtectedAdapter()
    store = backend.IndexStore()
    layer = backend.AdapterLayer(store, [adapter], start_worker=False)
    layer.refresh_now("protected")

    adapter.records = []
    adapter.marker = "two"
    result = layer.refresh_now("protected")
    assert result["resource_error_count"] == 1
    assert store.internal_get(record["resource_id"])["title"] == "Paper"

    source.unlink()
    adapter.marker = "three"
    result = layer.refresh_now("protected")
    assert result["deleted"] == 1
    with pytest.raises(KeyError):
        store.internal_get(record["resource_id"])


def test_knowledge_adapter_updates_one_existing_item_incrementally(tmp_path: Path) -> None:
    """知识适配器按变更文件增量更新或删除单个条目"""
    project_root = tmp_path / "Example"
    tag_root = project_root / "knowledge_base" / "thinking"
    tag_root.mkdir(parents=True)
    resource_id = "thinking_0123456789ab"
    primary = tag_root / f"20260722__thinking__Example__{resource_id}.md"

    def write_item(title: str) -> None:
        primary.write_text(
            "\n".join(
                [
                    "---",
                    f"kb_id: {resource_id}",
                    "type: thinking",
                    "project: Example",
                    "created_at: 2026-07-22",
                    "importance: 5",
                    "---",
                    "",
                    f"# {title}",
                    "",
                    "Body",
                ]
            ),
            encoding="utf-8",
        )

    write_item("Original")
    allowed, _folders, reverse, config_hash = backend.knowledge_backend._get_tag_config()
    raw = backend.knowledge_backend._get_md_item_cached(
        primary,
        tag_root,
        [primary],
        "Example",
        config_hash,
        set(allowed),
        reverse,
    )
    adapter = backend.ProjectKnowledgeAdapter()
    original = adapter._record_for_item(
        raw,
        statuses={"Example": "active"},
        order=0,
    )
    assert original is not None

    write_item("Updated")
    project = {"id": "Example", "status": "active", "path": str(project_root)}
    with mock.patch.object(
        backend.knowledge_backend,
        "_parse_projects_yaml",
        return_value=[project],
    ):
        changes = adapter.scan_changes([str(primary)], [original], {})
        assert changes is not None
        assert [record["title"] for record in changes.upserts] == ["Updated"]
        assert changes.deletes == ()

        primary.unlink()
        changes = adapter.scan_changes([str(primary)], [original], {})
        assert changes is not None
        assert changes.upserts == ()
        assert changes.deletes == (resource_id,)


class _QuietHandler(serve_management_portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_resource_index_http_query_get_refresh_and_status_routes() -> None:
    """资源索引 HTTP 路由传递查询、刷新和状态参数"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    query_payload = {"schema": backend.SCHEMA, "resources": [], "total": 0}
    resource_payload = _record("paper_0123456789ab", "paper")
    resource_payload.pop("search_terms")
    resource_service = mock.Mock()
    resource_service.query.return_value = query_payload
    resource_service.suggest.return_value = {"resources": [resource_payload]}
    resource_service.get.return_value = resource_payload
    resource_service.refresh.return_value = {"queued": ["paper"], "finished": False}
    resource_service.status.return_value = {"schema": backend.SCHEMA, "resource_count": 1, "resource_types": ["paper", "resident"]}
    try:
        with (
            mock.patch.object(serve_management_portal, "_check_session", return_value=True),
            mock.patch.object(serve_management_portal, "_get_session_username", return_value="alice"),
            mock.patch.object(
                serve_management_portal,
                "get_resource_service",
                return_value=resource_service,
            ),
        ):
            with urllib.request.urlopen(
                f"{base}/api/resource-index/query?q=robot&facet=paper&attribute.year=2026&limit=7"
            ) as response:
                assert json.load(response) == query_payload
            resource_service.query.assert_called_once()
            assert resource_service.query.call_args.kwargs["actor"] == "alice"
            assert resource_service.query.call_args.kwargs["q"] == "robot"
            assert resource_service.query.call_args.kwargs["facets"] == ["paper"]
            assert resource_service.query.call_args.kwargs["attribute_filters"] == {"year": ["2026"]}
            assert resource_service.query.call_args.kwargs["limit"] == 7

            with urllib.request.urlopen(
                f"{base}/api/resource-index/suggest?q=robot&limit=7&resource_type=paper"
            ) as response:
                assert json.load(response) == {"resources": [resource_payload]}
            resource_service.suggest.assert_called_once_with(actor="alice", q="robot", limit=7, resource_type="paper")

            with urllib.request.urlopen(
                f"{base}/api/resource-index/resources/paper_0123456789ab"
            ) as response:
                assert json.load(response)["resource_id"] == "paper_0123456789ab"
            resource_service.get.assert_called_once_with("paper_0123456789ab", actor="alice")

            request = urllib.request.Request(
                f"{base}/api/resource-index/refresh",
                data=json.dumps({"adapters": ["paper"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                assert response.status == 202
            resource_service.refresh.assert_called_once()
            assert resource_service.refresh.call_args.kwargs["adapters"] == ["paper"]

            with urllib.request.urlopen(f"{base}/api/resource-index/status") as response:
                status = json.load(response)
                assert status["resource_count"] == 1
                assert status["resource_types"] == ["paper", "resident"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reconcile_recovers_a_missed_change_while_other_adapters_stay_busy(isolated_portal_case):
    """其他适配器持续忙碌时定期校准仍恢复遗漏变更"""
    with isolated_portal_case():
        busy = FakeAdapter('paper', [_record('paper_0123456789ab', 'paper', adapter='paper')])
        missed = FakeAdapter('skill', [_record('skill_0123456789ab', 'skill', adapter='skill')])
        store = backend.IndexStore()
        with mock.patch.object(backend, 'WATCH_FILESYSTEM', False):
            layer = backend.AdapterLayer(store, [busy, missed], reconcile_seconds=0.05)
        stop = threading.Event()

        def keep_busy():
            while not stop.is_set():
                layer.request_refresh(['paper'], force=True)
                time.sleep(0.005)

        feeder = threading.Thread(target=keep_busy, daemon=True)
        try:
            assert layer.wait(timeout=1)
            feeder.start()
            missed.current_fingerprint = 'missed-change'
            deadline = time.monotonic() + 2
            while missed.scan_count < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert missed.scan_count == 2
        finally:
            stop.set()
            feeder.join(timeout=1)
            layer.stop()


def test_a_slow_or_long_queued_index_refresh_is_reported_with_its_adapter(isolated_portal_case, monkeypatch):
    """索引刷新缓慢或排队过久时报告对应适配器与耗时"""
    with isolated_portal_case():
        reports = []
        monkeypatch.setattr(backend, 'SLOW_REFRESH_SECONDS', 0.0)
        monkeypatch.setattr(backend.timing, 'report_slow', lambda label, seconds, threshold=0.2, **details: reports.append((label, details)) if seconds >= threshold else None)
        logged = mock.Mock()
        monkeypatch.setattr(backend.LOGGER, 'exception', logged)
        adapter = FakeAdapter('paper', [])
        adapter.error = RuntimeError('broken source')
        store = backend.IndexStore()
        with mock.patch.object(backend, 'WATCH_FILESYSTEM', False):
            layer = backend.AdapterLayer(store, [adapter], reconcile_seconds=60)
        try:
            layer.wait(['paper'], timeout=1)
            assert logged.call_args.args == ('resource index refresh failed: %s', 'paper')
            label, details = reports[0]
            assert label == 'resource index refresh'
            assert details['adapter'] == 'paper'
            assert {'waited', 'ran', 'paths', 'queued'} <= set(details)
        finally:
            layer.stop()


def test_projection_only_adapter_reports_content_changes() -> None:
    """仅有投影签名变化时刷新仍报告内容变更"""
    class ProjectionAdapter:
        name = "projection"
        resource_types = frozenset()
        value = "one"

        def fingerprint(self) -> str:
            return self.value

        def scan(self):
            return [], {"projection_signature": self.value}

        def owns_path(self, _path: Path) -> bool:
            return False

    adapter = ProjectionAdapter()
    store = backend.IndexStore()
    layer = backend.AdapterLayer(
        store,
        [adapter],
        start_worker=False,
    )

    first = layer.refresh_now("projection")
    adapter.value = "two"
    second = layer.refresh_now("projection")

    assert first["changed"] is True
    assert second["changed"] is True


def test_todo_knowledge_uses_frontmatter_identity_state_and_incremental_files(tmp_path, monkeypatch):
    """TODO 知识按元数据识别身份状态并增量更新文件"""
    todo_id = "todo_0123456789ab"
    root = tmp_path / "Example" / "knowledge_base" / "todo"
    root.mkdir(parents=True)
    path = root / f"20261011__todo__Task__{todo_id}.md"
    body = "\n**Task**: Preserve the original body.\n"
    document = (
        f"---\nid: {todo_id}\ntype: todo\nproject: Example\nstatus: open\n"
        "task_status: finished\nassignees: [alice]\ntask_kind: research\n"
        "finished_at: '2026-10-11T10:00:00Z'\n---\n" + body
    )
    path.write_text(document)
    monkeypatch.setattr(backend.knowledge_backend, "_parse_projects_yaml", lambda: [
        {"id": "Example", "status": "active", "path": str(tmp_path / "Example")},
    ])
    adapter = backend.ProjectKnowledgeAdapter()
    changes = adapter.scan_changes([str(path)], [], {})
    assert len(changes.upserts) == 1
    record = changes.upserts[0]
    assert record["resource_id"] == todo_id
    assert "knowledge" in record["facets"]
    assert record["url"] == f"/resource/knowledge?item={todo_id}"
    assert record["attributes"]["assignees"] == ["alice"]
    assert record["attributes"]["status"] == "finished"
    assert record["attributes"]["task_kind"] == "research"
    assert "board" not in record["attributes"]
    path.write_text(document.replace("task_status: finished", "task_status: active"))
    changes = adapter.scan_changes([str(path)], [record], {})
    assert changes.upserts[0]["attributes"]["status"] == "active"
    assert path.read_text().endswith(body)
    # A filename never supplies the missing formal id.
    path.write_text(document.replace(f"id: {todo_id}\n", ""))
    assert not backend.knowledge_backend._read_kb_file(
        str(path), "Example", {"todo"}, {}, entry_root=root, entry_files=[path],
    )
    path.unlink()
    changes = adapter.scan_changes([str(path)], [record], {})
    assert changes.deletes == (todo_id,)


def test_resource_index_persists_only_changed_rows():
    """资源索引持久化仅更新发生变化的记录行"""
    from management_portal.resource_index_backend import IndexStore, ChangeSet, resource_record
    from tests.cases.database_test_support import connect
    store = IndexStore()
    first = resource_record(resource_id='paper_111111111111', resource_type='paper', title='First', adapter='paper')
    second = resource_record(resource_id='paper_222222222222', resource_type='paper', title='Second', adapter='paper')
    store.apply(ChangeSet(adapter='paper', upserts=(first, second)))
    with connect(store) as connection:
        original_version = connection.execute(
            'SELECT xmin::text FROM resource_index_records WHERE resource_id = %s',
            (second['resource_id'],),
        ).fetchone()[0]
    changed_first = {**first, 'title': 'Updated'}
    store.apply(ChangeSet(adapter='paper', upserts=(changed_first,)))
    with connect(store) as connection:
        untouched_version = connection.execute(
            'SELECT xmin::text FROM resource_index_records WHERE resource_id = %s',
            (second['resource_id'],),
        ).fetchone()[0]
        title = connection.execute("SELECT record_json->>'title' FROM resource_index_records WHERE resource_id = %s", (first['resource_id'],)).fetchone()[0]
    assert untouched_version == original_version
    assert title == 'Updated'
