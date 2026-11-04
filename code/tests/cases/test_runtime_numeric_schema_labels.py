"""Version numbers without parsing semantics must not reject consumable fields."""
from dataclasses import replace

import pytest

from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.context_seed import context_seed
from management_portal.society_service.resident_context.evidence import project_conversation_messages
from management_portal.society_service.resident_conversation_records import conversation_event_notification
from tests.cases.test_provider_context_seed import _invocation


@pytest.mark.parametrize("version", [0, 1, 999])
def test_request_numeric_labels_do_not_override_field_validation(version):
    request = replace(_invocation("codex").request,
        schema=f"tfi.execution-request.v{version}",schema_version=version)
    restored = ExecutionRequest.from_payload(request.to_payload())
    assert restored.schema == request.schema and restored.schema_version == version
    assert restored.identity == request.identity
    payload = request.to_payload()
    payload["identity"] = {**payload["identity"], "actor":"different-actor"}
    with pytest.raises(ValueError):
        ExecutionRequest.from_payload(payload)
    with pytest.raises(ValueError):
        replace(request,prompt="").validate()


@pytest.mark.parametrize("version", [0, 999])
def test_seed_and_snapshot_preserve_labels_and_validate_fields(version):
    request = _invocation("codex").request
    seed = {"schema":f"tfi.provider-context-seed.v{version}","segments":[{"segment_id":"s"}]}
    assert context_seed(replace(request,metadata={"fork_context_input":seed}))["schema"] == seed["schema"]
    with pytest.raises(ValueError,match="Segment"):
        context_seed(replace(request,metadata={"fork_context_input":{**seed,"segments":[{}]}}))
    tag = f"tfi.resident-messages-state.v{version}"
    assert project_conversation_messages({"schema":tag,"messages":[],"input_states":[]}) == []
    with pytest.raises(ValueError,match="arrays"):
        project_conversation_messages({"schema":tag,"messages":{},"input_states":[]})
    event = {"schema":tag,"resident_id":"resident_aaaaaaaaaaaa","cursor":1,
        "event_type":"resident.message.appended","payload":{}}
    assert conversation_event_notification(event)["schema"] == tag


def test_runtime_snapshot_labels_keep_revision_and_artifact_digest_checks():
    import hashlib
    from management_portal.society_service.lineage_state_domains import validate_runtime_state
    from management_portal.execution.harness_artifacts import HarnessArtifactStore
    snapshot = {"schema":"tfi.lineage-runtime-state.v999","lineage_id":"lineage_aaaaaaaaaaaa",
        "runtime_revision":3,"nodes":{},"edges":{}}
    assert validate_runtime_state(snapshot)["runtime_revision"] == 3
    with pytest.raises(ValueError,match="revision"):
        validate_runtime_state({**snapshot,"runtime_revision":-1})
    envelope = {"schema":"portal.harness-artifact","schema_version":999,"handle":"h",
        "content":"body","sha256":hashlib.sha256(b"body").hexdigest()}
    HarnessArtifactStore._validate_envelope(envelope,expected_handle="h")
    with pytest.raises(RuntimeError,match="digest"):
        HarnessArtifactStore._validate_envelope({**envelope,"content":"altered"},expected_handle="h")
