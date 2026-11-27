"""Family defaults share Resident quota intent without overwriting member settings."""
import pytest

from management_portal.execution import account_registrations
from management_portal.society_service import family_quota as quota
from tests.cases.test_family_quota import quota_family, _record  # noqa: F401
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import team  # noqa: F401
from tests.cases.family_test_support import set_role


def test_member_policies_are_local_after_migration(quota_family):
    first, second = _record(quota_family.steward), _record(quota_family.spare)
    first["execution_settings"] = {"auto_switch_on_quota": True, "emergency_switch_on_quota": True,
                                   "emergency_switch_order": ["codex:subscription_login"]}
    second["execution_settings"] = {"account_selection": "auto"}
    assert quota.auto_migration_enabled(first)
    assert quota.auto_migration_priority(first) == ["codex:subscription_login"]
    assert not quota.auto_migration_enabled(second)
    assert quota.auto_migration_priority(second) == []


def test_migration_resolves_prior_effective_state():
    from scripts.migrate_resident_quota_switches import old_switch
    modes = {"default": "auto", "static": "fixed"}
    def previous(settings, family=None, facade=False):
        return old_switch(settings, facade=facade, family_default=family, role_modes=modes)
    assert previous({"auto_switch_on_quota": True}, False)
    assert not previous({"auto_switch_on_quota": False}, True)
    assert previous({"account_selection": "fixed"}, True)
    assert not previous({"account_selection": "auto"}, False)
    assert previous({"backend_role": "missing"})
    assert not previous({"backend_role": "static"})
    assert not previous({}, None)
    assert not previous({"auto_switch_on_quota": True}, True, facade=True)
