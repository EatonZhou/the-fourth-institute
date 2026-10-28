"""Family account switching is explicit, persistent and independent of input routing."""
import pytest

from management_portal.society_service import family_quota, family_storage, resident_execution_work
from management_portal.society_service import resident_input_queue_domain, resident_restart_domain
from management_portal.society_service import resident_account_policy as policy
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.family_test_support import set_role
from tests.cases.isa_test_support import ACTOR, ok, run
from tests.cases.test_family_familize import lineage, charters, graph, resident, future_creation_store  # noqa: F401
from tests.cases.test_family_quota import quota_family, team, owners, _account, _record  # noqa: F401


def _switch(family_id, enabled):
    get_lineage_data_store().database.write(lambda c: c.execute(
        "UPDATE families SET auto_migrate=%s WHERE family_id=%s", (enabled, family_id)))


def test_policy_defaults_on_and_survives_unrelated_updates(lineage, charters):
    member = resident(lineage, "Member")
    family_id = ok("Familize", {"lineage_id": lineage, "resident_ids": [member], "title": "Quota"})["family_id"]
    def config():
        return next(b["family"] for b in graph(lineage)["function_blocks"] if b["id"] == family_id)
    assert config()["auto_migrate"] is True
    ok("UpdateFamily", {"lineage_id": lineage, "family_id": family_id, "auto_migrate": False})
    assert config()["auto_migrate"] is False
    ok("UpdateFamily", {"lineage_id": lineage, "family_id": family_id, "goal": "New goal"})
    assert config()["auto_migrate"] is False
    assert run("UpdateFamily", {"lineage_id": lineage, "family_id": family_id, "auto_migrate": "false"}).status != "committed"
    ok("UpdateFamily", {"lineage_id": lineage, "family_id": family_id, "auto_migrate": True})
    assert config()["auto_migrate"] is True


@pytest.mark.parametrize("role", ["member", "router", "steward"])
@pytest.mark.parametrize("enabled", [True, False])
def test_five_hour_limit_migrates_all_family_executors_only_when_enabled(quota_family, monkeypatch, role, enabled):
    f = quota_family
    if role == "steward":
        f.member = f.steward
    else:
        set_role(f.member, family_id="fbi_test", role=role, actor=ACTOR)
    resident_input_queue_domain.commit_resident_intake_state(_record(f.member), "open")
    _switch("fbi_test", enabled)
    calls = []
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", lambda *a, **k: calls.append((a, k)) or {})
    # A direct workflow input reaches this same admission boundary; no facade relay is needed.
    f.accounts[0] = _account("one", 80, short=0)
    assert family_quota.ensure_role_reserve(_record(f.member), f.accounts) is enabled
    assert len(calls) == int(enabled)
    if enabled:
        payload = calls[0][0][2]
        assert payload["target_account"] == "two" and payload["context_strategy"] == "compact"
        assert family_storage.auto_migration_enabled(_record(f.member))


@pytest.mark.parametrize("boundary", ["member_paused", "family_paused", "admitted", "stop_requested", "ordinary"])
def test_member_migration_preserves_execution_and_pause_boundaries(quota_family, monkeypatch, boundary):
    f = quota_family
    if boundary != "ordinary":
        set_role(f.member, family_id="fbi_test", role="member", actor=ACTOR)
    resident_input_queue_domain.commit_resident_intake_state(_record(f.member), "open")
    if boundary in {"member_paused", "family_paused"}:
        rid = f.member if boundary == "member_paused" else f.facade
        resident_input_queue_domain.commit_resident_intake_state(_record(rid), "closed")
    elif boundary in {"admitted", "stop_requested"}:
        monkeypatch.setattr(resident_execution_work, "read", lambda r: {"status": "running", "execution_id": "exec_busy", "stop_requested": boundary == "stop_requested"})
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", lambda *a, **k: pytest.fail("must not restart"))
    assert not family_quota.ensure_role_reserve(_record(f.member), f.accounts)


def test_short_window_routes_to_quota_review(quota_family):
    f = quota_family
    f.accounts[0] = _account("one", 80, short=1)
    result = family_quota.relay_message(f.facade, f.message, f.member, ACTOR)
    assert result["status"] == "waiting_for_quota_review"


def test_direct_member_pending_input_migrates_without_replaying_old_work(quota_family, monkeypatch):
    from management_portal.society_service import resident_domain
    f = quota_family
    set_role(f.member, family_id="fbi_test", role="member", actor=ACTOR)
    resident_input_queue_domain.commit_resident_intake_state(_record(f.member), "open")
    calls = []
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", lambda *a, **k: calls.append(a[2]) or {})
    # Idle members are not periodically migrated.
    assert family_quota.reconcile_role_reserves([_record(f.member)]) == 0
    resident_domain.deliver_resident_message_content(f.member, ACTOR, "Workflow work", message_id="msg_abcdef1234567890")
    before = resident_execution_work.read(_record(f.member))
    assert before["user_message_id"] == "msg_abcdef1234567890"
    assert family_quota.reconcile_role_reserves([_record(f.member)]) == 1
    assert resident_execution_work.read(_record(f.member)) == before
    assert calls[0]["resume_failed_input"] is False


def test_cross_provider_uses_target_default_model(monkeypatch):
    calls = []
    class Target:
        def validate_settings(self, route, model):
            calls.append((route, model))
            return "target-default", ""
    monkeypatch.setitem(policy.AGENT_PROVIDERS, "codex", Target())
    assert policy.migration_target_options(_account("two", 90), "claude") == {
        "target_provider": "codex", "target_account": "two", "context_strategy": "compact",
        "model": "target-default", "reasoning_effort": ""}
    assert calls == [("two", "")]
    assert "model" not in policy.migration_target_options(_account("two", 90), "codex")


def test_five_hour_only_account_is_a_valid_target():
    account = _account("claude_two", 90, provider="claude")
    account["quota_reserve"]["windows"] = [{"window": "five_hour", "remaining_percent": 80}]
    assert policy.sufficient_reserve(account)
    account["quota_reserve"]["windows"][0]["remaining_percent"] = 0
    assert not policy.sufficient_reserve(account)


def test_migration_is_explicit_default_on_and_idempotent(quota_family):
    from management_portal.society_service.state_store import lineage_data_schema_matches
    from scripts.migrate_family_auto_migration import upgrade
    database = get_lineage_data_store().database
    with database.transaction() as c:
        c.execute("ALTER TABLE families DROP COLUMN auto_migrate")
        c.execute("UPDATE lineage_meta SET value='tfi.lineage-data.v61' WHERE key='schema'")
        assert not lineage_data_schema_matches(c)
        assert upgrade(c)["schema"] == "tfi.lineage-data.v62"
        assert lineage_data_schema_matches(c)
        assert c.execute("SELECT auto_migrate FROM families WHERE family_id='fbi_test'").fetchone()["auto_migrate"] is True
        c.execute("UPDATE families SET auto_migrate=false WHERE family_id='fbi_test'")
        assert upgrade(c)["previous_schema"] == "tfi.lineage-data.v62"
        assert c.execute("SELECT auto_migrate FROM families WHERE family_id='fbi_test'").fetchone()["auto_migrate"] is False
