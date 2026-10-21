from __future__ import annotations

from pathlib import Path
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
import threading
import secrets

import pytest

from management_portal.resources import ResourceService
from management_portal.resources.providers import experiment
from management_portal.resources.registration import ResourceRegistrationStore


@pytest.fixture()
def experiment_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ResourceService, Path]:
    project_root = tmp_path / "OSForAgent"
    project_root.mkdir()
    monkeypatch.setattr(
        experiment,
        "_project_roots",
        lambda: [("OSForAgent", project_root)],
    )
    index = mock.Mock()
    index.change_targets.return_value = ["project_knowledge"]
    index.queue_change.return_value = ["project_knowledge"]
    index.supports_type.return_value = True
    service = ResourceService(index_provider=index)
    experiment.install_mutation_provider(service)
    return service, project_root


@pytest.mark.parametrize("importance", [True, False, 1.7, 5.0, "5", None, [], {}])
def test_experiment_importance_rejects_non_integer_values_without_writing(
    experiment_service: tuple[ResourceService, Path], importance: object,
) -> None:
    """实验重要性拒绝非整数并保留已有文件"""
    service, project_root = experiment_service
    payload = {"project_id": "OSForAgent", "title": "Importance validation"}
    with pytest.raises(ValueError, match="importance must be an integer"):
        service.register("experiment", {**payload, "importance": importance}, actor="resident_aaaaaaaaaaaa")
    assert not list(project_root.glob("knowledge_base/experiment/*/experiment.md"))

    created = service.register("experiment", payload, actor="resident_aaaaaaaaaaaa")
    document = next(project_root.glob("knowledge_base/experiment/*/experiment.md"))
    before = document.read_bytes()
    with pytest.raises(ValueError, match="importance must be an integer"):
        service.update(created["resource_id"], {"importance": importance}, actor="resident_aaaaaaaaaaaa", resource_type="experiment")
    assert document.read_bytes() == before


@pytest.mark.parametrize("importance", [0, 10])
def test_experiment_importance_accepts_integer_boundaries(
    experiment_service: tuple[ResourceService, Path], importance: int,
) -> None:
    """实验重要性接受零和十两个整数边界"""
    service, _project_root = experiment_service
    created = service.register("experiment", {"project_id": "OSForAgent", "title": "Boundary value", "importance": importance}, actor="resident_aaaaaaaaaaaa")
    assert created["importance"] == importance
    updated = service.update(created["resource_id"], {"importance": 10 - importance}, actor="resident_aaaaaaaaaaaa", resource_type="experiment")
    assert updated["importance"] == 10 - importance


@pytest.mark.parametrize("operation", ["update", "unregister"])
def test_experiment_mutations_reject_flat_documents_without_touching_neighbors(
    experiment_service: tuple[ResourceService, Path], operation: str,
) -> None:
    """实验变更拒绝扁平文档并保留相邻文件"""
    service, project_root = experiment_service
    created = service.register("experiment", {"project_id": "OSForAgent", "title": "Flat document"}, actor="resident_aaaaaaaaaaaa")
    document = next(project_root.glob("knowledge_base/experiment/*/experiment.md"))
    original_root = document.parent
    flat_document = original_root.parent / "legacy.md"
    document.rename(flat_document)
    original_root.rmdir()
    sibling = flat_document.parent / "neighbor.txt"
    sibling.write_text("preserve")
    before = flat_document.read_bytes()
    with pytest.raises(ValueError, match="contained experiment directory"):
        if operation == "update":
            experiment.update_experiment(created["resource_id"], {"body": "changed"})
        else:
            experiment.unregister_experiment(created["resource_id"])
    assert flat_document.read_bytes() == before
    assert sibling.read_text() == "preserve"


def test_experiment_update_rejects_symlinked_document_outside_entry(
    experiment_service: tuple[ResourceService, Path], tmp_path: Path,
) -> None:
    """实验更新拒绝指向条目外的文档符号链接"""
    service, project_root = experiment_service
    created = service.register("experiment", {"project_id": "OSForAgent", "title": "Symlink document"}, actor="resident_aaaaaaaaaaaa")
    document = next(project_root.glob("knowledge_base/experiment/*/experiment.md"))
    outside = tmp_path / "outside.md"
    document.rename(outside)
    document.symlink_to(outside)
    before = outside.read_bytes()
    with pytest.raises(ValueError, match="contained experiment directory"):
        experiment.update_experiment(created["resource_id"], {"body": "changed"})
    assert outside.read_bytes() == before


