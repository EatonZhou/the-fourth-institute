"""Family defaults share Resident quota intent without overwriting member settings."""
import pytest

from management_portal.execution import account_registrations
from management_portal.society_service import family_quota as quota
from tests.cases.test_family_quota import quota_family, _record  # noqa: F401
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import team  # noqa: F401
from tests.cases.family_test_support import set_role


def test_family_default_overrides_selection_mode_and_member_override_wins(quota_family):
    record = _record(quota_family.steward)
    record["execution_settings"] = {"account_selection": "fixed"}
    assert quota.auto_migration_enabled(record)
    quota.get_lineage_data_store().database.write(lambda c: c.execute(
        "UPDATE families SET auto_migrate=false WHERE family_id=%s", ("fbi_test",)))
    record["execution_settings"]["account_selection"] = "auto"
    assert not quota.auto_migration_enabled(record)
    record["execution_settings"]["auto_switch_on_quota"] = True
    assert quota.auto_migration_enabled(record)
    record["execution_settings"]["auto_switch_on_quota"] = False
    assert not quota.auto_migration_enabled(record)


def test_family_saved_priority_survives_and_empty_uses_site(quota_family, monkeypatch):
    record = _record(quota_family.steward)
    monkeypatch.setattr(account_registrations, "resolve_role", lambda _: (
        {"backend_priority": ["claude:subscription_login"], "account_selection": "auto"}, False))
    assert quota.auto_migration_priority(record) == ["claude:subscription_login"]
    quota.get_lineage_data_store().database.write(lambda c: c.execute(
        "UPDATE families SET auto_migrate_priority=%s WHERE family_id=%s",
        (["codex:subscription_login"], "fbi_test")))
    assert quota.auto_migration_priority(record) == ["codex:subscription_login"]
    record["execution_settings"] = {"auto_switch_on_quota": True}
    assert quota.auto_migration_priority(record) == ["claude:subscription_login"]


def test_family_exhaustion_uses_shared_handoff(quota_family, monkeypatch):
    f = quota_family
    f.accounts[0]["quota_reserve"].update(used_up=True)
    calls = []
    monkeypatch.setattr(quota, "_ensure_quota_switch", lambda r, a: calls.append(r["resident_id"]) or True)
    assert quota.ensure_role_reserve(_record(f.steward), f.accounts)
    assert calls == [f.steward]


def test_nested_executor_inherits_its_direct_family_only(quota_family):
    f = quota_family
    set_role(f.spare, family_id="fbi_inner", role="member", actor=ACTOR)
    quota.get_lineage_data_store().database.write(lambda c: c.execute(
        "UPDATE families SET auto_migrate=false WHERE family_id=%s", ("fbi_test",)))
    assert not quota.auto_migration_enabled(_record(f.steward))
    assert quota.auto_migration_enabled(_record(f.spare))
