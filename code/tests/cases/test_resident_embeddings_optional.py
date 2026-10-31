"""Resident discovery keeps lexical search available without local model packages."""

from types import SimpleNamespace

from management_common import embedding_feature
from management_portal.society_service.resident_context import embedder as embedder_module
from management_portal.society_service.resident_context import service as service_module
from management_portal.society_service.resident_context.worker import ResidentContextWorker


def test_runtime_choice_disables_installed_embedding_packages(monkeypatch, tmp_path) -> None:
    """运行时开关设为关闭时，已安装的向量模型包也不启用"""
    settings = tmp_path / "embeddings.json"
    monkeypatch.setattr(embedding_feature, "settings_file", lambda: settings)
    assert not embedding_feature.enabled()
    assert not embedder_module.resident_embeddings_available()
    settings.write_text('{"enabled": false}\n', encoding="utf-8")
    assert not embedder_module.resident_embeddings_available()
    settings.write_text('{"enabled": true}\n', encoding="utf-8")
    assert embedding_feature.enabled()


def test_lexical_search_and_summary_worker_continue_without_embeddings(monkeypatch) -> None:
    """向量功能不可用时 Resident 搜索按词语检索，worker 不做向量计算"""
    monkeypatch.setattr(service_module, "resident_embeddings_available", lambda: False)

    def unexpected_embedding(*_args) -> None:
        raise AssertionError("embedding work must stay disabled")

    store = SimpleNamespace(
        documents=lambda _ids: [{"resident_id": "resident_aaaaaaaaaaaa", "title": "Alpha"}],
        claim_embedding_job=unexpected_embedding,
        recover_running_embeddings=lambda: None,
        recover_expired_leases=lambda: None,
        discard_inactive_jobs=lambda: None,
        enqueue_summary_backfill=lambda _version: None,
        enqueue_embedding_backfill=unexpected_embedding,
    )
    source = SimpleNamespace(authorized_resident_ids=lambda _actor, ids: set(ids))
    context = service_module.ResidentContext(store=store, source=source)

    result = context.search(actor="test", query="Alpha")
    assert result["retrieval"] == "lexical"
    assert result["results"][0]["resident_id"] == "resident_aaaaaaaaaaaa"
    assert context.process_one_embedding() is None
    ResidentContextWorker(context, holder="test:optional")._take_over()
