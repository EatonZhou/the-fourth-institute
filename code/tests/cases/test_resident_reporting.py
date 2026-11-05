"""Reporting intent is persisted by Society and delivered on every input."""
import hashlib
import json
from pathlib import Path

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
    from management_portal.society_service.resident_input_queue_domain import _running_turn_prompt

    rid = resident_record["resident_id"]
    monkeypatch.setattr(prompts.resident_input_scope, "get", lambda *_a: {"scoped":True})
    job = {"user_message_id":"msg_report", "message":"请写汇报", "raw_message":"原始输入"}
    first = prompts.build_resident_prompt(resident_record, job)
    assert '"initiative": "off"' in first
    assert "明确要求汇报" in first and "本轮不得新建" in first and "原始输入" in first
    reporting.update(rid, "CASE-A", {"runtime_settings":{"reporting":{"initiative":"high","quality":"high"}}})
    later = prompts.build_resident_prompt(resident_record, job)
    assert '"initiative": "high"' in later and "绝不每轮固定生成" in later
    assert "独立 Resident" in later and "取消时停止" in later
    appended = _running_turn_prompt(resident_record, {**job, "raw_message":"继续完成已委托的解释"})
    assert appended["actual_sent_prompt"].split("</reporting-policy>")[0] == later.split("</reporting-policy>")[0]
    assert "继续完成已委托的解释" in appended["actual_sent_prompt"]
    assert "已委托的高质量解释即使采用简洁文字" in later
    assert "简单事实可直接简答" in later and "这一分类不取消上述已委托成果的质量要求" in later
    assert "--quality" in later and "--previous-review-id" in later
    reporting.update(rid, "CASE-A", {"runtime_settings":{"reporting":{"initiative":"off","quality":"low"}}})
    lowered = _running_turn_prompt(resident_record, job)["actual_sent_prompt"]
    assert '"quality": "low"' in lowered and "汇报功能：不开启" in lowered
    assert "汇报使用清晰的 Markdown" not in lowered
    assert "高质量汇报与解释成果须交独立 Resident 审查" not in lowered


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
    assert "高质量汇报与解释成果须交独立 Resident 审查" in materialized
    assert "home_explanation_authoring.md" in materialized and "build_explanation_package.py" in materialized
    from management_portal.society_service.resident_input_queue_domain import _running_turn_prompt
    appended = _running_turn_prompt(records.find_resident(member), {
        "user_message_id":leaf,"raw_message":"解释一个新问题"})["actual_sent_prompt"]
    assert appended.split("</reporting-policy>")[0] == materialized.split("</reporting-policy>")[0]


def test_non_home_prompt_links_existing_authoring_entry_for_concise_high_artifact(resident_record, tmp_path):
    from scripts.build_explanation_package import build

    rid = resident_record["resident_id"]
    reporting.update(rid, "CASE-A", {"runtime_settings":{"reporting":{"initiative":"high","quality":"high"}}})
    text = prompts.build_resident_prompt(resident_record, {"message":"完成之前委托的解释"})
    policy = json.loads(text.split("<reporting-policy>\n", 1)[1].split("\n", 1)[0])
    assert policy["initiative"] == "high" and policy["quality"] == "high"
    code_root = Path(__file__).resolve().parents[2]
    for relative in ("management_portal/static/home_explanation_authoring.md",
                     "scripts/build_explanation_package.py", "scripts/submit_explanation_review.py"):
        entry = code_root / relative
        assert entry.is_file() and str(entry) in text
    # The advertised authoring contract also supports a short explanation without
    # imposing a page, interaction or video; its fixed quality still requires review.
    content = "# 两种策略\n在同一收益表上比较各自最坏情况下的结果。\n"
    (tmp_path / "explanation.md").write_text(content, encoding="utf-8")
    manifest = build(tmp_path, "workspace_0123456789ab/file/concise", title="简洁解释",
                     supplement="explanation.md", quality=policy["quality"])
    assert manifest["quality"] == "high"
    assert manifest["supplement"]["sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert "interaction" not in manifest and "video_plan" not in manifest
    assert "submitted、可打开或媒体 ready 均不表示通过" in text


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


@pytest.fixture
def reporting_upgrade_database(resident_record):
    from uuid import uuid4
    from psycopg import sql
    from management_common.portal_database import portal_database
    database = portal_database(schema="reporting_probe_" + uuid4().hex[:8])
    database.write(lambda c: c.execute(
        "CREATE TABLE resident_execution_configuration (resident_id TEXT PRIMARY KEY, "
        "execution_settings_json JSONB, harness_config_json TEXT); "
        "INSERT INTO resident_execution_configuration VALUES ('resident_probe', '{\"model\":\"keep\"}', '{}')"))
    try:
        yield database
    finally:
        database.write(lambda c: c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(database.schema))))
        database.close()