def test_experiment_retirement_uses_project_knowledge_trash(
    experiment_service: tuple[ResourceService, Path],
) -> None:
    """实验退役移入项目知识库回收站"""
    service, project_root = experiment_service
    created = service.register("experiment", {"project_id": "OSForAgent", "title": "Retirement path"}, actor="resident_aaaaaaaaaaaa")
    result = experiment.unregister_experiment(created["resource_id"])
    assert Path(result["trash_path"]).parent == project_root / "knowledge_base" / ".trash"


def test_experiment_index_preserves_registered_title_instead_of_folder_slug(experiment_service):
    """实验索引保留注册标题"""
    from knowledge_board_lan import knowledge_backend

    service, project_root = experiment_service
    created = service.register("experiment", {
        "project_id": "OSForAgent", "title": "Readable experiment title", "slug": "different_path",
    }, actor="resident_aaaaaaaaaaaa")
    document = next(project_root.glob("knowledge_base/experiment/*/experiment.md"))
    indexed = knowledge_backend._read_kb_file(
        str(document), "OSForAgent", {"experiment"}, {},
        entry_root=document.parent, entry_files=[document],
    )
    assert indexed["title"] == created["title"] == "Readable experiment title"


def test_experiment_registration_is_idempotent_and_updatable(
    experiment_service: tuple[ResourceService, Path],
) -> None:
    """实验注册保持幂等且支持更新与退役后重建"""
    service, project_root = experiment_service
    with pytest.raises(ValueError, match="slug must use lowercase"):
        service.register(
            "experiment",
            {
                "project_id": "OSForAgent",
                "title": "Mixed case slug",
                "slug": "Mixed-Case",
            },
            actor="resident_aaaaaaaaaaaa",
        )
    assert not list(
        (project_root / "knowledge_base" / "experiment").glob(
            "*/experiment.md"
        )
    )
    payload = {
        "project_id": "OSForAgent",
        "title": "SmolVLA Thor optimization",
        "slug": "smolvla_thor",
        "importance": 0,
        "summary": "Prepare the baseline and optimization tree.",
    }

    created = service.register("experiment", payload, actor="resident_aaaaaaaaaaaa")
    repeated = service.register("experiment", payload, actor="resident_aaaaaaaaaaaa")
    updated = service.update(
        created["resource_id"],
        {
            "status": "running",
            "importance": 8,
            "body": "Baseline preparation is active.",
        },
        actor="resident_aaaaaaaaaaaa",
        resource_type="experiment",
    )

    assert created["created"] is True
    assert repeated["created"] is False
    assert repeated["resource_id"] == created["resource_id"]
    assert created["importance"] == 0
    assert updated["status"] == "running"
    assert updated["importance"] == 8
    documents = list(
        (project_root / "knowledge_base" / "experiment").glob(
            "*/experiment.md"
        )
    )
    assert len(documents) == 1
    text = documents[0].read_text(encoding="utf-8")
    assert f"kb_id: {created['resource_id']}" in text
    assert "registration_key" not in text
    assert "registration_key" not in created
    assert "registration_key" not in repeated
    assert "status: running" in text
    assert "Baseline preparation is active." in text
    assert service.capabilities("experiment")["registerable"] is True
    assert service.capabilities("experiment")["updatable"] is True
    assert service.capabilities("experiment")["unregisterable"] is True

    repeated_after_update = service.register(
        "experiment",
        payload,
        actor="resident_aaaaaaaaaaaa",
    )
    assert repeated_after_update["resource_id"] == created["resource_id"]
    assert repeated_after_update["created"] is False
    assert len(
        list(
            (project_root / "knowledge_base" / "experiment").glob(
                "*/experiment.md"
            )
        )
    ) == 1

    deleted = service.unregister(
        created["resource_id"],
        actor="resident_aaaaaaaaaaaa",
        resource_type="experiment",
    )
    assert deleted["deleted"] is True
    assert not documents[0].exists()
    assert Path(deleted["trash_path"]).is_dir()

    recreated = service.register("experiment", payload, actor="resident_aaaaaaaaaaaa")
    assert recreated["resource_id"] != created["resource_id"]
    assert recreated["created"] is True


def test_experiment_unregister_retires_registration_without_prior_update(
    experiment_service: tuple[ResourceService, Path],
) -> None:
    """实验未经更新直接注销后可用新标识重建"""
    service, _project_root = experiment_service
    payload = {
        "project_id": "OSForAgent",
        "title": "Delete before update",
        "slug": "delete_before_update",
    }

    created = service.register("experiment", payload, actor="resident_aaaaaaaaaaaa")
    service.unregister(
        created["resource_id"],
        actor="resident_aaaaaaaaaaaa",
        resource_type="experiment",
    )
    recreated = service.register("experiment", payload, actor="resident_aaaaaaaaaaaa")

    assert recreated["resource_id"] != created["resource_id"]


