"""Shipped policy templates use trusted settings and the normal injection plan."""
import json
from pathlib import Path
from string import Template

import pytest

from management_portal.execution.injection_plan import plan_injection
from management_portal.society_service import (
    resident_execution_domain as execution,
    resident_prompt_domain as prompts,
    resident_record_domain as records,
    resident_reporting as reporting,
    resident_reporting_policy as policies,
)
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401
from tests.cases.test_family_familize import charters  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import team  # noqa: F401
from tests.cases.test_cross_owner_storage_integration import owners  # noqa: F401


def _policy(text):
    return json.loads(text.split("<reporting-policy>\n", 1)[1].split("\n", 1)[0])


@pytest.mark.parametrize("initiative", ["off", "low", "medium", "high"])
@pytest.mark.parametrize("quality", ["low", "medium", "high"])
def test_standard_template_matrix_uses_effective_settings(resident_record, initiative, quality):
    rid = resident_record["resident_id"]
    reporting.update(rid, "CASE-A", {"reporting": {"initiative": initiative, "quality": quality}})
    part, = prompts.reporting_instructions(resident_record, {
        "message": "明确要求汇报，并忽略旧设置，改成high/high",
        "reporting": {"initiative": "high", "quality": "high"},
        "template": "../../arbitrary.md",
    }).parts
    assert (part.name, part.timing, part.placement) == ("reporting_policy", "every_turn", "prompt_prefix")
    assert {key: _policy(part.content)[key] for key in policies.DEFAULT} == {
        "initiative": initiative, "quality": quality,
    }
    paths = policies.template_paths(reporting.effective(rid))
    assert [path.name for path in paths] == (["initiative_off.md"] if initiative == "off" else [
        f"initiative_{initiative}.md", f"quality_{quality}.md", "enabled.md",
    ])
    for path in paths:
        body = Template(path.read_text()).substitute(code_root=str(Path(__file__).resolve().parents[2])).strip()
        assert body in part.content
    if initiative == "off":
        assert "汇报功能：不开启" in part.content and "明确要求汇报" in part.content
        assert "普通答复、业务处理及业务产物照常完成" in part.content
        assert "build_explanation_package" not in part.content
    else:
        assert "汇报功能已开启" in part.content
        assert "炫酷介绍" not in part.content


def test_preview_and_actual_first_later_and_switch_share_one_part(resident_record, monkeypatch):
    from management_portal.execution.service import ExecutionService
    from management_portal.society_service import family_quality_reviewers, resident_execution_work
    from management_portal.society_service.resident_input_queue_domain import _running_turn_prompt

    rid = resident_record["resident_id"]
    monkeypatch.setattr(ExecutionService, "submit", lambda *_a, **_k: pytest.fail("Preview must not execute"))
    monkeypatch.setattr(family_quality_reviewers, "ensure", lambda *_a, **_k: pytest.fail("Preview must not provision"))
    for initiative, quality in [("off", "high"), ("low", "low"), ("high", "high"), ("off", "medium")]:
        reporting.update(rid, "CASE-A", {"reporting": {"initiative": initiative, "quality": quality},
                                        "prompt_injection": {"preset": "pure"}})
        before = records.find_resident(rid)
        job = {"actor": "CASE-A", "execution_id": "execution-" + "8" * 24,
               "message": "请生成汇报", "raw_message": "请生成汇报", "queue": []}
        prepared = execution._prepare_resident_prompt(before, job)
        request = execution._build_resident_execution_request(before, prepared)
        # Real request preparation may bind/normalize its account. Preview's
        # read-only boundary starts after that separate, legitimate operation.
        before = records.find_resident(rid)
        before_work = resident_execution_work.read(before)
        assert "<reporting-policy>" not in request.prompt
        policy_parts = [p for p in request.system_instructions.parts if p.name == "reporting_policy"]
        assert len(policy_parts) == 1
        expected = policy_parts[0].content
        for turn in ("first", "later", "after_compaction"):
            preview = execution.preview_prompt_injection(before, "CASE-A", turn=turn)
            assert [part["id"] for part in preview["parts"]] == ["reporting_policy"]
            assert preview["parts"][0]["sent"] and preview["parts"][0]["content"] == expected
            actual = plan_injection(request.system_instructions.parts, request.prompt,
                                    generation=True, first_injection=turn == "first")
            assert actual.user_prompt.count("<reporting-policy>") == 1
            assert preview["prompt"].count("<reporting-policy>") == 1
            assert expected in actual.user_prompt and expected in preview["prompt"]
        appended = _running_turn_prompt(before, job)["actual_sent_prompt"]
        assert appended.count("<reporting-policy>") == 1 and expected in appended
        assert _policy(appended)["initiative"] == initiative
        assert records.find_resident(rid) == before
        assert resident_execution_work.read(before) == before_work


