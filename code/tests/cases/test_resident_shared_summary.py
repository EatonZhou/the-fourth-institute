"""One summary survives writer handoffs and feeds work, search and routing."""
import pytest

from tests.cases.test_resident_context import context, A, B, C, WRITER, _summarize
from tests.cases.test_family_work_summaries import SUMMARY, setup_work
from tests.cases.test_family_familize import lineage, charters, future_creation_store, resident
from tests.cases.isa_test_support import ok
from management_portal.society_service.resident_context import work_summary as work
from management_portal.society_service.resident_context.source import LineageResidentSource


def test_global_writer_updates_the_summary_read_by_work_and_discovery(context):
    """全局摘要写入者写下的摘要即为工作摘要与检索读到的同一份，其他写入不改写入者"""
    context.sync_catalog()
    _summarize(context, A, "处理相机驱动")
    authored = context.store.work_summary(A)
    indexed = context.contexts(actor="caller", resident_ids=[A])["residents"][0]
    assert authored["writer_id"] == WRITER
    assert indexed["summary"] == authored["summary"]
    assert indexed["summary_generation"] == authored["revision"] == 1
    assert indexed["summary_updated_at"] == authored["updated_at"]
    assert indexed["summary_stale"] is False
    context.store.write_work_summary(A, A, None, ["covered"], 1, {})
    assert context.store.work_summary(A)["writer_id"] == WRITER


def test_facade_and_internal_search_use_authored_summary_without_global_rewrite(setup_work):
    """Family 的 facade 与内部检索使用 steward 写的摘要，不再全局重写，摘要更新后重新索引"""
    context, roles, *_ = setup_work
    for record in context.source.records:
        record.update(global_summary_managed=False, discovery_visible=record["resident_id"] == C)
    context.sync_catalog()
    work.update_resident_work_summary(A, A, 0, summary=SUMMARY, message_ids=["one"])
    inputs = work.get_family_summary_inputs(C, C)
    authored = work.update_resident_work_summary(roles["steward"][0], C, 0, summary=SUMMARY,
                                               source_versions=inputs["source_versions"])
    context.source.conversation_snapshot = lambda rid: pytest.fail("Family must not be re-summarized")
    context.reconcile()
    assert context.prepare_due() == []
    assert context.claim_summary_jobs(WRITER)["jobs"] == []
    visible = context.search_directory(actor="caller", query="登录")["results"]
    assert [item["resident_id"] for item in visible] == [C]
    assert visible[0]["summary"] == authored["summary"]
    assert visible[0]["summary_stale"] is False
    internal = context.search(actor="caller", query="登录", resident_ids=[A])["results"]
    assert internal[0]["summary"] == authored["summary"]
    assert "登录" in internal[0]["summary_text"]
    for _ in range(10):
        if context.process_one_embedding() is None:
            break
    assert C in context.store.vectors(context.embedder.model_version)
    work.update_resident_work_summary(roles["steward"][0], C, 1,
                                     summary={**SUMMARY, "current_focus": "紫晶导出完成"},
                                     source_versions=inputs["source_versions"])
    assert C not in context.store.vectors(context.embedder.model_version)
    assert context.search_directory(actor="caller", query="紫晶")["results"][0]["resident_id"] == C
    with pytest.raises(ValueError, match="Steward"):
        context.request_rebuild(actor="caller", resident_id=C)
    # Missing coverage is reflected in the very same summary view.
    context.store.missing_work_round(A, "missing")
    assert context.contexts(actor="caller", resident_ids=[A])["residents"][0]["summary_stale"]


def test_familize_during_a_global_claim_cannot_overwrite_facade(context):
    """全局摘要任务领取后 Resident 被 Familize，提交的旧摘要不能覆盖 Family 写入的摘要"""
    context.sync_catalog()
    context.prepare_due()
    claim = next(j for j in context.claim_summary_jobs(WRITER)["jobs"] if j["resident_id"] == A)
    # The live Family roles change before catalog reconciliation catches up.
    context.source.global_summary_resident_ids = lambda ids: set(ids) - {A}
    context.store.write_work_summary(A, "steward", SUMMARY, [], 0, {B: "1"})
    with pytest.raises(PermissionError, match="Family"):
        context.submit_summary(WRITER, claim["claim_id"], {"current_focus": "obsolete profile"},
                               model_version="m", account_id="a")
    assert context.store.work_summary(A)["summary"] == SUMMARY
    assert context.store.summary_job(A) is None


