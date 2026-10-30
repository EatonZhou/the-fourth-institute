from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from management_portal import search_identity_backend as backend
from management_portal import semantic_search_store
from management_portal.resources import get_resource_service
from tests.cases.registry_support import put_registry_document


PAPER = semantic_search_store.PAPER_SEARCHES
KNOWLEDGE = semantic_search_store.KNOWLEDGE_SEARCHES
BOARD = semantic_search_store.BOARD_SEARCHES


def _write(target: Path | semantic_search_store.SearchStore, payload: object) -> None:
    if isinstance(target, semantic_search_store.SearchStore):
        with target.locked():
            target.write(payload)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _paths(tmp_path: Path) -> tuple[semantic_search_store.SearchStore, ...]:
    """The paper, knowledge and board job lists."""
    return PAPER, KNOWLEDGE, BOARD


def test_wall_search_identity_resolves_to_its_project_and_search_contracts(tmp_path, subtests, isolated_portal_case):
    """WALL 检索 ID 解析到所在 Project 的检索页，检索记录拒绝身份别名和跨页面 ID 冲突"""
    with subtests.test(contract='test_wall_search_identity_resolves_to_its_project_and_search'), isolated_portal_case() as case_path:
        tmp_path = case_path
        paper_store, knowledge_store, board_store = _paths(tmp_path)
        _write(board_store, [{'search_id': 'search_123456789abc', 'surface': 'wall', 'project': 'demo project', 'query': '职责分工', 'created_by': 'alice'}])
        report = backend.scan_search_ids()
        assert report['invalid_count'] == 0
        resolved = backend.resolve_search_address('search_123456789abc', actor='alice')
        assert resolved['portal_url'] == '/wall?project=demo%20project&semantic=search_123456789abc'
        assert backend.resolve_search_address('search_123456789abc', actor='bob')['exists'] is True
    with subtests.test(contract='test_scan_rejects_identity_alias_and_cross_surface_collision'), isolated_portal_case() as case_path:
        tmp_path = case_path
        paper_store, knowledge_store, board_store = _paths(tmp_path)
        with pytest.raises(ValueError, match='Unknown search fields: resource_id'):
            _write(paper_store, [{'search_id': 'search_0123456789ab', 'resource_id': 'search_0123456789ab'}])
        _write(paper_store, [{'search_id': 'search_0123456789ab'}])
        with pytest.raises(ValueError, match='another surface'):
            _write(knowledge_store, [{'search_id': 'search_0123456789ab'}])
        scan = backend.scan_search_ids()
        assert scan['collision_count'] == 0
    with subtests.test(contract='test_resource_scan_isolates_invalid_search_and_broken_store'), isolated_portal_case() as case_path:
        tmp_path = case_path
        paper_store, knowledge_store, board_store = _paths(tmp_path)
        valid_id = 'search_0123456789ab'
        invalid_id = 'search_notcanonical'
        _write(paper_store, [{'search_id': valid_id, 'query': 'valid', 'created_by': 'alice'}])
        _write(knowledge_store, [{'search_id': invalid_id, 'query': 'invalid', 'created_by': 'bob'}])
        with mock.patch.object(board_store, 'load', side_effect=ValueError('broken store')):
            records, errors = backend.scan_search_resources()
        assert [item['search_id'] for item in records] == [valid_id]
        assert {item.get('resource_id') for item in errors} >= {invalid_id}
        assert any((item['path'] == f'semantic_search:{BOARD.surface}' for item in errors))
    with subtests.test(contract='test_paper_and_knowledge_create_canonical_search_ids'), isolated_portal_case() as case_path:
        tmp_path = case_path
        from management_portal import semantic_search_jobs
        with mock.patch.object(backend, 'generate_search_id', side_effect=['search_111111111111', 'search_222222222222']), mock.patch.object(semantic_search_jobs, 'start_task', side_effect=lambda job: job):
            paper = semantic_search_jobs.start_search('paper', {'query': 'paper query'}, 'alice')
            knowledge = semantic_search_jobs.start_search('knowledge', {'query': 'knowledge query'}, 'alice')
        assert paper['search_id'] == 'search_111111111111'
        assert 'resource_id' not in paper
        assert paper['surface'] == 'paper'
        assert knowledge['search_id'] == 'search_222222222222'
        assert 'resource_id' not in knowledge
        assert knowledge['surface'] == 'knowledge'