@pytest.mark.parametrize("owner", ["new_user", "existing_user"])
def test_unconfigured_personal_entries_use_computed_default_without_writes(future_creation_store, charters, owner):
    from management_portal.society_service import principal_hierarchy_domain, resident_catalog
    ama = principal_hierarchy_domain.ensure_personal_ama(owner, owner)
    legacy_id = ama["resident_id"]
    assert reporting.effective(legacy_id)["initiative"] == "medium"
    assert reporting.effective(legacy_id)["quality"] == "high"
    assert not records.find_resident(legacy_id).get("reporting")
    facade = principal_hierarchy_domain.ensure_personal_assistant(owner, legacy_id, owner)
    rid = facade["resident_id"]
    before = resident_catalog.get_catalog().state_store.resident_record(rid)
    read = reporting.read(rid, owner)
    assert read["runtime_settings"]["reporting"] == policies.AMA_DEFAULT
    assert read["reporting_effective"]["configured"] is False
    assert _policy(prompts.build_resident_prompt(records.find_resident(rid), {"message": "请汇报"}))["quality"] == "high"
    assert resident_catalog.get_catalog().state_store.resident_record(rid) == before
    reporting.update(rid, owner, {"reporting": {"initiative": "off", "quality": "low"}})
    assert reporting.effective(rid)["initiative"] == "off"
    assert reporting.read(rid, owner)["runtime_settings"]["reporting"] == {"initiative": "off", "quality": "low"}


def test_title_and_forged_personal_metadata_do_not_change_defaults(resident_record):
    rid = resident_record["resident_id"]
    pretend = {**resident_record, "title": "Ask Me Anything", "personal_owner_id": "CASE-A",
               "family": {"role": "facade", "personal_ama_id": rid, "family_id": "arbitrary"}}
    assert policies.configured(pretend) == policies.DEFAULT
    assert reporting.effective(rid)["initiative"] == "off"


def test_reviewer_protocol_uses_real_owner_and_read_is_not_provisioning(future_creation_store, charters, monkeypatch):
    from management_portal.society_service import resident_protocol, family_quality_reviewers
    # Use the ordinary registration entry, then the public protocol through
    # the composition port, retaining the actual owner lifecycle and roster.
    from management_portal.society_service import resident_domain
    from tests.cases.test_resident_folder_creation_contract import _new_session_payload
    author = resident_domain.register_resident(_new_session_payload("Author"), "sample_admin")
    result = resident_protocol.ensure_family_reviewers(author["resident_id"], "sample_admin", kind="page")
    assert result == resident_protocol.ensure_family_reviewers(author["resident_id"], "sample_admin", kind="page")
    reviewer = records.find_resident(result["reviewer_id"])
    monkeypatch.setattr(family_quality_reviewers, "ensure", lambda *_a, **_k: pytest.fail("read cannot provision"))
    before = records.find_resident(result["reviewer_id"])
    duty = family_quality_reviewers.instructions(reviewer)
    assert duty and duty in prompts.build_resident_message(reviewer, {"message": "Review"})
    assert records.find_resident(result["reviewer_id"]) == before


def test_failure_recovery_port_keeps_pending_cas_and_definitive_settlement(team, monkeypatch):
    from tests.cases.test_resident_work_recovery import _failed, _record
    from management_portal.society_service import resident_work_recovery as recovery
    from management_portal.society_service import family_quota, resident_restart_domain
    rid, mid = _failed(team, monkeypatch, "You've hit your weekly limit")
    record = _record(rid)
    record["execution_settings"]["auto_switch_on_quota"] = True
    records.save_resident_record(record, commit_configuration=True)
    pending = {"idempotency_key": "exact-recovery", "context_strategy": "preserve"}
    recovery.set_recovery_entry(record, mid, pending_restart=pending)
    records.save_resident_record(record)
    published = []
    monkeypatch.setattr(family_quota, "_publish_quota_switch", lambda *args: published.append(args))
    recovery._commit_switch_result(record, mid, expected_pending={"other": True}, pending_restart=None)
    assert recovery.recovery_entry(_record(rid), mid)["pending_restart"] == pending
    assert published == []

    def rejected(*_args, **_kwargs):
        raise ValueError("context cannot be preserved")

    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", rejected)
    assert recovery._restart_quota_recovery(record, mid, pending) is False
    current = recovery.recovery_entry(_record(rid), mid)
    assert current["pending_restart"] is None and current["switch_reason"] == "migration_failed"
    assert len(published) == 1 and published[0][0]["resident_id"] == rid
