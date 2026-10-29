"""Runtime entry points ignore retired naming configuration without rewriting data."""
from copy import deepcopy
import importlib

import pytest

from management_portal import agent_skill_context as skills
from management_portal.resources import domain_projection_evidence as evidence
from scripts import check_portal_architecture as architecture
from scripts import resident_hatg_instruction as client
from tests.cases.compatibility_test_support import synthetic_site


def descriptor():
    return {"schema": "tfi.collaboration-descriptor.v2", "resource_id": "resident_0123456789ab",
            "deleted_at": "2026-10-20T00:00:00Z", "title": "Unchanged body"}


@pytest.mark.parametrize("change", ["descriptor_prefix", "source_prefix", "source_version",
                                   "historical_digest", "changed_body", "extra_field"])
def test_tombstone_proofs_reject_old_or_changed_preimages_without_writes(change):
    value = descriptor()
    source = evidence.tombstone_source(value)
    receipt = {"schema": evidence.EVIDENCE_SCHEMA, "domain_identity": "society-service", "incoming_descriptor": deepcopy(value)}
    if change == "descriptor_prefix": value["schema"] = "archive.collaboration-descriptor.v2"
    elif change == "source_prefix": source["schema"] = "archive.domain-tombstone-source.v2"
    elif change == "source_version": source["schema"] = "tfi.domain-tombstone-source.v1"
    elif change == "historical_digest":
        source["source_fingerprint"] = evidence.projection_fingerprint(
            {**value, "schema": "archive.collaboration-descriptor.v2"})
    elif change == "changed_body": value["title"] = "Another body"
    else: source["extra"] = True
    before = deepcopy((source, receipt, value))
    with synthetic_site():
        assert not evidence.matches_tombstone_source(source, value)
        assert not evidence.historical_tombstone_retry(source, receipt, value)
    assert (source, receipt, value) == before


def test_canonical_tombstone_retry_keeps_exact_descriptor_and_authority_checks():
    value = descriptor()
    source = evidence.tombstone_source(value)
    receipt = {"schema": evidence.EVIDENCE_SCHEMA, "domain_identity": "society-service", "incoming_descriptor": deepcopy(value)}
    with synthetic_site():
        assert evidence.matches_tombstone_source(source, value)
        assert evidence.historical_tombstone_retry(source, receipt, value)
        assert evidence.same_descriptor(value, deepcopy(value))
        assert not evidence.same_descriptor(value, {**value, "schema": "archive.collaboration-descriptor.v2"})
        assert not evidence.historical_tombstone_retry(source, {**receipt, "domain_identity": "external"}, value)
        assert not evidence.historical_tombstone_retry(source, {**receipt, "domain_identity": "lineage-service"}, value)
        assert not evidence.historical_tombstone_retry(source, {**receipt, "schema": "archive.domain-tombstone-evidence.v2"}, value)
        assert not evidence.historical_tombstone_retry(source, {**receipt, "incoming_descriptor": {}}, value)


@pytest.mark.parametrize("execution_id", ["execution-fixture", ""])
def test_instruction_client_only_emits_canonical_execution_header(monkeypatch, execution_id):
    monkeypatch.setattr(client, "_token", lambda resident: "fixture-token")
    monkeypatch.setattr(client, "execution_context", lambda: {"execution_id": execution_id})
    monkeypatch.delenv("PORTAL_EXECUTION_ID", raising=False)
    monkeypatch.setenv("ARCHIVE_EXECUTION_ID", "retired-value")
    with synthetic_site({"execution_id_header": "X-Archive-Execution-ID"}):
        request = client._request("resident_0123456789ab", {"instruction": "GetMessage"}, accept="application/json")
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers.get("x-portal-execution-id", "") == execution_id
    assert "x-archive-execution-id" not in headers
    assert headers["authorization"] == "Bearer fixture-token"


def test_instruction_client_keeps_canonical_environment_fallback(monkeypatch):
    monkeypatch.setattr(client, "_token", lambda resident: "fixture-token")
    monkeypatch.setattr(client, "execution_context", lambda: {})
    monkeypatch.setenv("PORTAL_EXECUTION_ID", "execution-environment")
    with synthetic_site({"execution_id_header": {"malformed": True}}):
        request = client._request("resident_0123456789ab", {}, accept="application/json")
    assert dict(request.header_items())["X-portal-execution-id"] == "execution-environment"


def test_skill_name_is_canonical_despite_site_alias_and_keeps_root_and_stale_cache(tmp_path):
    for name, text in (("tfi-agent-rule", "Canonical rule"), ("archive-rule", "Retired rule")):
        path = tmp_path / name
        path.mkdir()
        (path / "SKILL.md").write_text(text)
    with synthetic_site({"portal_system_skill": "archive-rule"}):
        importlib.reload(skills)
        assert skills.PORTAL_SYSTEM_SKILL == "tfi-agent-rule"
        result = skills.materialize_portal_skills("general", skill_root=str(tmp_path))
        assert "Canonical rule" in result.content and "Retired rule" not in result.content
        assert skills.materialize_portal_skills("general", skill_root=str(tmp_path),
                                               system_skill_name="tfi-agent-rule") == result
        with pytest.raises(ValueError, match="tfi-agent-rule"):
            skills.materialize_portal_skills("general", skill_root=str(tmp_path), system_skill_name="archive-rule")
        (tmp_path / "tfi-agent-rule" / "SKILL.md").unlink()
        stale = skills.materialize_portal_skills("general", skill_root=str(tmp_path))
        assert stale.status == "stale" and stale.sources == result.sources
    importlib.reload(skills)


@pytest.mark.parametrize("configured", [["/archive/source/"], {"malformed": True}])
def test_architecture_uses_current_root_without_retired_source_prefixes(tmp_path, configured):
    with synthetic_site({"architecture_source_prefixes": configured}):
        assert architecture.source_path(str(tmp_path / "module.py") + ":12#symbol", tmp_path) == "module.py"
        assert architecture.source_path("module.py:12", tmp_path) == "module.py"
        assert architecture.source_reference("/archive/source/module.py:12", tmp_path) == (
            "external_path", "/archive/source/module.py")
