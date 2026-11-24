"""Society's executable definitions at the no-alias ISA cutover."""

from copy import deepcopy
from hashlib import sha256
import json
import sys
from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    effect_script_candidate,
    domain_work_storage,
    family_tasks,
    instruction_name_migration as migration,
    lineage_aggregate,
    lineage_effect_script_journal as journal,
    lineage_lifecycle,
    server,
    system_workflow_switches,
)
from management_portal.society_service.templates import builtins, marketplace, store
from tests.cases.test_lineage_generic_primitives import ACTOR, WORK_A, _worker, primitive_store  # noqa: F401


# Shapes from the pre-cutover distribution's lineage_components.config_json
# and lineage_edges.config_json, with unrelated arguments omitted.
EFFECT_CONFIG = {
    "endpoints": [],
    "script": (
        'checked=$(CheckResidentWorkSummary resident_id="${event.subject_resident_id}" '
        'message_id="${event.payload.message_id}")\n'
        'if [ "${checked.consolidation_due}" != "true" ]; then\n'
        '  output status="${checked.status}"\n'
        '  exit\n'
        'fi'
    ),
}
WIRE_CONFIG = {
    "invoke_profiles": {"effect": {"arguments": {"family_tasks": {"value": {
        "queue": {"guide": "必要时 ReorderMailbox 调整顺序", "prompt": "", "role": "steward"},
        "recovery": {
            "guide": "用CheckFamilyRecovery查看原故障；用 OpenIntake(open=true) 恢复接单。",
            "prompt": "",
            "role": "steward",
        },
        "route_review": {"guide": "核对 CheckFamilyRecovery。", "prompt": "", "role": "steward"},
    }}}}},
}
EFFECT_ID = "effect_a11a11a11a11"
SOURCE_ID = "event_source_b22b22b22b22"
WIRE_ID = "wire_c33c33c33c33"
TARGET_WIRE_ID = "wire_d44d44d44d44"


def test_definition_cutover_rejects_inflight_then_resumes_without_rewriting_history(
    primitive_store, monkeypatch, capsys,
):
    created = lineage_lifecycle.create_lineage({
        "title": "Old ISA configuration",
        "nodes": [_worker(WORK_A)],
        "edges": [],
        "program": {
            "components": [
                {"id": SOURCE_ID, "kind": "event_source", "config": {"event_type": "manual.triggered"}},
                {"id": EFFECT_ID, "kind": "effect", "config": {**EFFECT_CONFIG, "endpoints": ["target"]}},
            ],
            "wires": [
                {"id": WIRE_ID, "kind": "route", "source": SOURCE_ID,
                 "target": EFFECT_ID, "config": WIRE_CONFIG},
                {"id": TARGET_WIRE_ID, "kind": "target", "source": EFFECT_ID, "target": WORK_A},
            ],
        },
    }, ACTOR)
    lineage_id = created["lineage_id"]
    catalog = lineage_aggregate.lineage_catalog_store()
    before = catalog.get(lineage_id)
    configless = next(item for item in before["program"]["wires"] if item["id"] == TARGET_WIRE_ID)
    assert "config" not in configless
    script_sha = sha256(EFFECT_CONFIG["script"].encode()).hexdigest()
    journal.begin(lineage_id, "open-activation", effect_id=EFFECT_ID, trigger_path_id="",
                  script_sha=script_sha, source={"source_wire_id": WIRE_ID})
    journal.record_call_started(lineage_id, "open-activation", "c1", {
        "instruction": "CheckResidentWorkSummary", "caller": ACTOR,
        "instruction_id": "inst_lin_11111111111111111111",
    })
    open_journal = journal.load(lineage_id, "open-activation")
    report = {
        "blocked_count": 1,
        "blockers": [{"kind": "effect_activation", "lineage_id": lineage_id,
                      "effect_id": EFFECT_ID, "activation_key": "open-activation", "wire_id": WIRE_ID,
                      "journal_status": "running", "step_status": "running",
                      "trigger_path_id": open_journal["trigger_path_id"],
                      "calls": [{"position": "c1", "status": "executing",
                                 "instruction_id": "inst_lin_11111111111111111111"}]}],
    }
    list_work = domain_work_storage.list_work

    def read_work(connection, table, identity):
        assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        return list_work(connection, table, identity)

    with monkeypatch.context() as patch:
        def forbid_catalog():
            pytest.fail("Read-only preflight opened the ordinary owner store")

        patch.setattr(lineage_aggregate, "lineage_catalog_store", forbid_catalog)
        patch.setattr(domain_work_storage, "list_work", read_work)
        patch.setattr(sys, "argv", ["society", "--check-isa-name-migration"])
        assert server.main() == 2
        assert json.loads(capsys.readouterr().out) == report
    with pytest.raises(RuntimeError, match="open-activation"):
        migration.migrate()
    assert catalog.get(lineage_id) == before
    assert journal.load(lineage_id, "open-activation") == open_journal

    journal.record_call(lineage_id, "open-activation", "c1", {
        "instruction": "CheckResidentWorkSummary", "caller": ACTOR,
        "status": "committed", "ok": True, "result": {},
    })
    journal.finish(lineage_id, "open-activation", status="succeeded", outputs={})
    history = journal.load(lineage_id, "open-activation")
    assert history["calls"]["c1"]["instruction"] == "CheckResidentWorkSummary"
    assert migration.check()["blocked_count"] == 0
    monkeypatch.setattr(sys, "argv", ["society", "--check-isa-name-migration"])
    assert server.main() == 0
    assert json.loads(capsys.readouterr().out) == {"blocked_count": 0, "blockers": []}
    migration.migrate()
    after = catalog.get(lineage_id)
    assert after["revision"] == before["revision"] + 1
    assert after["layout_revision"] == before["layout_revision"]
    assert next(item for item in after["program"]["wires"] if item["id"] == TARGET_WIRE_ID) == configless
    effect = next(item for item in after["program"]["components"] if item["id"] == EFFECT_ID)
    assert effect["config"]["script"] == EFFECT_CONFIG["script"].replace(
        "CheckResidentWorkSummary", "MaintainResidentWorkSummary",
    )
    wire = next(item for item in after["program"]["wires"] if item["id"] == WIRE_ID)
    tasks = wire["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"]
    assert tasks["queue"]["guide"] == "必要时 MoveMailboxItems 调整顺序"
    assert tasks["recovery"]["guide"] == "用MaintainFamilyRecovery查看原故障；用 SetIntakeOpen(open=true) 恢复接单。"
    assert tasks["route_review"]["guide"] == "核对 MaintainFamilyRecovery。"
    assert journal.load(lineage_id, "open-activation") == history
    migration.migrate()
    assert catalog.get(lineage_id) == after


