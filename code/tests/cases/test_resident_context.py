"""Resident discovery inside SocietyService (``resident_context``).

The worker syncs the catalog, notices conversation changes and prepares
evidence; the ``resident-summary`` duty holder claims prepared jobs, reads
their evidence and submits summaries; searches answer with the caller's
read access.  Real PostgreSQL; SocietyService reads and the embedding model
are replaced.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations

import math
import threading
import uuid
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql

from management_common.portal_database import portal_database, portal_database_url
from management_portal.society_service.contracts import operation_contract
from management_portal.society_service.resident_context import domain as discovery_domain
from management_portal.society_service.resident_context.evidence import (
    PROMPT_VERSION,
    SUMMARY_DUTY_REF,
    SUMMARY_INSTRUCTIONS,
)
from management_portal.society_service.resident_context.service import ResidentContext, Settings
from management_portal.society_service.resident_context.source import LineageResidentSource
from management_portal.society_service.resident_context.store import ResidentContextStore
from management_portal.society_service.resident_context.worker import ResidentContextWorker

A, B, C = "resident_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb", "resident_cccccccccccc"
WRITER = "resident_fbaf22f40dc6"


def _record(resident_id: str, title: str, description: str = "") -> dict:
    return {"resident_id": resident_id, "title": title, "lineage_id": "lineage_aaaaaaaaaaaa",
            "lineage_title": "Portal", "lineage_description": description, "status": "active"}


def _turn(index: int, request: str, reply: str, state: str = "completed") -> list[dict]:
    turn = f"msg_in_{index}"
    return [
        {"id": turn, "role": "user", "kind": "user_request", "turn_id": turn, "runtime_status": state,
         "content": request, "created_at": f"2026-10-05T00:0{index}:00Z"},
        *([{"id": f"msg_out_{index}", "role": "assistant", "kind": "final_answer", "turn_id": turn,
            "runtime_status": state, "content": reply, "created_at": f"2026-10-05T00:0{index}:30Z"}]
          if state == "completed" else []),
    ]


class FakeSource:
    def __init__(self) -> None:
        self.records = [_record(A, "Lineage 页面 Preview", "PDF 预览"), _record(B, "Chat composer", "对话框输入")]
        self.conversations: dict[str, dict] = {}
        self.visible: set[str] | None = None
        self.removed: set[str] = set()

    def removed_resident_ids(self, resident_ids: list[str]) -> set[str]:
        return self.removed.intersection(resident_ids)

    def converse(self, resident_id: str, messages: list[dict], cursor: int) -> None:
        self.conversations[resident_id] = {
            "resident_id": resident_id, "cursor": cursor, "messages": messages, "fingerprint": f"fp{cursor}",
        }

    def discover(self) -> list[dict]:
        return list(self.records)

    def conversation_snapshot(self, resident_id: str) -> dict:
        return self.conversations.get(resident_id) or {
            "resident_id": resident_id, "cursor": 0, "messages": [], "fingerprint": "empty",
        }

    def authorized_resident_ids(self, actor: str, resident_ids: list[str]) -> set[str]:
        return {rid for rid in resident_ids if self.visible is None or rid in self.visible}


class FakeEmbedder:
    """Bag-of-characters vectors: deterministic, normalized, topic-sensitive."""

    model_version = "test-embedder"

    def embed_query(self, text: str) -> list[float]:
        return self.embed_document(text)

    def embed_document(self, text: str) -> list[float]:
        vector = [0.0] * 64
        for character in str(text or "").casefold():
            vector[ord(character) % 64] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


@pytest.fixture
def context() -> ResidentContext:
    return ResidentContext(
        store=ResidentContextStore(), source=FakeSource(), embedder=FakeEmbedder(),
        settings=Settings(debounce_seconds=0, max_job_attempts=2, evidence_budget_chars=10_000),
    )


def _summarize(context: ResidentContext, resident_id: str, focus: str) -> dict:
    """Write a summary for ``resident_id`` the way the duty holder does."""

    context.prepare_due(limit=10)
    claimed = context.claim_summary_jobs(WRITER, limit=10)["jobs"]
    claim = next(job for job in claimed if job["resident_id"] == resident_id)
    for other in claimed:
        if other is not claim:
            context.release_summary_job(WRITER, other["claim_id"], "not in this test")
    return context.submit_summary(
        WRITER, claim["claim_id"], {"current_focus": focus, "primary_ownership": [focus]},
        model_version=f"{WRITER}:codex:gpt-test:medium", account_id="zero",
    )


def test_catalog_sync_queues_new_residents_and_retires_missing_ones(context: ResidentContext) -> None:
    """Resident 目录同步为新 Resident 排队摘要与向量，移除消失的 Resident

    首次同步为每个 Resident 排一份完整摘要和一份向量任务；目录不变时不再排队；目录中消失的 Resident 变为非活动并丢弃其任务。

    :design: decision_d19b9909a674
    """
    assert context.sync_catalog()["created"] == 2
    counts = context.store.counts()
    assert (counts["residents"], counts["queued_jobs"], counts["queued_embedding_jobs"]) == (2, 2, 2)
    # An unchanged catalog queues nothing more.
    assert context.sync_catalog() == {"schema": "tfi.resident-context.catalog-sync.v1",
                                      "seen": 2, "created": 0, "changed": 0, "purged": 0}
    context.source.records = context.source.records[:1]
    context.sync_catalog()
    counts = context.store.counts()
    assert (counts["residents"], counts["queued_jobs"]) == (1, 1)
    assert context.store.resident(B)["active"] is False


def test_a_removed_resident_is_forgotten_at_once_and_an_unreadable_one_kept(context: ResidentContext) -> None:
    """已删除的Resident立即清除摘要，未删除但无法读取的Resident保留

    删除的Resident在下一次发现同步时清除摘要及向量；仅无法读取但未删除的Resident保留。

    :design: decision_8fe87d812a4c
    """
    context.source.converse(B, _turn(1, "输入框换行", "已支持 Shift+Enter 换行"), cursor=2)
    context.sync_catalog()
    _summarize(context, B, "对话框输入")
    # Missing without a recorded removal (its Lineage failed to read): kept however long it lasts.
    context.source.records = context.source.records[:1]
    assert context.sync_catalog()["purged"] == 0
    with context.store.transaction() as connection:
        connection.execute("UPDATE residents SET updated_at='2026-01-01T00:00:00Z' WHERE resident_id=%s", (B,))
    assert context.sync_catalog()["purged"] == 0
    assert (context.store.resident(B)["active"], context.store.resident(B)["generation"]) == (False, 1)
    # Back in the catalog, it still has its summary.
    context.source.records.append(_record(B, "Chat composer"))
    context.sync_catalog()
    assert (context.store.resident(B)["active"], context.store.resident(B)["generation"]) == (True, 1)
    # Deleted: its summary and vector go at the next sync.
    context.source.records = context.source.records[:1]
    context.source.removed.add(B)
    assert context.sync_catalog()["purged"] == 1
    assert context.store.resident(B) is None
    assert context.store.work_summary(B)["revision"] == 0


def test_a_periodic_rebuild_needs_incremental_changes_since_the_last_full_summary(context: ResidentContext) -> None:
    """定期完整重写只针对上次完整重写后有过增量更新的摘要

    完整重写超过 30 天但其间没有增量更新时不排队；有增量更新时以 periodic_full_rebuild 排队。

    :design: decision_8fe87d812a4c
    """
    context.source.converse(A, _turn(1, "PDF 打开慢", "已改为渐进渲染"), cursor=2)
    context.sync_catalog()
    _summarize(context, A, "PDF 预览")
    with context.store.transaction() as connection:
        connection.execute("DELETE FROM summary_jobs")
        connection.execute("UPDATE snapshots SET rebuilt_at='2026-01-01T00:00:00Z' WHERE resident_id=%s", (A,))
    context.reconcile(limit=10)
    assert context.store.summary_job(A) is None  # Nothing new to rebuild from.
    with context.store.transaction() as connection:
        connection.execute("UPDATE snapshots SET incremental_generations=3 WHERE resident_id=%s", (A,))
    context.reconcile(limit=10)
    assert (context.store.summary_job(A) or {}).get("reason") == "periodic_full_rebuild"


def test_the_writer_claims_prepared_evidence_and_submits_a_summary(context: ResidentContext) -> None:
    """摘要职责承担者认领已准备的证据并提交摘要

    worker 读对话后任务变为 ready；认领返回写作规则与已完成回合证据；提交后记录承担者的模型与账号、证据游标，删除任务，同一认领不能再次提交。

    :design: decision_d19b9909a674
    """
    context.source.converse(A, _turn(1, "PDF 打开慢", "已把 PDF 预览改为渐进渲染"), cursor=3)
    context.sync_catalog()
    prepared = {item["resident_id"]: item["result"] for item in context.prepare_due(limit=10)}
    assert prepared == {A: "ready", B: "ready"}

    claimed = context.claim_summary_jobs(WRITER, limit=2)
    [first, second] = claimed["jobs"]
    assert claimed["instructions"] == SUMMARY_INSTRUCTIONS
    assert first["input"]["mode"] == "full"
    assert first["input"]["resident"]["resident_id"] == first["resident_id"]
    claim_a = first if first["resident_id"] == A else second
    texts = [item["content"] for item in claim_a["input"]["new_or_rebuild_context"]]
    assert texts == ["PDF 打开慢", "已把 PDF 预览改为渐进渲染"]

    result = context.submit_summary(
        WRITER, claim_a["claim_id"],
        {"current_focus": "PDF 预览渲染", "primary_ownership": ["Lineage Preview"], "extra": "dropped"},
        model_version=f"{WRITER}:codex:gpt-test:medium", account_id="zero",
    )
    assert (result["resident_id"], result["generation"], result["status"]) == (A, 1, "recorded")
    stored = context.store.resident(A)
    assert stored["summary"]["primary_ownership"] == ["Lineage Preview"]
    assert "owned_components" not in stored["summary"]
    assert "extra" not in stored["summary"]
    assert (stored["model_version"], stored["account_id"], stored["prompt_version"]) == (
        f"{WRITER}:codex:gpt-test:medium", "zero", PROMPT_VERSION,
    )
    assert (stored["source_cursor"], stored["stale"]) == (3, False)
    assert context.store.summary_job(A) is None
    # The claim is spent.
    with pytest.raises(KeyError):
        context.submit_summary(WRITER, claim_a["claim_id"], {"current_focus": "again"},
                               model_version="m", account_id="")


def test_the_queue_counts_jobs_by_step(context: ResidentContext) -> None:
    """Resident 发现摘要队列按状态给出份数

    验证摘要队列按可认领、等待读取对话、已认领与搁置给出份数，认领结果中的剩余份数与之一致。
    """
    context.sync_catalog()
    assert context.summary_queue() == {"ready": 0, "waiting": 2, "claimed": 0, "parked": 0}
    context.prepare_due(limit=10)
    assert context.summary_queue()["ready"] == 2
    claimed = context.claim_summary_jobs(WRITER, limit=1)
    assert (claimed["remaining_ready"], claimed["waiting"]) == (1, 0)
    assert context.summary_queue() == {"ready": 1, "waiting": 0, "claimed": 1, "parked": 0}


def _snapshot_payload(turns: list[tuple[str, str, str]]) -> dict:
    """A canonical conversation snapshot of completed turns ``(input id, request, reply)``."""

    messages, states = [], []
    for index, (input_id, request, reply) in enumerate(turns):
        messages += [
            {"message_id": input_id, "role": "user", "content_parts": [{"type": "text", "text": request}],
             "created_at": f"2026-10-05T00:0{index}:00Z"},
            {"message_id": f"{input_id}_reply", "role": "agent", "content_parts": [{"type": "text", "text": reply}],
             "reply_to_message_id": input_id, "created_at": f"2026-10-05T00:0{index}:30Z"},
        ]
        states.append({"message_id": input_id, "state": "completed", "reply_message_ids": [f"{input_id}_reply"]})
    return {"schema": "tfi.resident-messages-state.v1", "cursor": 9, "messages": messages, "input_states": states}


def test_the_summary_writers_own_duty_runs_are_not_its_work(monkeypatch) -> None:
    """摘要承担者执行职责的轮次不算它的工作

    验证 resident-summary 承担者带职责标记的轮次不进入它的对话快照与指纹，其他 Resident 的同类轮次照常保留。
    """
    payload = _snapshot_payload([
        ("msg_duty", "按职责文档写本轮摘要", "提交 3 份"),
        ("msg_task", "检查摘要搜索为什么慢", "向量检索改为进程内运行"),
    ])
    service = SimpleNamespace(resident=SimpleNamespace(
        get_resident_conversation_snapshot=lambda resident_id, actor: dict(payload, resident_id=resident_id),
    ))
    source = LineageResidentSource(service)
    monkeypatch.setattr(LineageResidentSource, "_summary_writer", staticmethod(lambda: WRITER))
    monkeypatch.setattr(LineageResidentSource, "_business_ref", staticmethod(
        lambda resident_id, message_id: SUMMARY_DUTY_REF if message_id == "msg_duty" else "",
    ))

    writer = source.conversation_snapshot(WRITER)
    assert [item["content"] for item in writer["messages"]] == ["检查摘要搜索为什么慢", "向量检索改为进程内运行"]
    # Only the writer's runs are the duty; anyone else's marked turn is work.
    other = source.conversation_snapshot(A)
    assert len(other["messages"]) == 4
    assert writer["fingerprint"] != other["fingerprint"]


def test_an_empty_summary_is_refused_and_the_claim_kept(context: ResidentContext) -> None:
    """空摘要被拒绝，认领保留

    没有任何字段内容的摘要提交报错，认领仍可重新提交。

    :design: decision_d19b9909a674
    """
    context.sync_catalog()
    context.prepare_due(limit=10)
    [claim] = context.claim_summary_jobs(WRITER, limit=1)["jobs"]
    with pytest.raises(ValueError):
        context.submit_summary(WRITER, claim["claim_id"], {"current_focus": ""}, model_version="m", account_id="")
    recorded = context.submit_summary(WRITER, claim["claim_id"], {"current_focus": "PDF 预览"},
                                      model_version="m", account_id="")
    assert recorded["resident_id"] == claim["resident_id"]


def test_a_summary_reporting_test_counts_is_refused(context: ResidentContext) -> None:
    """写测试数量的摘要被拒绝

    摘要中出现“71 项测试通过”“181 passed”这类测试数量时提交报错并保留认领；只写实验结果的摘要正常记录。

    :design: decision_8fe87d812a4c
    """
    context.sync_catalog()
    context.prepare_due(limit=10)
    [claim] = context.claim_summary_jobs(WRITER, limit=1)["jobs"]
    for result in ("修复恢复预算，71 项测试通过", "修复恢复预算，181 passed、54 subtests passed"):
        with pytest.raises(ValueError, match="测试数量"):
            context.submit_summary(WRITER, claim["claim_id"], {"current_focus": "恢复预算", "recent_results": [result]},
                                   model_version="m", account_id="")
    recorded = context.submit_summary(
        WRITER, claim["claim_id"],
        {"current_focus": "恢复预算", "recent_results": ["修复恢复预算，12 个样本通过质量验收"]},
        model_version="m", account_id="",
    )
    assert recorded["status"] == "recorded"


def test_new_turns_without_completed_work_only_record_pending_requests(context: ResidentContext) -> None:
    """没有新完成工作的对话变化只记录待处理请求

    增量任务读到的新回合尚未完成时不交给写作者：保留原摘要与原写作者，只更新 pending_requests 与游标。

    :design: decision_d19b9909a674
    """
    context.source.converse(A, _turn(1, "PDF 打开慢", "已改为渐进渲染"), cursor=3)
    context.sync_catalog()
    _summarize(context, A, "PDF 预览")
    # A new request that has not finished: no writer is needed.
    context.source.converse(A, _turn(1, "PDF 打开慢", "已改为渐进渲染") + _turn(2, "再加缩放", "", "running"), cursor=5)
    context.store.enqueue(A, mode="incremental", reason="context_cursor_advanced")
    [result] = [item for item in context.prepare_due(limit=10) if item["resident_id"] == A]
    assert result["result"] == "pending_recorded"
    stored = context.store.resident(A)
    assert stored["summary"]["current_focus"] == "PDF 预览"
    assert [item["request"] for item in stored["summary"]["pending_requests"]] == ["再加缩放"]
    assert stored["source_cursor"] == 5 and stored["generation"] == 2
    # The stable text keeps the writer that wrote it.
    assert stored["model_version"] == f"{WRITER}:codex:gpt-test:medium"


def test_a_claim_belongs_to_its_holder_and_expires_back_to_ready(context: ResidentContext) -> None:
    """摘要认领只属于认领者，租约到期退回待认领

    其他 Resident 不能提交别人的认领；租约过期后任务回到 ready，旧认领失效，可被重新认领。

    :design: decision_d19b9909a674
    """
    context.sync_catalog()
    context.prepare_due(limit=10)
    [claim] = context.claim_summary_jobs(WRITER, limit=1)["jobs"]
    with pytest.raises(PermissionError):
        context.submit_summary("resident_ffffffffffff", claim["claim_id"], {"current_focus": "x"},
                               model_version="m", account_id="")
    with context.store.transaction() as connection:
        connection.execute("UPDATE summary_jobs SET lease_until='2000-01-01T00:00:00Z' WHERE claim_id=%s",
                           (claim["claim_id"],))
    assert context.store.recover_expired_leases()["claimed"] == 1
    with pytest.raises(KeyError):
        context.submit_summary(WRITER, claim["claim_id"], {"current_focus": "x"}, model_version="m", account_id="")
    assert len(context.claim_summary_jobs(WRITER, limit=10)["jobs"]) == 2


def test_new_messages_replace_unclaimed_evidence_but_not_a_claim(context: ResidentContext) -> None:
    """新消息作废未认领的证据，不打断已认领的写作

    对话变化时 ready 任务回到 queued 并清空证据、保留 full 模式；已认领任务保持 claimed。

    :design: decision_d19b9909a674
    """
    context.sync_catalog()
    context.prepare_due(limit=10)
    [claim] = context.claim_summary_jobs(WRITER, limit=1)["jobs"]
    other = B if claim["resident_id"] == A else A
    context.store.enqueue(other, mode="incremental", reason="conversation_messages_changed")
    context.store.enqueue(claim["resident_id"], mode="incremental", reason="conversation_messages_changed")
    jobs = {rid: context.store.summary_job(rid) for rid in (A, B)}
    assert (jobs[other]["status"], jobs[other]["evidence_json"], jobs[other]["mode"]) == ("queued", {}, "full")
    assert jobs[claim["resident_id"]]["status"] == "claimed"


def test_a_released_job_backs_off_then_parks_until_its_conversation_changes(context: ResidentContext) -> None:
    """放弃的摘要任务退避重试，多次失败后搁置到对话变化

    达到最大尝试次数后任务搁置为 failed；相同对话不唤醒，新对话唤醒并重置尝试次数。

    :design: decision_d19b9909a674
    """
    context.source.records = context.source.records[:1]
    context.source.converse(A, _turn(1, "PDF 打开慢", "已改"), cursor=3)
    context.sync_catalog()
    for expected in ("retry", "parked"):
        with context.store.transaction() as connection:
            connection.execute("UPDATE summary_jobs SET available_at='2000-01-01T00:00:00Z' WHERE resident_id=%s", (A,))
        context.prepare_due()
        [claim] = context.claim_summary_jobs(WRITER)["jobs"]
        assert context.release_summary_job(WRITER, claim["claim_id"], "证据不足")["status"] == expected
    assert context.store.summary_job(A)["status"] == "failed"
    # The same conversation does not revive it; a changed one does.
    context.reconcile()
    assert context.store.summary_job(A)["status"] == "failed"
    context.source.converse(A, _turn(1, "PDF 打开慢", "已改") + _turn(2, "缩放卡", "已修"), cursor=6)
    with context.store.transaction() as connection:
        connection.execute("UPDATE residents SET last_checked_at=NULL")
    context.reconcile()
    job = context.store.summary_job(A)
    assert (job["status"], job["attempts"]) == ("queued", 0)


def test_a_claim_is_bounded_by_its_evidence_size(context: ResidentContext) -> None:
    """一次认领受证据总量限制

    证据总量超过预算时一次只给一份，返回剩余可认领份数。

    :design: decision_d19b9909a674
    """
    long_reply = "渐进渲染" * 2000
    context.source.converse(A, _turn(1, "PDF 打开慢", long_reply), cursor=3)
    context.source.converse(B, _turn(1, "缩进丢失", long_reply), cursor=3)
    context.sync_catalog()
    context.prepare_due(limit=10)
    first = context.claim_summary_jobs(WRITER, limit=10)
    assert len(first["jobs"]) == 1 and first["remaining_ready"] == 1


def test_a_claim_without_a_count_takes_every_ready_job() -> None:
    """省略份数时认领全部可认领的摘要

    不传 limit 时一次认领全部可认领的份数，没有固定份数上限；证据总量预算仍然生效。

    :design: decision_d19b9909a674
    """
    source = FakeSource()
    source.records = [_record(f"resident_{index:012x}", f"Resident {index}") for index in range(1, 13)]
    for record in source.records:
        source.converse(record["resident_id"], _turn(1, "PDF 打开慢", "已改"), cursor=3)
    context = ResidentContext(
        store=ResidentContextStore(), source=source, embedder=FakeEmbedder(),
        settings=Settings(debounce_seconds=0, evidence_budget_chars=1_000_000),
    )
    context.sync_catalog()
    context.prepare_due(limit=20)
    claimed = context.claim_summary_jobs(WRITER)
    assert len(claimed["jobs"]) == 12 and claimed["remaining_ready"] == 0


def test_search_combines_words_and_vectors_within_the_callers_read_access(context: ResidentContext) -> None:
    """Resident 搜索在调用者可读范围内合并词语与向量结果，向量模型不可用时只按词语检索

    :module: resident.workspace-memory"""
    context.source.records.append(_record(C, "Paper reader", "论文阅读"))
    context.sync_catalog()
    _summarize(context, A, "Lineage 页面 Preview 的 PDF 预览")
    while context.process_one_embedding():
        pass
    result = context.search(actor="CASE-A", query="Lineage 页面 Preview PDF 慢", limit=3)
    assert result["results"][0]["resident_id"] == A
    assert result["retrieval"] == "hybrid"
    assert set(result["results"][0]["retrieval_sources"]) == {"lexical", "vector"}
    context.source.visible = {B, C}
    assert A not in [item["resident_id"] for item in context.search(actor="guest", query="Lineage Preview PDF")["results"]]
    # Without the model, search still answers from words.
    context._embedder = SimpleNamespace(model_version="test-embedder", loaded=False,
                                        embed_query=lambda _q: (_ for _ in ()).throw(RuntimeError("no model")))
    context.source.visible = None
    assert context.search(actor="CASE-A", query="Lineage Preview")["retrieval"] == "lexical"


def test_disabling_embeddings_stops_cached_model_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turning off embeddings takes effect even after the shared model was loaded."""
    from management_portal.society_service.resident_context import service as service_module

    switch = {"enabled": True}
    model = FakeEmbedder()
    monkeypatch.setattr(service_module, "resident_embeddings_available", lambda: switch["enabled"])
    monkeypatch.setattr(service_module, "shared_embedder", lambda: model)
    store = SimpleNamespace(vectors=lambda *_args: pytest.fail("stored vectors were read while disabled"))
    context = ResidentContext(store=store, source=object())
    assert context.embeddings_available
    assert context.embedder is model

    switch["enabled"] = False
    assert not context.embeddings_available
    results = context._hybrid_candidates("Preview", [
        {"resident_id": A, "title": "Preview", "lineage_title": "Portal", "summary_text": ""},
    ], limit=8)
    assert results[0]["retrieval_sources"] == ["lexical"]