@pytest.mark.parametrize("metadata", ["absent", "empty", "different"])
def test_reporting_upgrade_optional_metadata(reporting_upgrade_database, metadata):
    from scripts.migrate_resident_reporting import migrate
    database = reporting_upgrade_database
    expected = "tfi.lineage-data.v999" if metadata == "different" else ""
    if metadata != "absent":
        database.write(lambda c: c.execute("CREATE TABLE lineage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"))
    if metadata == "different":
        database.write(lambda c: c.execute("INSERT INTO lineage_meta VALUES ('schema', %s)", (expected,)))
    preview = migrate(database)
    assert preview == {"schema":expected, "changed":True, "default":reporting.DEFAULT, "committed":False}
    with database.read() as c:
        assert not c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() "
                            "AND table_name='resident_execution_configuration' AND column_name='reporting_json'").fetchone()
    assert migrate(database, apply=True) == {**preview, "committed":True}
    with database.read() as c:
        row = c.execute("SELECT * FROM resident_execution_configuration").fetchone()
        assert row["reporting_json"] is None and row["harness_config_json"] == "{}"
        assert row["execution_settings_json"] == {"model":"keep"}
        if metadata != "absent":
            rows = c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchall()
            assert [row["value"] for row in rows] == ([expected] if expected else [])


def test_reporting_upgrade_existing_text_issues_no_ddl(reporting_upgrade_database):
    from scripts.migrate_resident_reporting import migrate
    from tests.cases.database_test_support import QueryGuard
    database = reporting_upgrade_database
    migrate(database, apply=True)
    policy = '{"initiative":"high","quality":"high"}'
    database.write(lambda c: c.execute("UPDATE resident_execution_configuration SET reporting_json=%s", (policy,)))
    with database.transaction() as c:
        result = reporting.install_schema(QueryGuard(c, forbidden=("alter table",)))
        assert result["changed"] is False and result["schema"] == ""
        assert c.execute("SELECT reporting_json FROM resident_execution_configuration").fetchone()["reporting_json"] == policy


@pytest.mark.parametrize("damage", ["missing_table", "view", "missing_field", "wrong_field_type", "wrong_reporting_type", "not_nullable"])
def test_reporting_upgrade_rejects_real_structure_errors(reporting_upgrade_database, damage):
    from scripts.migrate_resident_reporting import migrate
    database = reporting_upgrade_database
    commands = {
        "missing_table":"DROP TABLE resident_execution_configuration",
        "view":"ALTER TABLE resident_execution_configuration RENAME TO configuration_source; "
               "CREATE VIEW resident_execution_configuration AS SELECT * FROM configuration_source",
        "missing_field":"ALTER TABLE resident_execution_configuration DROP COLUMN harness_config_json",
        "wrong_field_type":"ALTER TABLE resident_execution_configuration ALTER COLUMN resident_id TYPE INTEGER USING 1",
        "wrong_reporting_type":"ALTER TABLE resident_execution_configuration ADD COLUMN reporting_json INTEGER",
        "not_nullable":"ALTER TABLE resident_execution_configuration ADD COLUMN reporting_json TEXT NOT NULL DEFAULT '{}'",
    }
    database.write(lambda c: c.execute(commands[damage]))
    with pytest.raises(ValueError, match="configuration|Reporting column"):
        migrate(database, apply=True)


def test_reporting_upgrade_accepts_fresh_owner_jsonb_without_ddl(reporting_upgrade_database):
    """新安装的正式对象列和旧库增列入口兼容，不改类型、策略或其他配置"""
    from management_portal.society_service import resident_storage
    from tests.cases.database_test_support import QueryGuard
    database = reporting_upgrade_database
    with database.transaction() as c:
        c.execute("DROP TABLE resident_execution_configuration")
        c.execute("CREATE TABLE residents (resident_id TEXT PRIMARY KEY)")
        resident_storage.create_table(c, "resident_execution_configuration", resident_storage.CONFIGURATION_FIELDS)
        c.execute("INSERT INTO residents VALUES ('resident_probe')")
        c.execute("INSERT INTO resident_execution_configuration (resident_id,reporting_json) "
                  "VALUES ('resident_probe','{\"initiative\":\"high\",\"quality\":\"high\"}')")
        before = dict(c.execute("SELECT * FROM resident_execution_configuration").fetchone())
        assert reporting.install_schema(QueryGuard(c, forbidden=("alter table",)))["changed"] is False
        assert dict(c.execute("SELECT * FROM resident_execution_configuration").fetchone()) == before
