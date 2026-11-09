"""Runtime account policy travels through the same formal settings interface as the UI.

:module: resident.session
:design: decision_ccbb1cac24aa
"""
from copy import deepcopy

import pytest

from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import resident_backend, resident_catalog
from management_portal.society_service.errors import LineageConflictError
from management_portal.society_service.isa import residents
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401
from tests.cases.test_prompt_injection import _invocation


def test_account_selection_isa_roundtrip_preserves_binding_and_quota_override(resident_record):
    rid = resident_record["resident_id"]
    read = lambda: residents.get_resident_settings(_invocation("GetResidentSettings", resident_id=rid))
    initial = read()
    binding = deepcopy(resident_backend.session_record(resident_record))
    for mode in ("auto", "fixed"):
        before = read()
        changes = {"account_selection": mode}
        if mode == "auto":
            changes["auto_switch_on_quota"] = False
        saved = residents.update_resident_settings(_invocation("UpdateResidentSettings", resident_id=rid,
            expected_revision=before["settings_revision"], changes=changes))
        reread = read()
        assert saved["settings"]["account_selection"] == mode
        assert reread["settings"] == saved["settings"]
        assert reread["settings_revision"] == saved["settings_revision"]
        assert reread["settings"]["auto_switch_on_quota"] is False
        assert resident_catalog.get_catalog().state_store.read_resident_execution_settings(rid)["account_selection"] == mode
        assert resident_backend.session_record(resident_record) == binding
        unchanged = residents.update_resident_settings(_invocation("UpdateResidentSettings", resident_id=rid,
            expected_revision=saved["settings_revision"], changes={"account_selection": mode}))
        assert unchanged["settings_revision"] == saved["settings_revision"]
    with pytest.raises(LineageConflictError):
        residents.update_resident_settings(_invocation("UpdateResidentSettings", resident_id=rid,
            expected_revision=initial["settings_revision"], changes={"account_selection": "auto"}))
    assert read()["settings"]["account_selection"] == "fixed"


@pytest.mark.parametrize("value", [None, True, 1, {}, "automatic", ""])
def test_account_selection_rejects_invalid_values_without_writing(resident_record, value):
    rid = resident_record["resident_id"]
    before = residents.get_resident_settings(_invocation("GetResidentSettings", resident_id=rid))
    with pytest.raises(ValueError, match="account_selection must be auto or fixed"):
        residents.update_resident_settings(_invocation("UpdateResidentSettings", resident_id=rid,
            expected_revision=before["settings_revision"], changes={"account_selection": value}))
    after = residents.get_resident_settings(_invocation("GetResidentSettings", resident_id=rid))
    assert after["settings_revision"] == before["settings_revision"]
    assert after["settings"] == before["settings"]


def test_account_selection_does_not_allow_unknown_settings(resident_record):
    rid = resident_record["resident_id"]
    with pytest.raises(InstructionError, match="changes 不接受字段"):
        residents.update_resident_settings(_invocation("UpdateResidentSettings", resident_id=rid,
            changes={"account_selection": "auto", "unknown_account_field": "auto"}))