def test_routing_recall_interleaves_ranked_title_and_ownership_matches(context: ResidentContext) -> None:
    """路由召回交错合并排名、标题与负责范围匹配

    负责范围匹配请求的 Resident 进入召回首位；目录搜索按标题匹配并报告可见总数。

    :design: decision_d19b9909a674
    """
    context.sync_catalog()
    _summarize(context, B, "对话框缩进")
    result = context.routing_candidates(actor="CASE-A", query="对话框缩进格式发出去后没有了", limit=5)
    assert result["results"][0]["resident_id"] == B
    directory = context.search_directory(actor="CASE-A", query="chat")
    assert [item["resident_id"] for item in directory["results"]] == [B]
    assert directory["total_visible"] == 2


def test_contexts_and_rebuilds_answer_with_the_callers_read_access(context: ResidentContext) -> None:
    """Resident 上下文读取与摘要重建按调用者读权限过滤，一次最多查 128 个

    :module: resident.workspace-memory"""
    context.sync_catalog()
    context.source.visible = {A}
    assert [item["resident_id"] for item in context.contexts(actor="u", resident_ids=[A, B])["residents"]] == [A]
    with pytest.raises(PermissionError):
        context.request_rebuild(actor="u", resident_id=B)
    assert context.request_rebuild(actor="u", resident_id=A, reason="职责调整")["queued"] is True
    assert context.store.summary_job(A)["reason"] == "manual:职责调整"
    with pytest.raises(ValueError):
        context.contexts(actor="u", resident_ids=[f"resident_{index:012x}" for index in range(129)])


