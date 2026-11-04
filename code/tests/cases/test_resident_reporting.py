"""Reporting intent is persisted by Society and delivered on every input."""
import pytest

from management_portal.society_service import resident_reporting as reporting
from management_portal.society_service import resident_record_domain as records
from management_portal.society_service import resident_prompt_domain as prompts
from management_portal.society_service import resident_protocol, family_facade, resident_access
from management_portal.society_service.errors import LineageConflictError
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import team, instruction  # noqa: F401
from tests.cases.test_cross_owner_storage_integration import owners  # noqa: F401


@pytest.mark.parametrize("value", [None, {}, {"initiative":"off"},
    {"initiative":"all","quality":"medium"}, {"initiative":True,"quality":"high"},
    {"initiative":"off","quality":"all"}, {"initiative":"off","quality":"low","extra":1}])
def test_invalid_policy_is_rejected(value):
    with pytest.raises(ValueError):
        reporting.normalize(value)


def test_default_roundtrip_cas_and_no_model_operation(resident_record, monkeypatch):
    rid = resident_record["resident_id"]
    before = resident_protocol.get_resident_runtime_settings(rid, "CASE-A")
    assert before["runtime_settings"]["reporting"] == {"initiative":"off","quality":"medium"}
    assert before["reporting_effective"]["configured"] is False
    before = reporting.update(rid, "CASE-A", {"expected_revision":before["settings_revision"],
        "runtime_settings":{"reporting":dict(reporting.DEFAULT)}})
    assert before["reporting_effective"]["configured"] is True
    from management_portal.society_service import resident_execution_domain as engine
    monkeypatch.setattr(engine, "change_resident_runtime_settings", lambda *_a, **_k: pytest.fail("no Provider update"))
    policy = {"initiative":"high","quality":"high"}
    saved = resident_protocol.update_resident_runtime_settings(rid, "CASE-A", {
        "runtime_settings":{"reporting":policy}, "expected_revision":before["settings_revision"]})
    assert saved["runtime_settings"]["reporting"] == policy
    assert saved["reporting_effective"]["configured"] is True
    assert saved["next_turn_fields"] == ["reporting"]
    assert saved["application"]["settings_revision"] == saved["settings_revision"]
    application = next(item for item in saved["application"]["items"] if item["path"] == "reporting")
    assert application["status"] == "next_turn" and application["delivery"] == "unknown"
    assert records.find_resident(rid)["reporting"] == policy
    with pytest.raises(LineageConflictError):
        resident_protocol.update_resident_runtime_settings(rid, "CASE-A", {
            "runtime_settings":{"reporting":policy}, "expected_revision":before["settings_revision"]})
    same = resident_protocol.update_resident_runtime_settings(rid, "CASE-A", {
        "runtime_settings":{"reporting":policy}, "expected_revision":saved["settings_revision"]})
    assert same["next_turn_fields"] == []


def test_each_later_input_and_scoped_input_receives_current_policy(resident_record, monkeypatch):
    rid = resident_record["resident_id"]
    monkeypatch.setattr(prompts.resident_input_scope, "get", lambda *_a: {"scoped":True})
    job = {"user_message_id":"msg_report", "message":"请写汇报", "raw_message":"原始输入"}
    first = prompts.build_resident_prompt(resident_record, job)
    assert '"initiative": "off"' in first
    assert "用户明确请求" in first and "原始输入" in first
    reporting.update(rid, "CASE-A", {"runtime_settings":{"reporting":{"initiative":"high","quality":"high"}}})
    later = prompts.build_resident_prompt(resident_record, job)
    assert '"initiative": "high"' in later and "绝不每轮固定生成" in later
    assert "独立 Resident" in later and "取消时停止" in later


def test_facade_only_permissions_and_actual_relay_member(team, monkeypatch):
    from tests.cases.family_test_support import set_role
    from tests.cases.test_cross_owner_storage_integration import ACTOR
    from management_portal.society_service import resident_conversation_stream
    facade, member = team.source["resident_id"], team.target["resident_id"]
    set_role(facade, family_id="fbi_reporting", actor=ACTOR)
    set_role(member, family_id="fbi_reporting", role="member", actor=ACTOR)
    # The caller has no independent access to any possible Router settings.
    original = resident_access.has_resident_workspace_authority
    monkeypatch.setattr(resident_access, "has_resident_workspace_authority",
        lambda record, actor: record["resident_id"] == facade and original(record, actor))
    before = instruction(team, "GetResidentSettings", {"resident_id":facade})
    assert before.status == "committed", before.error
    assert before.result["settings"] == {"reporting":{"initiative":"off","quality":"medium"}}
    changed = instruction(team, "UpdateResidentSettings", {"resident_id":facade,
        "expected_revision":before.result["settings_revision"],
        "changes":{"reporting":{"initiative":"medium","quality":"high"}}})
    assert changed.status == "committed", changed.error
    repo = resident_conversation_stream.get_repository()
    root, leaf = "msg_" + "a" * 32, "msg_" + "b" * 32
    for rid, mid in ((facade,root),(member,leaf)):
        repo.append_message(rid, {"message_id":mid,"role":"user","author_id":ACTOR,
            "content_parts":[{"type":"text","text":"report"}],
            **({"business_ref":family_facade.relay_mark(facade,root)} if rid==member else {})})
        repo.set_input_state(rid, mid, "queued")
    family_facade.complete_relay((facade,root), member,leaf)
    policy = reporting.effective(member,leaf)
    assert policy["source_resident_id"] == facade and policy["quality"] == "high"
    assert policy["initiative"] == "medium" and policy["source"] == "family_facade"
    assert reporting.effective(member)["initiative"] == "off"
    materialized = prompts.build_resident_prompt(records.find_resident(member), {
        "user_message_id":leaf,"message":"请汇报"})
    assert '"source_resident_id": "'+facade+'"' in materialized


