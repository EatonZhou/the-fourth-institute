""":module: spec.design-governance"""

import pytest

from management_portal.society_service.resident_storage import validate_record_fields


def test_resident_quota_configuration_rejects_unused_metadata():
    """Resident 额度预留覆盖只接受 binding_id、revision 与 enabled 字段"""
    policy = {"binding_id": "resident:test", "revision": 3, "enabled": False}
    validate_record_fields({"quota_reserve_override": policy})
    with pytest.raises(ValueError, match="only binding_id, revision and enabled"):
        validate_record_fields({"quota_reserve_override": {**policy, "updated_by": "alice"}})