def test_a_routing_correction_becomes_one_ground_truth_case(context: ResidentContext) -> None:
    """路由纠正只形成一个基准用例

    同一请求与同一正确答案重复提交只新增一次基准用例，两次提交都有记录；非法动作被拒绝。

    :design: decision_d19b9909a674
    """
    context.sync_catalog()
    arguments = {"actor": "sample_peer", "query": "缩进丢失", "predicted": [{"resident_id": A, "reason": "猜的"}],
                 "ground_truth_resident_id": B, "ground_truth_action": "open", "rationale": "直接负责"}
    first = context.submit_routing_eval_correction(**arguments)
    second = context.submit_routing_eval_correction(**arguments)
    assert (first["dataset_added"], second["dataset_added"]) == (True, False)
    assert first["submission"]["dataset_case_id"] == second["submission"]["dataset_case_id"]
    with pytest.raises(ValueError):
        context.submit_routing_eval_correction(**{**arguments, "ground_truth_action": "delete"})


def test_one_worker_holds_the_lease_until_it_expires() -> None:
    """Dev 与 Release 之间同一时刻只有一个发现 worker

    租约持有者可以续租，其他实例在租约到期前拿不到，到期后可以接手，释放后租约清空。

    :design: decision_d19b9909a674
    """
    store = ResidentContextStore()
    assert store.hold_worker_lease("dev:1", ttl_seconds=90)
    assert not store.hold_worker_lease("release:2", ttl_seconds=90)
    assert store.hold_worker_lease("dev:1", ttl_seconds=90)
    with store.transaction() as connection:
        connection.execute(
            "UPDATE service_state SET value_json='{\"holder\":\"dev:1\",\"lease_until\":\"2000-01-01T00:00:00Z\"}'"
        )
    assert store.hold_worker_lease("release:2", ttl_seconds=90)
    store.release_worker_lease("release:2")
    assert store.worker_lease() == {}