def test_unknown_source_is_explicit(monkeypatch):
    monkeypatch.setattr(records, "find_resident", lambda *_a: None)
    assert reporting.effective("resident_missing")["state"] == "unknown"
    assert "不推断个人 AMA" in reporting.prompt({"resident_id":"resident_missing"}, {} )


def test_policy_and_execution_settings_share_cas(resident_record):
    rid = resident_record["resident_id"]
    before = reporting.read(rid, "CASE-A")
    result = reporting.update(rid, "CASE-A", {"expected_revision":before["settings_revision"],
        "runtime_settings":{"allow_subagents":True,"reporting":{"initiative":"low","quality":"low"}}})
    assert result["runtime_settings"]["allow_subagents"] is True
    assert result["runtime_settings"]["reporting"]["initiative"] == "low"
    assert set(result["next_turn_fields"]) == {"allow_subagents","reporting"}


def test_exact_temporary_ama_loan_does_not_require_router_access(team, monkeypatch):
    from tests.cases.family_test_support import set_role
    from tests.cases.permission_test_support import enable, Fact
    from tests.cases.isa_test_support import run
    from management_portal.permission_system import temporary
    facade, router = team.source["resident_id"], team.target["resident_id"]
    set_role(facade, family_id="fbi_reporting_loan", actor="CASE-A")
    set_role(router, family_id="fbi_reporting_loan", role="router", actor="CASE-A")
    for rid in (facade, router):
        record = records.find_resident(rid)
        record["personal_owner_id"] = "report-owner"
        records.save_resident_record(record)
    permissions = enable(monkeypatch)
    permissions.users("report-owner", "report-reader", category="intern")
    permissions.place(Fact("resident", facade), Fact("resident", router))
    permissions.grant("report-owner", facade, "@admin")
    scheme, engine, _instance = permissions.implementation.current()
    temporary.issue(engine, scheme, issuer="report-owner", recipient="report-reader",
                    a="@contributor", resource=facade, seconds=120)
    before = run("GetResidentSettings", {"resident_id":facade}, actor="report-reader")
    assert before.status == "committed", before.error
    assert before.result["runtime_settings_access"] == "unavailable"
    saved = run("UpdateResidentSettings", {"resident_id":facade,
        "expected_revision":before.result["settings_revision"],
        "changes":{"reporting":{"initiative":"medium","quality":"high"}}}, actor="report-reader")
    assert saved.status == "committed", saved.error
    assert saved.result["settings"]["reporting"]["quality"] == "high"
    denied = run("GetResidentSettings", {"resident_id":router}, actor="report-reader")
    assert denied.status != "committed"


def test_old_database_upgrade_preview_apply_and_idempotency(resident_record):
    from management_portal.society_service import resident_catalog
    from scripts.migrate_resident_reporting import migrate
    store = resident_catalog.get_catalog().state_store
    rid = resident_record["resident_id"]
    before = store.read_resident_execution_settings(rid)
    store.database.write(lambda c: c.execute(
        "ALTER TABLE resident_execution_configuration DROP COLUMN reporting_json"))
    assert migrate(store.database)["committed"] is False
    with store.database.read() as c:
        assert not c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() "
            "AND table_name='resident_execution_configuration' AND column_name='reporting_json'").fetchone()
    applied = migrate(store.database, apply=True)
    assert applied["changed"] is True and applied["committed"] is True
    assert migrate(store.database, apply=True)["changed"] is False
    # The existing installed-db converter delegates to this Family converter.
    from scripts.migrate_family_chat_controls import convert
    assert store.database.write(convert)["changed"] is False
    assert store.read_resident_execution_settings(rid) == before
    assert reporting.read(rid,"CASE-A")["runtime_settings"]["reporting"] == reporting.DEFAULT


def test_seed_new_installation_has_the_same_reporting_column(resident_record):
    from pathlib import Path
    from management_portal.society_service import resident_catalog
    source = Path(__file__).resolve().parents[3]/"instance/example_instance/seed/database/distribution.sql"
    sql = source.read_text()
    start = sql.index("CREATE TABLE lineage.resident_execution_configuration (")
    ddl = sql[start:sql.index(";", start)+1].replace(
        "CREATE TABLE lineage.resident_execution_configuration", "CREATE TEMP TABLE reporting_seed_probe")
    with resident_catalog.get_catalog().state_store.database.transaction() as c:
        c.execute(ddl)
        assert c.execute("SELECT data_type FROM information_schema.columns "
            "WHERE table_name='reporting_seed_probe' AND column_name='reporting_json'").fetchone()["data_type"] == "text"