def test_global_claim_cannot_overwrite_a_newer_revision(context):
    """全局摘要任务提交时摘要已有更新版本则被拒绝，不覆盖新版本"""
    context.sync_catalog()
    context.prepare_due()
    claim = next(j for j in context.claim_summary_jobs(WRITER)["jobs"] if j["resident_id"] == A)
    context.store.write_work_summary(A, A, SUMMARY, ["input"], 0, {})
    with pytest.raises(ValueError, match="版本"):
        context.submit_summary(WRITER, claim["claim_id"], {"current_focus": "old"},
                               model_version="m", account_id="a")
    assert context.store.work_summary(A)["summary"] == SUMMARY
    assert context.store.resident(A)["generation"] is None


def test_unfamilize_keeps_the_summary_for_the_next_global_writer(context):
    """Unfamilize 后保留原摘要，下一次全局写入以它为上一版继续更新"""
    context.source.records[0].update(discovery_visible=False, global_summary_managed=False)
    context.sync_catalog()
    context.store.write_work_summary(A, A, SUMMARY, ["input"], 0, {})
    context.source.records[0].update(discovery_visible=True, global_summary_managed=True)
    context.sync_catalog()
    assert context.store.work_summary(A)["summary"] == SUMMARY
    context.prepare_due()
    claim = next(j for j in context.claim_summary_jobs(WRITER)["jobs"] if j["resident_id"] == A)
    assert claim["input"]["previous_summary"] == SUMMARY
    context.submit_summary(WRITER, claim["claim_id"], {**SUMMARY, "current_focus": "独立维护登录"},
                           model_version="m", account_id="a")
    assert context.store.work_summary(A)["revision"] == 2
    assert context.store.work_summary(A)["writer_id"] == WRITER


def test_family_roles_separate_discovery_visibility_from_summary_writers(lineage, charters):
    """Family 中只有 facade 对外可见，成员与内部角色不由全局写入者摘要，Unfamilize 后恢复"""
    member, outsider = resident(lineage, "Member"), resident(lineage, "Independent")
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [member], "title": "Summary Family"})
    facade, steward, router = (family[k] for k in ("facade_resident_id", "steward_resident_id", "router_resident_id"))
    source = LineageResidentSource()
    ids = [facade, steward, router, member, outsider]
    assert source.external_resident_ids(ids) == {facade, outsider}
    assert source.global_summary_resident_ids(ids) == {outsider}
    ok("Unfamilize", {"lineage_id": lineage, "family_id": family["family_id"]})
    assert source.global_summary_resident_ids([member, outsider]) == {member, outsider}


def test_summary_changed_while_embedding_is_reindexed_instead_of_losing_update(context, monkeypatch):
    """生成向量期间摘要被修改时本次结果作废并重新索引，更新不丢失"""
    context.source.records = context.source.records[:1]
    context.sync_catalog()
    context.store.write_work_summary(A, A, SUMMARY, [], 0, {})
    embed = context.embedder.embed_document
    def change_during_embedding(text):
        context.store.write_work_summary(A, A, {**SUMMARY, "current_focus": "新进展"}, [], 1, {})
        return embed(text)
    monkeypatch.setattr(context.embedder, "embed_document", change_during_embedding)
    assert context.process_one_embedding()["result"] == "superseded"
    assert A not in context.store.vectors(context.embedder.model_version)
    monkeypatch.setattr(context.embedder, "embed_document", embed)
    assert context.process_one_embedding()["result"] == "embedded"
    assert A in context.store.vectors(context.embedder.model_version)


def test_discovery_forgets_only_recorded_removals(lineage, monkeypatch):
    """检索只移除已记录删除的 Resident，包括随 Lineage 删除和未知的 ID"""
    from types import SimpleNamespace
    from management_portal.society_service import lineage_reads

    monkeypatch.setattr(lineage_reads, "get_portal_access_store",
                        lambda: SimpleNamespace(is_service_actor=lambda actor: actor == "portal"))
    kept, deleted = resident(lineage, "Kept"), resident(lineage, "Deleted")
    ok("DeleteResident", {"lineage_id": lineage, "resident_id": deleted}, key="discovery-delete-resident")
    other = ok("CreateLineage", {"title": "Other", "project_id": "project_test"}, key="discovery-other")["lineage_id"]
    with_lineage = resident(other, "Gone with its Lineage")
    ok("DeleteLineage", {"lineage_id": other}, key="discovery-delete-lineage")
    unknown = "resident_0123456789ab"
    assert LineageResidentSource().removed_resident_ids([kept, deleted, with_lineage, unknown]) == {
        deleted, with_lineage, unknown,
    }