def test_old_store_snapshot_and_builtin_restore_share_executable_normalization(monkeypatch):
    legacy = {
        "kind": "script", "category": "apply", "title": "Old script", "description": "",
        "targets": ["canvas"], "parameters": [],
        "body": {"script": 'OpenIntake open:=true\n# OpenIntake', "endpoints": []},
    }
    monkeypatch.setattr(marketplace, "is_admin", lambda _: False)
    source = store.save(legacy, "publisher")
    public = marketplace.publish(source["template_id"], "publisher")["item"]
    installed = marketplace.install(public["store_id"], "installer")["template"]
    assert installed["body"]["script"] == "SetIntakeOpen open:=true\n# OpenIntake"
    assert not installed["store_status"]["locally_modified"]
    assert public["instructions_used"] == ["SetIntakeOpen"]
    with store.database().read() as connection:
        before = marketplace.items(connection)
    assert before[0]["body"] == legacy["body"]
    assert before[0]["content_hash"] == store.content_hash(legacy)

    builtin = {**legacy, "template_id": "template_11aa11aa11aa", "builtin_key": "old_script"}
    monkeypatch.setattr(builtins, "definitions", lambda: [builtin])
    restored = store.restore(builtin["template_id"], "installer")
    assert restored["body"] == installed["body"]
    store.migrate_instruction_names()
    assert store.get(source["template_id"])["version"] == source["version"] + 1
    assert store.get(installed["template_id"])["version"] == installed["version"]
    with store.database().read() as connection:
        assert marketplace.items(connection) == before


def test_candidate_evidence_remains_verifiable_and_delivery_prompts_are_normalized():
    draft = {
        "script": 'DeliverMessage resident_id="${endpoint.target}" message="请用OpenTerminal继续"',
        "endpoints": ["target"],
        "description": "Old approval evidence: OpenTerminal",
        "requested_by": ACTOR,
        "confirm_by": ACTOR,
    }
    draft["hash"] = effect_script_candidate.digest(draft)
    config = {"script": "# OpenTerminal", "endpoints": [], "script_draft": draft}
    before = deepcopy(config)
    assert migration.normalize_component_config(config) == before
    reviewed = effect_script_candidate.review_config(config, ACTOR, draft["hash"], "confirm")
    assert "请用CreateTerminal继续" in reviewed["script"]
    assert config == before
    assert migration.normalize_script('result=$("Open"InTake)') == 'result=$("Open"InTake)'
    assert migration.normalize_script('result=$("Open"Intake)') == "result=$(SetIntakeOpen)"


