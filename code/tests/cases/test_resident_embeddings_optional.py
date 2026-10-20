"""Resident discovery keeps lexical search available without local model packages."""

from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from management_common import embedding_feature
from management_portal.society_service.resident_context import embedder as embedder_module
from management_portal.society_service.resident_context import service as service_module
from management_portal.society_service.resident_context.worker import ResidentContextWorker


def test_runtime_choice_disables_installed_embedding_packages(monkeypatch, tmp_path) -> None:
    """运行时开关设为关闭时，已安装的向量模型包也不启用"""
    monkeypatch.setattr(embedding_feature, "settings_file", lambda: tmp_path / "embeddings.json")
    assert not embedding_feature.enabled()
    assert not embedder_module.resident_embeddings_available()
    embedding_feature.set_enabled(False)
    assert not embedder_module.resident_embeddings_available()


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


def test_disabling_releases_loaded_model_without_deleting_vectors(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(embedding_feature, "settings_file", lambda: tmp_path / "embeddings.json")
    embedding_feature.set_enabled(True)
    monkeypatch.setattr(service_module, "resident_embeddings_available", embedding_feature.enabled)
    loaded = embedder_module.ResidentEmbedder(model_dir=tmp_path)
    loaded._model = (object(), object(), set())
    cancelled = []
    loaded._unloader = SimpleNamespace(cancel=lambda: cancelled.append(True))
    monkeypatch.setattr(embedder_module, "_SHARED", loaded)
    vectors = {"resident_aaaaaaaaaaaa": [1.0, 0.0]}
    store = SimpleNamespace(
        vectors=lambda *_args: (_ for _ in ()).throw(AssertionError("disabled search read stored vectors")),
        claim_embedding_job=lambda: (_ for _ in ()).throw(AssertionError("disabled worker claimed embedding job")),
    )
    context = service_module.ResidentContext(store=store, source=object())
    assert context.embedder is loaded  # model was already used by this process

    assert context.set_embeddings_enabled(False) == {"enabled": False}
    assert not embedding_feature.enabled()
    assert context._embedder is None
    assert embedder_module._SHARED is None
    assert loaded._model is None and loaded._unloader is None and cancelled == [True]
    assert vectors == {"resident_aaaaaaaaaaaa": [1.0, 0.0]}
    assert context.process_one_embedding() is None
    results = context._hybrid_candidates("Alpha", [
        {"resident_id": "resident_aaaaaaaaaaaa", "title": "Alpha", "summary_text": ""},
    ], limit=8)
    assert results[0]["retrieval_sources"] == ["lexical"]


def test_disabling_waits_for_active_vector_search(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(embedding_feature, "settings_file", lambda: tmp_path / "embeddings.json")
    embedding_feature.set_enabled(True)
    monkeypatch.setattr(service_module, "resident_embeddings_available", embedding_feature.enabled)
    monkeypatch.setattr(embedder_module, "_SHARED", None)
    context = service_module.ResidentContext(store=object(), source=object())
    entered, release = Event(), Event()
    vector_calls = []

    def vector_candidates(_query, documents, *, limit):
        vector_calls.append(True)
        entered.set()
        assert release.wait(2)
        return documents[:limit]

    monkeypatch.setattr(context, "_vector_candidates", vector_candidates)
    documents = [{"resident_id": "resident_aaaaaaaaaaaa", "title": "Alpha", "summary_text": ""}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        search = pool.submit(context._hybrid_candidates, "Alpha", documents, limit=8)
        assert entered.wait(2)
        disable = pool.submit(context.set_embeddings_enabled, False)
        assert not disable.done()
        release.set()
        assert search.result(timeout=2)[0]["retrieval_sources"] == ["lexical", "vector"]
        assert disable.result(timeout=2) == {"enabled": False}
    assert context._hybrid_candidates("Alpha", documents, limit=8)[0]["retrieval_sources"] == ["lexical"]
    assert vector_calls == [True]
