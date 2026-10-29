"""Installable native switch bindings: definitions only, no source-site changes."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import json

import pytest

from management_portal import portal_feature_switches as switches

from management_portal.society_service import lineage_aggregate


def site():
    return {"site_name": "keep", "deployment": {
        "pulse": {"lineage_id": "lineage_custom", "event_source_ids": ["event_daily", "event_manual"], "private_unrelated": "not exported"},
        "development_projection": {"lineage_id": "lineage_custom", "architecture_audit_effect_id": "effect_custom"}}}


def definitions():
    components = [("event_daily", "event_source"), ("event_manual", "event_source"), ("effect_custom", "effect")]
    return {("lineage", "lineages"): [{"lineage_id": "lineage_custom"}],
            ("lineage", "lineage_nodes"): [{"lineage_id": "lineage_custom", "node_id": cid, "node_type": kind} for cid, kind in components],
            ("lineage", "lineage_components"): [{"node_id": cid, "config_json": {}} for cid, _ in components]}


def resolve(name="pulse", config=None, rows=None, selected=("lineage_custom",)):
    return switches.installation_mapping(name, site_config=site() if config is None else config,
                                        selected_lineage_ids=selected, definition_rows=definitions() if rows is None else rows)


@pytest.mark.parametrize("name,kind,count", [("pulse", "event_source", 2), ("architecture_audit", "effect", 1)])
def test_mapping_is_pure_serializable_and_has_no_runtime_default(monkeypatch, name, kind, count):
    def forbidden(*args, **kwargs):
        pytest.fail("Mapping must not read ambient site/runtime or write anything")
    monkeypatch.setattr(switches, "read_site_config", forbidden)
    monkeypatch.setattr(switches.component_runtime, "views", forbidden)
    monkeypatch.setattr(switches.component_runtime, "set_paused", forbidden)
    config, rows = site(), definitions()
    original = deepcopy((config, rows))
    result = resolve(name, config, rows)
    assert result["readiness"] == "ready"
    assert len(result["binding"]["components"]) == count
    assert {c["kind"] for c in result["binding"]["components"]} == {kind}
    assert (config, rows) == original
    assert json.loads(json.dumps(result)) == result
    assert "enabled" not in result and "default_enabled" not in result
    assert "private_unrelated" not in json.dumps(result) and "site_name" not in result["site_config"]
    assert switches._site_binding(name, result["site_config"]) == result["binding"]


@pytest.mark.parametrize("config,readiness,code", [
    ({}, "needs_configuration", "missing_lineage"),
    ({"deployment": []}, "conflict", "invalid_configuration"),
    ({"deployment": {"pulse": {"lineage_id": ""}}}, "needs_configuration", "missing_lineage"),
    ({"deployment": {"pulse": {"lineage_id": "lineage_custom"}}}, "needs_configuration", "missing_components"),
    ({"deployment": {"pulse": {"lineage_id": "lineage_custom", "event_source_ids": "event_daily"}}}, "conflict", "invalid_components"),
    ({"deployment": {"pulse": {"lineage_id": "lineage_custom", "event_source_ids": ["event_daily", "event_daily"]}}}, "conflict", "duplicate_components"),
])
def test_missing_or_invalid_site_never_claims_off(config, readiness, code):
    result = resolve(config=config)
    assert result["readiness"] == readiness
    assert result["diagnostics"][0]["code"] == code
    assert result["binding"] is None and result["site_config"] is None
    assert "enabled" not in result


@pytest.mark.parametrize("change,readiness", [
    ("missing_lineage", "needs_configuration"), ("duplicate_lineage", "conflict"),
    ("missing_node", "needs_configuration"), ("deleted_node", "needs_configuration"),
    ("missing_definition", "needs_configuration"), ("duplicate_node", "conflict"),
    ("duplicate_definition", "conflict"), ("wrong_lineage", "conflict"), ("wrong_kind", "conflict"),
])
def test_definition_validation(change, readiness):
    rows = definitions()
    lineages, nodes, components = (rows[("lineage", key)] for key in ("lineages", "lineage_nodes", "lineage_components"))
    if change == "missing_lineage": lineages.clear()
    elif change == "duplicate_lineage": lineages.append(deepcopy(lineages[0]))
    elif change == "missing_node": nodes.pop(0)
    elif change == "deleted_node": nodes[0]["deleted_at"] = "deleted"
    elif change == "missing_definition": components.pop(0)
    elif change == "duplicate_node": nodes.append(deepcopy(nodes[0]))
    elif change == "duplicate_definition": components.append(deepcopy(components[0]))
    elif change == "wrong_lineage": nodes[0]["lineage_id"] = "other_lineage"
    elif change == "wrong_kind": nodes[0]["node_type"] = "effect"
    result = resolve(rows=rows)
    assert result["readiness"] == readiness and result["binding"] is None


def test_ordinary_selection_is_explicit_even_when_all_definitions_present():
    result = resolve(selected=())
    assert result["readiness"] == "needs_configuration"
    assert result["diagnostics"][0]["code"] == "lineage_not_selected"
    with pytest.raises(ValueError): resolve(selected="lineage_custom")
    with pytest.raises(ValueError): resolve(name="some-new-flow")


@pytest.mark.parametrize("rows,readiness", [([], "needs_configuration"),
    ([{"component_id": "effect_custom", "kind": "event_source", "paused": False}], "conflict"),
    ([{"component_id": "effect_custom", "kind": "effect"}], "conflict"),
    ([{}, {}], "conflict")])
def test_runtime_mapping_validation_finishes_before_any_write(monkeypatch, rows, readiness):
    monkeypatch.setattr(switches, "read_site_config", site)
    monkeypatch.setattr(switches.component_runtime, "views", lambda *a, **kw: rows)
    monkeypatch.setattr(switches.component_runtime, "set_paused", lambda *a: pytest.fail("invalid mapping wrote state"))
    with pytest.raises(switches.MappingError) as error:
        switches.set_enabled("architecture_audit", False, "admin")
    assert error.value.readiness == readiness


def test_one_binding_snapshot_and_commit_then_fail_compensation(monkeypatch):
    calls, state = [], {"event_daily": False, "event_manual": False}
    config_reads = []
    def read_site():
        config_reads.append(1)
        assert len(config_reads) == 1
        return site()
    monkeypatch.setattr(switches, "read_site_config", read_site)
    monkeypatch.setattr(switches.component_runtime, "views", lambda lid, actor, *, component_id:
                        [{"component_id": component_id, "kind": "event_source", "paused": state[component_id]}])
    def set_paused(lid, cid, paused, actor):
        assert (lid, actor) == ("lineage_custom", "admin")
        calls.append((cid, paused))
        state[cid] = paused
        if cid == "event_manual" and paused: raise RuntimeError("post-commit projection failed")
    monkeypatch.setattr(switches.component_runtime, "set_paused", set_paused)
    with pytest.raises(RuntimeError, match="post-commit"):
        switches.set_enabled("pulse", False, "admin")
    assert not any(state.values())
    assert calls[-2:] == [("event_manual", False), ("event_daily", False)]


@pytest.mark.parametrize("error,expected", [(KeyError("Lineage not found"), switches.MappingError),
    (PermissionError("denied"), PermissionError), (KeyError("corrupt internals"), KeyError)])
def test_absent_lineage_is_diagnosed_without_hiding_other_errors(monkeypatch, error, expected):
    monkeypatch.setattr(switches, "read_site_config", site)
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(switches.component_runtime, "views", fail)
    with pytest.raises(expected) as caught:
        switches.status("pulse", "admin")
    if expected is switches.MappingError:
        assert caught.value.code == "lineage_missing"
        assert caught.value.readiness == "needs_configuration"


def test_failed_rollback_continues_other_components_and_reports_uncertainty(monkeypatch):
    restored = []
    monkeypatch.setattr(switches, "read_site_config", site)
    monkeypatch.setattr(switches.component_runtime, "views", lambda lid, actor, *, component_id:
                        [{"component_id": component_id, "kind": "event_source", "paused": False}])
    def set_paused(lid, cid, paused, actor):
        if cid == "event_manual": raise RuntimeError("unavailable")
        if not paused: restored.append(cid)
    monkeypatch.setattr(switches.component_runtime, "set_paused", set_paused)
    with pytest.raises(RuntimeError, match="恢复未确认.*event_manual"):
        switches.set_enabled("pulse", False, "admin")
    assert restored == ["event_daily"]


def test_native_pause_owner_is_consumed_by_event_and_effect_admission(monkeypatch):
    """Real reducers and admission checks; only persistence/authority is in memory."""
    from management_portal.society_service import (
        lineage_program_runtime as runtime,
        lineage_program_effect_execution as effect, lineage_program_event_dispatch as events,
        lineage_primitives, system_workflow_switches, trigger_paths,
    )
    cr = switches.component_runtime
    data = {"event_sources": {}, "operators": {}}
    plan = {"event_sources": {key: {"kind": "event_source", "event_type": "manual.triggered"}
                              for key in ("event_daily", "event_manual")},
            "effects": {"effect_custom": {"kind": "effect", "script": ""}}}
    @contextmanager
    def transaction(lid):
        assert lid == "lineage_custom"
        yield data
    @contextmanager
    def operator(lid, cid):
        with transaction(lid) as state:
            yield state["operators"].setdefault(cid, {})
    monkeypatch.setattr(switches, "read_site_config", site)
    monkeypatch.setattr(cr, "_plan", lambda lid, actor: plan)
    monkeypatch.setattr(runtime, "_runtime_transaction", transaction)
    monkeypatch.setattr(runtime, "operator_state_transaction", operator)
    monkeypatch.setattr(runtime, "read_operator_state", lambda lid, cid: data["operators"].get(cid, {}))
    monkeypatch.setattr(lineage_aggregate, "execution_authority", lambda lid: nullcontext())
    monkeypatch.setattr(system_workflow_switches, "project_after_commit", lambda lid: None)
    def views(lid, actor, *, component_id):
        kind = "effect" if component_id in plan["effects"] else "event_source"
        state = data["operators" if kind == "effect" else "event_sources"].get(component_id, {})
        return [{"component_id": component_id, "kind": kind, "paused": bool(state.get("paused"))}]
    monkeypatch.setattr(cr, "views", views)
    monkeypatch.setattr(trigger_paths, "accepted_input", lambda *args: pytest.fail("paused event admitted"))
    for name in ("pulse", "architecture_audit"):
        assert switches.set_enabled(name, False, "admin")["enabled"] is False
    with pytest.raises(effect._EffectPaused):
        effect._require_effect_open("lineage_custom", "effect_custom")
    for cid in plan["event_sources"]:
        result = events.dispatch_program_event("lineage_custom", cid,
            lineage_primitives.make_event("manual.triggered", payload={}), "admin", cursor="test", authority=None)
        assert result["paused"] and result["matched_count"] == 0
    assert switches.set_enabled("architecture_audit", True, "admin")["enabled"] is True
    effect._require_effect_open("lineage_custom", "effect_custom")
    assert switches.set_enabled("pulse", True, "admin")["enabled"] is True
    assert all(value.get("rebaseline") and not value.get("paused") for value in data["event_sources"].values())