def test_the_worker_pass_syncs_prepares_and_embeds(context: ResidentContext) -> None:
    """发现 worker 一轮完成目录同步、证据准备与向量计算

    一轮执行同步目录、检查对话、准备证据并计算一个向量。

    :design: decision_d19b9909a674
    """
    worker = ResidentContextWorker(context, holder="test:1")
    ran = worker.run_once(sync_catalog=True, reconcile=True)
    assert ran == {"catalog": True, "reconcile": True, "work": True}
    counts = context.store.counts()
    assert counts["ready_jobs"] + counts["queued_jobs"] == 2
    assert counts["embeddings"] == 1


def test_searches_run_in_the_service_process_and_the_rest_in_the_read_pool(monkeypatch) -> None:
    """Resident 搜索在 SocietyService 主进程执行，其余读取进只读进程池

    search_residents 与 resident_routing_candidates 标记 in_process，绕过只读进程池；目录与摘要读取仍进进程池。

    :design: decision_d19b9909a674
    """
    from management_portal.society_service import server

    for method in ("search_residents", "resident_routing_candidates"):
        operation = operation_contract("resident", method)
        assert (operation.mode, operation.in_process) == ("read", True)
    for method in ("search_resident_directory", "get_resident_contexts"):
        assert operation_contract("resident", method).in_process is False

    served = []
    monkeypatch.setattr(discovery_domain, "resident_context", lambda: SimpleNamespace(
        search=lambda **kwargs: served.append(("process", kwargs)) or {"results": []},
        contexts=lambda **kwargs: {"residents": []},
    ))
    fake_server = SimpleNamespace(
        read_pool=SimpleNamespace(workers=4, call=lambda params: served.append(("pool", params["name"])) or {}),
        _finish_changes=lambda changes, domain="": None,
    )
    server.SocietyServiceServer._domain_call(fake_server, {
        "domain": "resident", "name": "search_residents", "args": ["CASE-A", "PDF"], "kwargs": {},
    })
    server.SocietyServiceServer._domain_call(fake_server, {
        "domain": "resident", "name": "get_resident_contexts", "args": ["CASE-A", [A]], "kwargs": {},
    })
    assert [kind for kind, _ in served] == ["process", "pool"]


def test_a_starting_process_does_not_lock_tables_of_an_existing_schema() -> None:
    """Resident 上下文存储在已有 schema 上启动时不加表锁，不被进行中的写入阻塞"""
    database = portal_database(schema=f"context_start_{uuid.uuid4().hex[:8]}")
    ResidentContextStore(database=database).ensure_schema()
    with psycopg.connect(portal_database_url()) as writer:
        writer.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(database.schema)))
        writer.execute("LOCK TABLE summary_jobs IN ROW EXCLUSIVE MODE")
        starting = threading.Thread(target=ResidentContextStore(database=database).ensure_schema)
        starting.start()
        starting.join(5)
        blocked = starting.is_alive()
        writer.rollback()
        starting.join()
    assert not blocked
