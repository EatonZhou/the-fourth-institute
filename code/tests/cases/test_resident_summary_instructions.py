"""Resident summary instructions (decision_d19b9909a674).

GetResidentSummaries and RequestResidentSummaryRebuild act with the caller's
own read access.  Claim/GetEvidence/Submit/Release are only for the Resident
holding the Portal duty ``resident-summary``; a submitted summary records that
Resident's current model and account.  GetResidentSummaryQueue gives anyone the
job counts, so a timer's Effect wakes the holder only when summaries are ready.

:importance: P2
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from management_portal.hatg.contracts import InstructionError
from management_portal.society_service.isa import residents as resident_isa


class _Discovery:
    def __init__(self, residents=None, *, error=None):
        self.residents = residents or []
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def contexts(self, *, actor, resident_ids):
        self.calls.append(("contexts", {"actor": actor, "resident_ids": resident_ids}))
        if self.error:
            raise self.error
        return {"schema": "tfi.resident-context.snapshots.v1", "residents": self.residents}

    def request_rebuild(self, *, actor, resident_id, reason):
        self.calls.append(("request_rebuild", {"actor": actor, "resident_id": resident_id, "reason": reason}))
        if self.error:
            raise self.error
        return {"resident_id": resident_id, "queued": True}


def _invoke(handler, arguments: dict, caller: str = "CASE-A"):
    return handler(SimpleNamespace(caller=caller, argument=lambda name, default=None: arguments.get(name, default)))


@pytest.fixture
def discovery(monkeypatch):
    fake = _Discovery([
        {
            "resident_id": "resident_aaaaaaaaaaaa",
            "title": "权限内核",
            "lineage_id": "lineage_e26fbccf5b06",
            "summary": {
                "current_focus": "Operation 资源",
                "route_when": ["权限内核问题"],
                "avoid_when": ["页面样式"],
                "pending_requests": [{"message_id": "msg_1", "summary": "补测试"}],
                "unexpected_field": "dropped",
            },
            "summary_updated_at": "2026-10-01T10:00:00Z",
            "summary_stale": False,
            "vector_score": 0.4,
        },
    ])
    monkeypatch.setattr(resident_isa, "_discovery", lambda: fake)
    return fake


def test_summaries_are_read_as_the_caller_and_keep_only_summary_fields(discovery):
    """GetResidentSummaries 以调用者身份读取并去重，只返回摘要字段，列出不可用的 Resident"""
    result = _invoke(resident_isa.get_resident_summaries, {
        "resident_ids": ["RESIDENT_AAAAAAAAAAAA", "resident_bbbbbbbbbbbb", "resident_aaaaaaaaaaaa"],
    }, caller="resident_4b1da6140cf2")

    assert discovery.calls == [("contexts", {
        "actor": "resident_4b1da6140cf2",
        "resident_ids": ["resident_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb"],
    })]
    assert result["unavailable_resident_ids"] == ["resident_bbbbbbbbbbbb"]
    assert result["summaries"] == [{
        "resident_id": "resident_aaaaaaaaaaaa",
        "title": "权限内核",
        "lineage_id": "lineage_e26fbccf5b06",
        "summary": {"current_focus": "Operation 资源", "route_when": ["权限内核问题"], "avoid_when": ["页面样式"]},
        "pending_requests": [{"message_id": "msg_1", "summary": "补测试"}],
        "summary_updated_at": "2026-10-01T10:00:00Z",
        "summary_stale": False,
    }]


def test_summaries_can_be_read_with_only_the_fields_a_reader_needs(discovery):
    """GetResidentSummaries 可以只读取需要的摘要字段

    GetResidentSummaries 带 fields 时只返回列出的摘要字段，未列出 pending_requests 时不带待处理请求；未知字段按参数错误拒绝。

    The Family Router reads only the routing fields, without the pending requests."""

    result = _invoke(resident_isa.get_resident_summaries, {
        "resident_ids": ["resident_aaaaaaaaaaaa"], "fields": ["route_when", "avoid_when"],
    }, caller="resident_4b1da6140cf2")
    [row] = result["summaries"]
    assert row["summary"] == {"route_when": ["权限内核问题"], "avoid_when": ["页面样式"]}
    assert "pending_requests" not in row
    with_pending = _invoke(resident_isa.get_resident_summaries, {
        "resident_ids": ["resident_aaaaaaaaaaaa"], "fields": ["current_focus", "pending_requests"],
    }, caller="resident_4b1da6140cf2")["summaries"][0]
    assert with_pending["summary"] == {"current_focus": "Operation 资源"}
    assert with_pending["pending_requests"] == [{"message_id": "msg_1", "summary": "补测试"}]
    with pytest.raises(InstructionError) as unknown:
        _invoke(resident_isa.get_resident_summaries, {"resident_ids": ["resident_aaaaaaaaaaaa"], "fields": ["vector_score"]})
    assert unknown.value.code == "instruction.invalid_arguments"


def test_summaries_include_what_their_maintainer_needs(discovery, monkeypatch):
    """GetResidentSummaries 带 include_maintenance 时附上维护摘要所需的版本、来源、漏写轮次和维护者

    尚无摘要的可见 Resident 也返回一项，revision 为 0，供首次 UpdateResidentWorkSummary 使用；调用者看不到的仍列为不可用。"""
    from management_portal.society_service.resident_context import work_summary

    written = {
        "resident_aaaaaaaaaaaa": {"resident_id": "resident_aaaaaaaaaaaa", "summary": {"current_focus": "Operation 资源"},
                                  "revision": 4, "source_versions": {}, "missing_message_ids": ["msg_2"],
                                  "writer_id": "resident_aaaaaaaaaaaa", "updated_at": "2026-10-01T10:00:00Z",
                                  "title": "权限内核", "lineage_id": "lineage_e26fbccf5b06"},
        "resident_cccccccccccc": {"resident_id": "resident_cccccccccccc", "summary": {}, "revision": 0,
                                  "source_versions": {}, "missing_message_ids": ["msg_1"], "writer_id": "",
                                  "updated_at": "", "title": "新成员", "lineage_id": "lineage_e26fbccf5b06"},
    }

    def maintenance_view(actor, resident_id):
        assert actor == "resident_cccccccccccc"
        if resident_id not in written:
            raise PermissionError("无权读取这个 Resident")
        return written[resident_id]

    monkeypatch.setattr(work_summary, "maintenance_view", maintenance_view)
    result = _invoke(resident_isa.get_resident_summaries, {
        "resident_ids": ["resident_aaaaaaaaaaaa", "resident_cccccccccccc", "resident_bbbbbbbbbbbb"],
        "fields": ["current_focus"], "include_maintenance": True,
    }, caller="resident_cccccccccccc")
    first, own = result["summaries"]
    assert first["summary"] == {"current_focus": "Operation 资源"}
    assert (first["revision"], first["missing_message_ids"], first["writer_id"]) == (4, ["msg_2"], "resident_aaaaaaaaaaaa")
    assert own["resident_id"] == "resident_cccccccccccc" and own["title"] == "新成员"
    assert (own["summary"], own["revision"], own["missing_message_ids"]) == ({}, 0, ["msg_1"])
    assert result["unavailable_resident_ids"] == ["resident_bbbbbbbbbbbb"]
    plain = _invoke(resident_isa.get_resident_summaries, {"resident_ids": ["resident_aaaaaaaaaaaa"]})["summaries"][0]
    assert "revision" not in plain


def test_summaries_reject_empty_and_oversized_requests(discovery):
    """GetResidentSummaries 拒绝空列表和超过 128 个 Resident 的请求"""
    with pytest.raises(InstructionError) as empty:
        _invoke(resident_isa.get_resident_summaries, {"resident_ids": []})
    with pytest.raises(InstructionError) as oversized:
        _invoke(resident_isa.get_resident_summaries, {
            "resident_ids": [f"resident_{index:012x}" for index in range(129)],
        })

    assert empty.value.code == oversized.value.code == "instruction.invalid_arguments"
    assert discovery.calls == []


def test_summaries_accept_one_lineage_sized_batch(discovery):
    """GetResidentSummaries 一次可读取一个 Lineage 规模的 113 个 Resident"""
    resident_ids = [f"resident_{index:012x}" for index in range(113)]
    _invoke(resident_isa.get_resident_summaries, {"resident_ids": resident_ids})
    assert discovery.calls == [("contexts", {"actor": "CASE-A", "resident_ids": resident_ids})]


def test_an_unexpected_summary_failure_is_a_retryable_error(monkeypatch):
    """摘要读取的意外失败是可重试错误

    发现模块抛出非权限、非参数错误时，指令返回可重试的 instruction.owner_unavailable。

    :design: decision_d19b9909a674
    """
    monkeypatch.setattr(resident_isa, "_discovery", lambda: _Discovery(error=RuntimeError("down")))
    with pytest.raises(InstructionError) as caught:
        _invoke(resident_isa.get_resident_summaries, {"resident_ids": ["resident_aaaaaaaaaaaa"]})

    assert caught.value.code == "instruction.owner_unavailable"
    assert caught.value.retryable is True


def test_rebuild_is_requested_as_the_caller(discovery):
    """RequestResidentSummaryRebuild 以调用者身份请求重建并带上原因"""
    result = _invoke(resident_isa.request_resident_summary_rebuild, {
        "resident_id": "resident_aaaaaaaaaaaa", "reason": "职责调整",
    }, caller="resident_4b1da6140cf2")

    assert result == {"resident_id": "resident_aaaaaaaaaaaa", "status": "queued"}
    assert discovery.calls == [("request_rebuild", {
        "actor": "resident_4b1da6140cf2", "resident_id": "resident_aaaaaaaaaaaa", "reason": "职责调整",
    })]


def test_rebuild_of_an_invisible_resident_is_forbidden(monkeypatch):
    """请求重建不可见 Resident 的摘要返回 instruction.forbidden"""
    monkeypatch.setattr(resident_isa, "_discovery", lambda: _Discovery(error=PermissionError("not visible")))
    with pytest.raises(InstructionError) as caught:
        _invoke(resident_isa.request_resident_summary_rebuild, {"resident_id": "resident_aaaaaaaaaaaa"})

    assert caught.value.code == "instruction.forbidden"


WRITER = "resident_fbaf22f40dc6"


class _Writer:
    def __init__(self):
        self.calls: list[tuple] = []

    def summary_queue(self):
        self.calls.append(("queue",))
        return {"ready": 2, "waiting": 1, "claimed": 0, "parked": 0}

    def claim_summary_jobs(self, holder, *, limit):
        self.calls.append(("claim", holder, limit))
        return {"jobs": [], "remaining_ready": 0, "waiting": 0}

    def submit_summary(self, holder, claim_id, summary, *, model_version, account_id):
        self.calls.append(("submit", holder, claim_id, summary, model_version, account_id))
        if claim_id == "summary_claim_gone":
            raise KeyError("没有这个摘要认领，或认领已过期被收回")
        return {"resident_id": "resident_aaaaaaaaaaaa", "mode": "full", "generation": 3, "status": "recorded"}

    def release_summary_job(self, holder, claim_id, reason):
        self.calls.append(("release", holder, claim_id, reason))
        return {"resident_id": "resident_aaaaaaaaaaaa", "status": "retry", "attempts": 1, "parked": False}


@pytest.fixture
def writer(monkeypatch):
    from management_portal import application_targets

    fake = _Writer()
    monkeypatch.setattr(resident_isa, "_discovery", lambda: fake)
    monkeypatch.setattr(application_targets, "summary_resident", lambda: WRITER)
    monkeypatch.setattr(resident_isa, "_writer_identity", lambda rid: (f"{rid}:codex:gpt-test:medium", "zero"))
    return fake


@pytest.mark.parametrize("handler, arguments", [
    (resident_isa.claim_resident_summary_jobs, {}),
    (resident_isa.submit_resident_summary, {"claim_id": "summary_claim_1", "summary": {"current_focus": "x"}}),
    (resident_isa.release_resident_summary_job, {"claim_id": "summary_claim_1", "reason": "r"}),
])
def test_only_the_summary_duty_holder_writes_summaries(writer, handler, arguments):
    """只有 resident-summary 职责承担者能认领和提交摘要

    其他 Resident 调用 Claim/Submit/Release 都返回 instruction.forbidden，且不触达发现模块。

    :design: decision_d19b9909a674
    """
    with pytest.raises(InstructionError) as caught:
        _invoke(handler, arguments, caller="resident_4b1da6140cf2")
    assert caught.value.code == "instruction.forbidden"
    assert writer.calls == []


def test_anyone_reads_how_many_summaries_wait(writer):
    """任何调用者都能读取摘要队列份数

    验证 GetResidentSummaryQueue 不限于摘要承担者，只返回队列份数。
    """
    queue = _invoke(resident_isa.get_resident_summary_queue, {}, caller="resident_3982a1ccedd2")
    assert queue == {"ready": 2, "waiting": 1, "claimed": 0, "parked": 0}
    assert writer.calls == [("queue",)]


def test_the_duty_holder_claims_reads_submits_and_releases_as_itself(writer):
    """职责承担者以自己的身份认领、提交和放弃摘要

    三条指令都以调用者为认领者；提交时附带承担者当前的 provider、模型、推理强度与账号。

    :design: decision_d19b9909a674
    """
    _invoke(resident_isa.claim_resident_summary_jobs, {"limit": 3}, caller=WRITER)
    submitted = _invoke(resident_isa.submit_resident_summary, {
        "claim_id": "summary_claim_1", "summary": {"current_focus": "PDF 预览"},
    }, caller=WRITER)
    released = _invoke(resident_isa.release_resident_summary_job, {
        "claim_id": "summary_claim_2", "reason": "证据不足",
    }, caller=WRITER)

    assert writer.calls == [
        ("claim", WRITER, 3),
        ("submit", WRITER, "summary_claim_1", {"current_focus": "PDF 预览"}, f"{WRITER}:codex:gpt-test:medium", "zero"),
        ("release", WRITER, "summary_claim_2", "证据不足"),
    ]
    assert submitted["generation"] == 3 and released["status"] == "retry"


def test_an_expired_claim_is_not_found(writer):
    """过期的摘要认领报告未找到

    认领已被收回时 SubmitResidentSummary 返回 instruction.not_found。

    :design: decision_d19b9909a674
    """
    with pytest.raises(InstructionError) as caught:
        _invoke(resident_isa.submit_resident_summary, {
            "claim_id": "summary_claim_gone", "summary": {"current_focus": "x"},
        }, caller=WRITER)
    assert caught.value.code == "instruction.not_found"


def test_without_a_registered_holder_no_one_writes_summaries(monkeypatch):
    """未登记摘要职责时没有人能写摘要

    resident-summary 未登记承担者时认领返回 instruction.not_found。

    :design: decision_d19b9909a674
    """
    from management_portal import application_targets

    monkeypatch.setattr(application_targets, "summary_resident", lambda: (_ for _ in ()).throw(KeyError("未登记")))
    with pytest.raises(InstructionError) as caught:
        _invoke(resident_isa.claim_resident_summary_jobs, {}, caller=WRITER)
    assert caught.value.code == "instruction.not_found"