def test_scan_requires_canonical_ids_and_enriches_surface_metadata(tmp_path: Path) -> None:
    """检索 ID 扫描只接受规范 ID，并在应用时补全页面和检索类型字段"""
    paper_store, knowledge_store, board_store = _paths(tmp_path)
    _write(
        paper_store,
        [
            {
                "search_id": "search_0123456789ab",
                "query": "robot acceleration",
                "created_by": "alice",
            },
            {
                "search_id": "search_aabbccddeeff",
                "query": "smoke",
                "created_by": "alice",
                "deleted": True,
            },
        ],
    )
    _write(
        knowledge_store,
        [
            {
                "search_id": "search_fedcba987654",
                "query": "lineages",
                "created_by": "bob",
            }
        ],
    )

    dry_run = backend.scan_search_ids()
    assert dry_run["search_count"] == 3
    assert dry_run["changed_count"] == 3
    assert dry_run["invalid_count"] == 0
    assert dry_run["collision_count"] == 0

    applied = backend.scan_search_ids(
        apply=True,
    )
    assert applied["changed_count"] == 3
    paper_jobs = PAPER.load()
    knowledge_jobs = KNOWLEDGE.load()
    assert paper_jobs[0]["search_id"] == "search_0123456789ab"
    assert "resource_id" not in paper_jobs[0]
    assert paper_jobs[0]["search_kind"] == "semantic"
    assert paper_jobs[0]["surface"] == "paper"
    assert get_resource_service().is_valid_id(paper_jobs[1]["search_id"], "search")
    assert knowledge_jobs[0]["search_id"] == "search_fedcba987654"

    second = backend.scan_search_ids()
    assert second["changed_count"] == 0
    assert second["unchanged_count"] == 3


def test_resolver_resolves_any_search_and_keeps_deleted_tombstones(tmp_path: Path) -> None:
    """检索解析不按创建者限制（读权限由权限系统判断），已删除的检索以墓碑形式返回，不存在的检索报缺失"""
    paper_store, knowledge_store, board_store = _paths(tmp_path)
    _write(
        paper_store,
        [
            {
                "search_id": "search_0123456789ab",
                "query": "robot acceleration",
                "created_by": "alice",
                "status": "done",
                "result_count": 4,
            }
        ],
    )
    _write(
        knowledge_store,
        [
            {
                "search_id": "search_fedcba987654",
                "query": "old query",
                "created_by": "alice",
                "deleted": True,
                "status": "deleted",
            }
        ],
    )
    backend.scan_search_ids(
        apply=True,
    )

    resolved = backend.resolve_search_address(
        "search_0123456789ab",
        actor="alice",
    )
    assert resolved["resource_type"] == "search"
    assert resolved["record"]["surface"] == "paper"
    assert resolved["portal_url"] == "/paper?semantic=search_0123456789ab"
    assert resolved["exists"] is True

    deleted = backend.resolve_search_address(
        "search_fedcba987654",
        actor="alice",
    )
    assert deleted["record"]["status"] == "deleted"
    assert deleted["exists"] is False

    assert backend.resolve_search_address("search_0123456789ab", actor="bob")["exists"] is True
    with pytest.raises(KeyError):
        backend.resolve_search_address("search_999999999999", actor="alice")


