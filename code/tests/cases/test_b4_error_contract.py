"""Domain failures keep their code and commit state across socket serialization."""
import importlib

import pytest

from management_common.operation_errors import OperationError, failure_payload
from management_portal.society_service.transport import SocietyServiceClient


@pytest.mark.parametrize("module,name,status", [
    ("management_portal.application_targets", "ApplicationUnconfigured", 404),
    ("resident_message_contract", "MessageIdentityConflict", 409),
    ("component_runtime", "ComponentRuntimeError", 404),
    ("lineage_program_backlog", "EventSourcePausedError", 409),
    ("lineage_effect_script_journal", "RecoveryPending", 409),
    ("lineage_effect_script_journal", "ScriptChanged", 409),
])
def test_declared_domain_error_survives_transport(module, name, status):
    """领域错误经远程传输后保留错误码与提交状态"""
    module_path = module if module.startswith("management_portal.") else "management_portal.society_service." + module
    cls = getattr(importlib.import_module(module_path), name)
    error = cls("domain detail")
    encoded = failure_payload(error)
    assert encoded["code"] == error.code
    assert encoded["http_status"] == status
    assert encoded["commit_state"] == "not_committed"
    with pytest.raises(OperationError) as caught:
        SocietyServiceClient._raise_remote_error(encoded)
    assert failure_payload(caught.value) == encoded