def test_pending_hold_diagnostics_exposes_status_and_ids_only():
    assert migration._delivery_status({
        "claim_token": "delivery-1",
        "event": {"payload": {"trigger_path_id": "trigger_path_abcdefabcdef", "prompt": "omitted"}},
        "recovery_blocked": {"activation_key": "effect:held", "instruction_id": "inst_lin_held",
                             "requires_resolution": True, "receipt": "omitted"},
    }) == {
        "delivery_id": "delivery-1",
        "trigger_path_id": "trigger_path_abcdefabcdef",
        "activation_key": "effect:held",
        "instruction_id": "inst_lin_held",
        "status": "blocked",
        "requires_resolution": True,
    }


@pytest.mark.parametrize(("trigger", "historical", "current_name"), [
    ("queue", "_PRE_UNIFIED_QUEUE_GUIDE", "MoveMailboxItems"),
    ("recovery", "_PRE_DIRECT_RECOVERY_GUIDE", "SetIntakeOpen"),
    ("route_review", "_PRE_DIRECT_RECOVERY_ROUTE_REVIEW_GUIDE", "MaintainFamilyRecovery"),
])
def test_family_default_guides_upgrade_in_original_and_normalized_templates(trigger, historical, current_name):
    legacy_guide = getattr(family_tasks, historical)
    renamed_guide = migration.normalize_prompt(legacy_guide)
    assert legacy_guide != renamed_guide
    route = family_tasks.TASKS[trigger][0]
    assert current_name in family_tasks.defaults(route)[trigger]["guide"]

    def normalized_entry(guide):
        entry = {"guide": guide, "prompt": "自定义每轮目标 ${facts}"}
        config = {"invoke_profiles": {"effect": {"arguments": {
            "family_tasks": {"value": {trigger: entry}},
        }}}}
        template = {"body": {"wires": [{"config": config}]}}
        current = migration.normalize_template(template)["body"]["wires"][0]["config"]
        assert current == migration.normalize_wire_config(config)
        return current["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"][trigger]

    assert family_tasks.saved_fields(trigger, {"guide": legacy_guide}) == {}
    installed = normalized_entry(legacy_guide)
    assert installed["guide"] == renamed_guide
    assert family_tasks.saved_fields(trigger, installed) == {"prompt": installed["prompt"]}
    custom_guide = legacy_guide + "\n保持这个 Family 的自定义处理步骤。"
    assert family_tasks.saved_fields(trigger, {"guide": custom_guide}) == {"guide": custom_guide}
    customized = normalized_entry(custom_guide)
    assert customized["guide"] == renamed_guide + "\n保持这个 Family 的自定义处理步骤。"
    assert family_tasks.saved_fields(trigger, customized) == customized


def test_family_default_summary_uses_message_watermark_and_keeps_recent_layer():
    for trigger, legacy, current in (
        ("summary", family_tasks._VERSIONED_SUMMARY_GUIDE, "consolidated_message_ids"),
        ("progress", family_tasks._VERSIONED_PROGRESS_GUIDE, "source_versions"),
    ):
        route = family_tasks.TASKS[trigger][0]
        default = family_tasks.defaults(route)[trigger]["guide"]
        assert "expected_revision" in legacy
        assert family_tasks.saved_fields(trigger, {"guide": migration.normalize_prompt(legacy)}) == {}
        assert current in default
        assert "expected_revision" not in default
    summary = family_tasks.defaults("request_summary")["summary"]["guide"]
    assert "pending_message_ids" in summary
    assert "近期层由成员本人维护" in summary


@pytest.mark.parametrize("blocked", [False, True])
def test_startup_migration_gates_recovery_and_maintenance(monkeypatch, blocked):
    calls = []

    def migrate():
        calls.append("migration")
        if blocked:
            raise RuntimeError("unsettled Effect work")

    monkeypatch.setattr(migration, "migrate", migrate)
    monkeypatch.setattr(system_workflow_switches, "restore", lambda: calls.append("restore"))
    monkeypatch.setattr(server.DrainingStreamServer, "serve_forever", lambda *_: calls.append("serve"))
    monkeypatch.setattr(server, "_start_resident_context_worker", lambda: None)
    from management_portal.society_service.script_runs import worker as script_worker
    monkeypatch.setattr(script_worker, "start_worker", lambda: None)
    monkeypatch.setattr(server.threading.Thread, "start", lambda _: None)
    service = object.__new__(server.SocietyServiceServer)
    service.projections_enabled = True
    for name in ("instruction_maintenance", "resident_conversation_event_relay",
                 "resident_resource_projection", "family_resource_projection",
                 "effect_recovery_worker", "coordinator"):
        setattr(service, name, SimpleNamespace(start=lambda name=name: calls.append(name)))
    if blocked:
        with pytest.raises(RuntimeError, match="unsettled Effect work"):
            service.serve_forever()
        assert calls == ["migration"]
    else:
        service.serve_forever()
        assert calls[0:2] == ["migration", "restore"]
        assert calls.index("effect_recovery_worker") < calls.index("coordinator") < calls.index("serve")