def test_search_records_name_their_creator_without_an_owner_list(tmp_path: Path) -> None:
    """检索资源记录只写创建者，不带 owner 名单；任何身份读到的都是同一份列表"""
    paper_store, knowledge_store, board_store = _paths(tmp_path)
    _write(
        paper_store,
        [{
            "search_id": "search_0123456789ab",
            "query": "shared search",
            "created_by": "alice",
            "status": "done",
        }],
    )
    _write(knowledge_store, [])
    _write(board_store, [])

    [record] = backend.load_search_resources()
    assert record["id"] == "search_0123456789ab"
    assert record["created_by"] == "alice"
    assert "owner_ids" not in record
    assert not hasattr(backend, "update_search_owners")


def test_board_surfaces_share_search_ids_and_resolve(tmp_path: Path) -> None:
    """WALL 页面的多条检索共用检索 ID 体系，并各自解析到所在 Project 的页面"""
    paper_store, knowledge_store, board_store = _paths(tmp_path)
    _write(paper_store, [])
    _write(knowledge_store, [])
    _write(
        board_store,
        [
            {
                "search_id": "search_111111111111",
                "surface": "wall",
                "project": "portal",
                "query": "blocked work",
                "created_by": "alice",
                "status": "done",
            },
            {
                "search_id": "search_222222222222",
                "surface": "wall",
                "project": "portal",
                "query": "stale review",
                "created_by": "alice",
                "status": "done",
            },
        ],
    )

    scan = backend.scan_search_ids()
    assert scan["surface_counts"] == {"wall": 2}
    assert scan["invalid_count"] == 0
    assert scan["collision_count"] == 0

    resources = backend.load_search_resources()
    assert {(item["id"], item["surface"]) for item in resources} == {
        ("search_111111111111", "wall"),
        ("search_222222222222", "wall"),
    }
    todo = backend.resolve_search_address(
        "search_111111111111",
        actor="alice",
    )
    review = backend.resolve_search_address(
        "search_222222222222",
        actor="alice",
    )
    assert todo["portal_url"] == "/wall?project=portal&semantic=search_111111111111"
    assert review["portal_url"] == "/wall?project=portal&semantic=search_222222222222"


def test_resource_index_search_record_uses_one_canonical_id_contracts(subtests, isolated_portal_case):
    """检索在资源索引中只用一个规范 ID，记录创建者，由根解析器分发"""
    with subtests.test(contract='test_resource_index_search_record_uses_one_canonical_id'), isolated_portal_case() as case_path:
        from management_portal import resource_index_backend
        record = {'id': 'search_0123456789ab', 'search_id': 'search_0123456789ab', 'query': 'robot acceleration', 'surface': 'paper', 'project': 'RobotWorker', 'search_kind': 'semantic', 'status': 'done', 'result_count': 4, 'created_by': 'alice'}
        with mock.patch.object(resource_index_backend.search_identity_backend, 'scan_search_resources', return_value=([record], [])) as load:
            entries, _state = resource_index_backend.SavedSearchAdapter().scan()
        load.assert_called_once_with()
        assert entries[0]['resource_type'] == 'search'
        assert entries[0]['resource_id'] == 'search_0123456789ab'
        assert entries[0]['url'] == '/paper?semantic=search_0123456789ab'
        assert entries[0]['attributes']['created_by'] == 'alice'
        assert entries[0]['attributes']['project_ids'] == ['RobotWorker']
        resource_index_backend._validate_record(entries[0], 'saved_search')
        assert 'aliases' not in entries[0]
        assert entries[0]['summary'] == 'Paper semantic search · 4 results'
        assert entries[0]['search_terms'] == ['search_0123456789ab', 'robot acceleration', 'paper', 'semantic', 'done']
    with subtests.test(contract='test_root_resolver_dispatches_search_addresses'), isolated_portal_case() as case_path:
        expected = {'root_id': 'search_0123456789ab', 'resource_type': 'search', 'record': {'id': 'search_0123456789ab'}}
        with mock.patch.object(backend, 'resolve_search_address', return_value=expected) as resolve:
            result = get_resource_service().resolve('search_0123456789ab', actor='sample_admin')
        resolve.assert_called_once_with('search_0123456789ab', actor='sample_admin')
        assert result['root_id'] == expected['root_id']
        assert result['resource_type'] == expected['resource_type']
        assert result['schema'] == 'tfi.resource_resolution.v2'
        assert result['address'] == 'search_0123456789ab'
        assert result['record'] == {'resource_id': 'search_0123456789ab'}


