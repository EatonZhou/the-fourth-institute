"""Resident capability intent, owner CAS and truthful application projection.

:module: resident.session
:design: decision_ccbb1cac24aa
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from management_portal.execution import resident_runtime_settings
from management_portal.society_service import (
    resident_backend, resident_catalog, resident_execution_domain,
    resident_record_domain, resident_runtime_domain, resident_session_backend_domain,
)
from management_portal.society_service.errors import LineageConflictError
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401


def capability_config():
    return {
        "schema": "tfi.resident-capabilities.v1",
        "tools": [{"ref": "portal:hatg_instruction", "enabled": False, "required": False}],
        "mcp": [{"ref": "mcp_111111111111", "enabled": True, "required": True,
                 "tools": ["lookup"]}],
        "skills": [{"ref": "skill_222222222222", "enabled": True, "required": False}],
    }


def read(rid):
    return resident_execution_domain.read_resident_runtime_settings(rid, "CASE-A")


def update(rid, value):
    return resident_execution_domain.change_resident_runtime_settings(rid, "CASE-A", value)


def test_capability_save_roundtrips_through_the_registered_owner_without_provider_io(resident_record, monkeypatch):
    rid = resident_record["resident_id"]
    original_binding = resident_backend.session_record(resident_record)
    def unexpected(*args, **kwargs):
        pytest.fail("capability save must not discover Provider runtime or start a session")
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    adapter_type = type(AGENT_PROVIDERS["codex"].runtime_adapter())
    monkeypatch.setattr(adapter_type, "validate_updates", unexpected)
    monkeypatch.setattr(resident_session_backend_domain, "run_backend_session_operation", unexpected)
    result = update(rid, {"capability_config": capability_config()})
    assert result["runtime_settings"]["capability_config"] == capability_config()
    stored = resident_catalog.get_catalog().state_store.read_resident_execution_settings(rid)
    assert stored["capability_config"] == capability_config()
    assert read(rid)["settings_revision"] == result["settings_revision"]
    assert resident_backend.session_record(resident_record) == original_binding
    persisted = resident_catalog.get_catalog().get(rid)
    assert "runtime_settings" not in persisted
    assert "capability_config" not in persisted


def test_revision_rejects_stale_changes_and_equal_save_is_a_noop(resident_record, monkeypatch):
    rid = resident_record["resident_id"]
    before = read(rid)
    first = update(rid, {"expected_revision": before["settings_revision"],
                         "runtime_settings": {"capability_config": capability_config()}})
    assert first["settings_revision"] != before["settings_revision"]
    changed = capability_config()
    changed["tools"][0]["enabled"] = True
    with pytest.raises(LineageConflictError):
        update(rid, {"expected_revision": before["settings_revision"], "capability_config": changed})
    def unexpected(*args, **kwargs):
        pytest.fail("equal settings must not be written again")
    monkeypatch.setattr(resident_runtime_domain, "write_resident_runtime_settings", unexpected)
    replay = update(rid, {"expected_revision": first["settings_revision"],
                          "capability_config": capability_config()})
    assert replay["settings_revision"] == first["settings_revision"]
    assert replay["runtime_settings"] == first["runtime_settings"]
    assert replay["next_turn_fields"] == []


def test_concurrent_clients_with_one_revision_only_commit_once(resident_record):
    rid = resident_record["resident_id"]
    revision = read(rid)["settings_revision"]
    ready = Barrier(2)
    def save(enabled):
        config = capability_config()
        config["tools"][0]["enabled"] = enabled
        ready.wait(timeout=10)
        try:
            return update(rid, {"expected_revision": revision, "capability_config": config})
        except LineageConflictError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [False, True]))
    assert sum(result == "conflict" for result in results) == 1
    accepted = next(result for result in results if result != "conflict")
    assert read(rid)["settings_revision"] == accepted["settings_revision"]


def test_missing_capability_metadata_preserves_legacy_defaults_and_is_unknown(resident_record):
    rid = resident_record["resident_id"]
    before = resident_catalog.get_catalog().state_store.read_resident_execution_settings(rid)
    result = read(rid)
    assert "capability_config" not in result["runtime_settings"]
    item = next(item for item in result["application"]["items"] if item["path"] == "capability_config")
    assert item["status"] == "unknown" and item["delivery"] == "unknown"
    assert resident_catalog.get_catalog().state_store.read_resident_execution_settings(rid) == before


def test_application_never_claims_provider_acceptance_from_desired_settings(resident_record):
    rid = resident_record["resident_id"]
    result = update(rid, {"capability_config": capability_config()})
    items = result["application"]["items"]
    by_ref = {item["ref"]: item for item in items if "ref" in item}
    assert by_ref["portal:hatg_instruction"]["status"] == "disabled"
    assert by_ref["mcp_111111111111"]["status"] == "pending_session"
    assert all(item["delivery"] == "unknown" for item in items)
    prompt = {item["path"]: item for item in items if item["path"].startswith("prompt_injection.")}
    assert prompt["prompt_injection.core_rules"]["status"] == "pending_session"
    assert prompt["prompt_injection.runtime_identity"]["status"] == "next_turn"
    assert prompt["prompt_injection.resident_md"]["source"] == "resident.md"


@pytest.mark.parametrize("raw", [
    None, [], {}, {"schema": "future"},
    {"schema": "tfi.resident-capabilities.v1", "mcp": {"ref": "x"}},
    {"schema": "tfi.resident-capabilities.v1", "tools": [{"ref": "x", "enabled": 1}]},
    {"schema": "tfi.resident-capabilities.v1", "tools": [{"ref": "x", "required": "false"}]},
    {"schema": "tfi.resident-capabilities.v1", "tools": [{"ref": "x", "enabled": False, "required": True}]},
    {"schema": "tfi.resident-capabilities.v1", "tools": [{"ref": "x"}, {"ref": "x"}]},
    {"schema": "tfi.resident-capabilities.v1", "mcp": [{"ref": "x", "env": {"KEY": "hidden"}}]},
    {"schema": "tfi.resident-capabilities.v1", "mcp": [{"ref": "x", "tools": ["a", "a"]}]},
    {"schema": "tfi.resident-capabilities.v1", "skills": [{"ref": "  "}]},
    {"schema": "tfi.resident-capabilities.v1", "skills": [{"ref": "x\ny"}]},
])
def test_invalid_capabilities_cannot_enter_the_settings_owner(raw):
    from management_portal.execution.resident_capability_settings import normalize_capability_config
    with pytest.raises(ValueError):
        normalize_capability_config(raw)


def test_reference_order_and_object_order_are_semantically_equal():
    from management_portal.execution.resident_capability_settings import (
        desired_capability_config, normalize_capability_config,
    )
    base = {"schema": "tfi.resident-capabilities.v1", "tools": [{"ref": "b"}, {"ref": "a"}],
            "mcp": [{"ref": "m", "tools": ["z", "a"]}]}
    canonical = normalize_capability_config(base)
    assert [item["ref"] for item in canonical["tools"]] == ["a", "b"]
    assert canonical["mcp"][0]["tools"] == ["a", "z"]
    assert canonical["tools"][0] == {"ref": "a", "enabled": True, "required": False}
    assert desired_capability_config({}) == {"schema": "tfi.resident-capabilities.v1", "tools": [], "mcp": [], "skills": []}
    assert resident_runtime_settings.merge_settings({}, {"capability_config": base})["capability_config"] == canonical


def test_mcp_discovery_without_explicit_tool_selection_does_not_enable_tools():
    from management_portal.execution.resident_capability_settings import normalize_capability_config
    config = normalize_capability_config({"schema": "tfi.resident-capabilities.v1", "mcp": [{"ref": "mcp_111111111111"}]})
    assert config["mcp"][0]["tools"] == []


def test_configuration_write_rolls_back_on_record_failure(resident_record, monkeypatch):
    rid = resident_record["resident_id"]
    before = read(rid)
    def fail(*args, **kwargs):
        raise RuntimeError("record failure")
    monkeypatch.setattr(resident_record_domain, "save_resident_record", fail)
    with pytest.raises(RuntimeError, match="record failure"):
        update(rid, {"capability_config": capability_config()})
    assert read(rid)["settings_revision"] == before["settings_revision"]
    assert read(rid)["runtime_settings"] == before["runtime_settings"]


def test_mixed_settings_validate_only_provider_parameters_and_preserve_capabilities(resident_record, monkeypatch):
    from management_portal import ai_provider_registry
    from types import SimpleNamespace
    from management_portal.society_service.resident_settings_contract import stage_runtime_settings
    monkeypatch.setattr(ai_provider_registry, "shared_ai_provider_registry", SimpleNamespace(get=lambda route: {
        "enabled": True, "kind": "openai_compatible_chat", "models": ["before", "after"]}))
    rid = resident_record["resident_id"]
    record = deepcopy(resident_catalog.get_catalog().get(rid))
    record.update(backend="provider_api", backend_route_id="test-api")
    stage_runtime_settings(record, {"backend": "provider_api", "model": "before"})
    resident_record_domain.save_resident_record(record, commit_configuration=True)
    result = update(rid, {"model": "after", "capability_config": capability_config()})
    assert result["runtime_settings"]["model"] == "after"
    assert result["runtime_settings"]["capability_config"] == capability_config()
    again = update(rid, {"model": "before"})
    assert again["runtime_settings"]["capability_config"] == capability_config()
    with pytest.raises(ValueError, match="does not support"):
        update(rid, {"allow_subagents": True, "capability_config": capability_config()})


def test_cas_is_rechecked_after_provider_validation(resident_record, monkeypatch):
    rid = resident_record["resident_id"]
    before = read(rid)
    original_validate = resident_runtime_domain.validate_backend_runtime_updates
    def validate(record, current, updates):
        if "model" in updates:
            update(rid, {"capability_config": capability_config()})
            return dict(updates)
        return original_validate(record, current, updates)
    monkeypatch.setattr(resident_runtime_domain, "validate_backend_runtime_updates", validate)
    with pytest.raises(LineageConflictError):
        update(rid, {"model": "stale-client-model", "expected_revision": before["settings_revision"]})
    result = read(rid)
    assert result["runtime_settings"]["capability_config"] == capability_config()
    assert result["runtime_settings"]["model"] == before["runtime_settings"]["model"]


def test_saving_speed_does_not_modify_an_admitted_turn(resident_record, monkeypatch):
    from management_portal.society_service import resident_input_queue_domain
    def unexpected(*args, **kwargs):
        pytest.fail("settings save must not send an active-turn control")
    monkeypatch.setattr(resident_input_queue_domain, "submit_active_turn_settings", unexpected)
    monkeypatch.setattr(resident_runtime_domain, "validate_backend_runtime_updates", lambda record, current, updates: dict(updates))
    result = update(resident_record["resident_id"], {"service_tier": "fast"})
    assert result["active_turn_fields"] == []
    assert result["next_turn_fields"] == ["service_tier"]
    assert result["active_turn_settings_update"]["requested"] is False


@pytest.mark.parametrize("provider,preset,part,status", [
    ("claude", "minimal", "core_rules", "next_turn"),
    ("claude", "pure", "resident_md", "disabled"),
    ("codex", "standard", "core_rules", "pending_session"),
    ("codex", "standard", "resident_md", "pending_compaction_or_session"),
    ("codex", "full", "runtime_identity", "next_turn"),
])
def test_prompt_timing_uses_the_provider_declared_transport(provider, preset, part, status):
    from management_portal.execution.resident_capability_settings import settings_application
    application = settings_application({"prompt_injection": {"preset": preset}}, backend=provider, revision="test")
    item = next(item for item in application["items"] if item["path"] == f"prompt_injection.{part}")
    assert item["status"] == status
    assert item["delivery"] == "unknown"
    assert item["placement"] and item["source"]


@pytest.mark.parametrize("provider,status", [
    ("claude", "next_turn"),
    ("codex", "pending_session"),
    ("kimi", "next_turn"),
    ("dsh", "next_turn"),
    ("opencode", "next_turn"),
    ("provider_api", "unknown"),
])
def test_tool_application_uses_provider_capabilities_without_claiming_delivery(provider, status):
    from management_portal.execution.resident_capability_settings import settings_application
    config = capability_config()
    config["tools"][0]["enabled"] = True
    config["tools"].append({"ref": "portal:disabled", "enabled": False})
    config["mcp"].append({"ref": "mcp_disabled", "enabled": False})
    application = settings_application({"capability_config": config}, backend=provider, revision="test")
    selected = [item for item in application["items"]
                if item["path"] in {"capability_config.tools", "capability_config.mcp"}]
    assert len(selected) == 4
    for item in selected:
        assert item["status"] == (status if item["enabled"] else "disabled")
        assert item["delivery"] == "unknown"


@pytest.mark.parametrize("fixed", [False, True])
def test_tool_application_without_provider_tool_support_is_unsupported(monkeypatch, fixed):
    from dataclasses import replace
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    from management_portal.execution.resident_capability_settings import settings_application
    provider = AGENT_PROVIDERS["claude"]
    monkeypatch.setitem(AGENT_PROVIDERS, "claude", replace(
        provider, harness=provider.harness - {"portal_tools"}, session_tools_fixed=fixed,
    ))
    config = capability_config()
    config["tools"][0]["enabled"] = True
    application = settings_application({"capability_config": config}, backend="claude", revision="test")
    selected = [item for item in application["items"]
                if item["path"] in {"capability_config.tools", "capability_config.mcp"}]
    assert len(selected) == 2
    assert all(item["status"] == "unsupported" and item["delivery"] == "unknown" for item in selected)


@pytest.mark.parametrize("revision", [None, "", 1, True, [], {}])
def test_malformed_revision_is_rejected_without_writing(resident_record, revision):
    rid = resident_record["resident_id"]
    before = read(rid)
    with pytest.raises(ValueError, match="expected_revision"):
        update(rid, {"expected_revision": revision, "capability_config": capability_config()})
    assert read(rid)["settings_revision"] == before["settings_revision"]