def test_experiment_registration_rejects_external_idempotency_fields(
    experiment_service: tuple[ResourceService, Path],
) -> None:
    """实验注册拒绝外部指定幂等字段"""
    service, _project_root = experiment_service

    with pytest.raises(ValueError, match="internal to ResourceService"):
        service.register(
            "experiment",
            {
                "project_id": "OSForAgent",
                "title": "External implementation detail",
                "registration_key": "caller-owned-key",
            },
        )


def test_experiment_registration_idempotency_survives_service_restart(
    experiment_service: tuple[ResourceService, Path],
) -> None:
    """服务重启后实验注册仍保持幂等"""
    _service, project_root = experiment_service
    index = mock.Mock()
    index.change_targets.return_value = ["project_knowledge"]
    index.queue_change.return_value = ["project_knowledge"]
    index.supports_type.return_value = True
    payload = {
        "project_id": "OSForAgent",
        "title": "Persistent service idempotency",
        "slug": "persistent_service_idempotency",
    }

    first = ResourceService(
        index_provider=index,
        registration_store=ResourceRegistrationStore(),
    )
    experiment.install_mutation_provider(first)
    created = first.register("experiment", payload, actor="resident_aaaaaaaaaaaa")

    restarted = ResourceService(
        index_provider=index,
        registration_store=ResourceRegistrationStore(),
    )
    experiment.install_mutation_provider(restarted)
    repeated = restarted.register("experiment", payload, actor="resident_aaaaaaaaaaaa")

    assert repeated["resource_id"] == created["resource_id"]
    assert repeated["created"] is False


def test_registration_forget_is_idempotent_for_an_unknown_resource() -> None:
    """遗忘未知资源的注册记录可重复执行"""
    store = ResourceRegistrationStore()

    store.forget("repo_0123456789ab")


def test_fresh_experiment_registration_does_not_scan_all_documents(
    experiment_service: tuple[ResourceService, Path],
) -> None:
    """新实验注册不扫描全部文档"""
    service, _root = experiment_service
    with mock.patch.object(experiment, "_by_resource_id", side_effect=AssertionError("full scan")):
        record = service.register(
            "experiment", {"project_id": "OSForAgent", "title": "Fresh entry"},
            actor="resident_aaaaaaaaaaaa",
        )
    assert record["created"] is True


def test_unrelated_registration_reservations_do_not_wait_for_each_other() -> None:
    """不同注册预留互不等待"""
    store = ResourceRegistrationStore()
    store._prepare_schema()
    entered = threading.Event()
    release = threading.Event()
    token = secrets.token_hex(6)

    def slow_id() -> str:
        entered.set()
        assert release.wait(5)
        return "experiment_" + secrets.token_hex(6)

    with ThreadPoolExecutor(max_workers=2) as workers:
        slow = workers.submit(store.reserve, "experiment", {"key": token + "-a"},
                              actor="resident_aaaaaaaaaaaa", allocate=slow_id)
        assert entered.wait(5)
        try:
            fast = workers.submit(store.reserve, "experiment", {"key": token + "-b"},
                                  actor="resident_aaaaaaaaaaaa",
                                  allocate=lambda: "experiment_" + secrets.token_hex(6))
            assert fast.result(timeout=5)[1] is True
        finally:
            release.set()
        assert slow.result(timeout=5)[1] is True


def test_concurrent_retries_share_one_registration_id() -> None:
    """并发重试共享同一个实验注册标识"""
    store = ResourceRegistrationStore()
    token = secrets.token_hex(6)
    with ThreadPoolExecutor(max_workers=4) as workers:
        reservations = list(workers.map(
            lambda _: store.reserve("experiment", {"key": token},
                                    actor="resident_aaaaaaaaaaaa",
                                    allocate=lambda: "experiment_" + secrets.token_hex(6)),
            range(4),
        ))
    assert len({resource_id for resource_id, _created in reservations}) == 1
    assert sum(created for _resource_id, created in reservations) == 1


def test_replayed_reservation_is_not_reported_as_new_when_allocator_repeats_id() -> None:
    """分配器重复返回相同标识时重放预留仍标记为已有"""
    store = ResourceRegistrationStore()
    payload = {"key": secrets.token_hex(6)}
    allocate = lambda: "experiment_0123456789ab"
    first = store.reserve("experiment", payload, actor="resident_aaaaaaaaaaaa", allocate=allocate)
    second = store.reserve("experiment", payload, actor="resident_aaaaaaaaaaaa", allocate=allocate)
    assert first == ("experiment_0123456789ab", True)
    assert second == ("experiment_0123456789ab", False)