def test_knowledge_semantic_search_jobs_are_scoped_to_resource_categories() -> None:
    """知识、Skill 和仓库的语义检索任务各自按资源类别隔离列出和刷新"""
    from management_portal import semantic_search_jobs

    with (
        mock.patch.object(
            backend,
            "generate_search_id",
            side_effect=[
                "search_111111111111",
                "search_222222222222",
                "search_333333333333",
            ],
        ),
        mock.patch.object(semantic_search_jobs, "start_task", side_effect=lambda job: job),
    ):
        knowledge = semantic_search_jobs.start_search("knowledge", {"query": "knowledge query"}, "alice")
        skill = semantic_search_jobs.start_search("skill", {"query": "skill query"}, "alice")
        repo = semantic_search_jobs.start_search("repo", {"query": "repo query"}, "alice")

        assert semantic_search_jobs.list_searches("knowledge", "alice")["searches"] == [knowledge]
        assert semantic_search_jobs.list_searches("skill", "alice")["searches"] == [skill]
        assert semantic_search_jobs.list_searches("repo", "alice")["searches"] == [repo]
        with pytest.raises(KeyError):
            semantic_search_jobs.refresh_search("skill", {"search_id": repo["search_id"]}, "alice")

    assert knowledge["resource_scope"] == "knowledge"
    assert skill["resource_scope"] == "skill"
    assert repo["resource_scope"] == "repo"


def test_paper_collection_jobs_are_listed_to_everyone_without_an_owner_list() -> None:
    """论文收集任务对所有用户列出，任务记录只写创建者，不带 owner 名单"""
    from management_portal import paper_collection_jobs, paper_state_store

    paper_state_store.append("collector_jobs", {
        "job_id": "collector-0123456789ab",
        "direction": "shared collection",
        "created_by": "alice",
        "status": "done",
    })

    [job] = paper_collection_jobs.list_paper_collection_jobs("bob")["jobs"]
    assert job["job_id"] == "collector-0123456789ab"
    assert job["created_by"] == "alice"
    assert "owner_ids" not in job
    assert not hasattr(paper_collection_jobs, "update_paper_collection_owners")


def test_scoped_resource_semantic_result_and_search_resolution(tmp_path: Path) -> None:
    """资源语义检索结果不采纳模型给出的路径，仓库检索解析到仓库资源页"""
    from management_portal import semantic_search_jobs

    results = semantic_search_jobs.coerce_results(
        "repo",
        {
            "results": [
                {
                    "resource_id": "repo_0123456789ab",
                    "score": 94,
                    "reason": "Runtime repository",
                    "evidence_paths": [str(tmp_path / "untrusted-model-path")],
                }
            ]
        },
        {"repo_0123456789ab"},
    )
    # A model-supplied path never becomes part of a result.
    assert results == [
        {
            "resource_id": "repo_0123456789ab",
            "score": 94,
            "reason": "Runtime repository",
        }
    ]

    paper_store, knowledge_store, board_store = _paths(tmp_path)
    _write(paper_store, [])
    _write(
        knowledge_store,
        [
            {
                "search_id": "search_0123456789ab",
                "surface": "repo",
                "query": "runtime",
                "created_by": "alice",
                "status": "done",
            }
        ],
    )
    backend.scan_search_ids(
        apply=True,
    )
    resolved = backend.resolve_search_address(
        "search_0123456789ab",
        actor="alice",
    )
    assert resolved["record"]["resource_scope"] == "repo"
    assert resolved["portal_url"] == "/resource/repo?semantic=search_0123456789ab"
