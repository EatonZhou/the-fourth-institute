"""A duty draft changes its graph and memory in one version, within its boundary."""
from tests.cases.isa_test_support import ok, run
from tests.cases.test_duties import apply, duties, preview_parts
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, graph, lineage, resident  # noqa: F401


def test_preview_commit_and_memory_share_one_version(lineage):
    worker = resident(lineage, "Worker")
    first = apply(lineage, "fixed_reminder", worker)
    second = apply(lineage, "scheduled_work", worker, task="Keep other duty")
    before = graph(lineage)
    block = next(b for b in before["function_blocks"] if b["id"] == first)
    event = next(e["id"] for e in block["elements"] if e["kind"] == "component")
    args = {"lineage_id": lineage, "function_block_id": first,
            "operations": [{"op": "update", "kind": "component", "id": event, "changes": {"title": "Edited event"}}],
            "duty_changes": {"description": "Edited duty", "prompt": "Only this duty", "memory_level": "session"}}
    preview = ok("ApplyLineageChanges", {**args, "dry_run": True}, version=before["revision"])
    assert graph(lineage) == before
    assert preview["preview"]["elements"][0]["after"]["title"] == "Edited event"
    result = ok("ApplyLineageChanges", args, version=before["revision"])
    after = graph(lineage)
    assert result["revision"] == after["revision"] == before["revision"] + 1
    assert next(b for b in after["function_blocks"] if b["id"] == second) == next(b for b in before["function_blocks"] if b["id"] == second)
    assert after["nodes"] == before["nodes"]
    assert "Only this duty" in preview_parts(worker)["duty_memory"]["content"]
    assert run("ApplyLineageChanges", args, version=before["revision"]).error["code"] == "instruction.conflict"
    assert run("ApplyLineageChanges", {**args, "dry_run": True}, version=before["revision"]).error["code"] == "instruction.conflict"
    assert graph(lineage) == after
    ok("DeleteFunctionBlock", {"lineage_id": lineage, "function_block_id": first})
    assert "Only this duty" not in str(preview_parts(worker))


def test_outside_edits_and_cascading_shared_wire_deletion_are_atomic(lineage):
    worker = resident(lineage, "Worker")
    first = apply(lineage, "fixed_reminder", worker)
    apply(lineage, "fixed_reminder", worker)
    a, b = duties(lineage, worker)
    owned = {e["id"] for e in a["elements"]}
    event = next(c["id"] for c in graph(lineage)["program"]["components"] if c["id"] in owned and c["kind"] == "event_source")
    foreign = next(e["id"] for e in b["elements"] if e["kind"] == "component")
    ok("CreateWire", {"lineage_id": lineage, "kind": "route", "source": event, "target": worker})
    before = graph(lineage)
    base = {"lineage_id": lineage, "function_block_id": first, "duty_changes": {"description": "Must roll back"}}
    for operation in [
        {"op": "update", "kind": "component", "id": foreign, "changes": {"title": "No"}},
        {"op": "delete", "kind": "component", "id": event},
        {"op": "delete", "kind": "resident", "id": worker},
        {"op": "create", "kind": "wire", "value": {"kind": "route", "source": "event_source_000000000000", "target": worker}},
    ]:
        assert run("ApplyLineageChanges", {**base, "operations": [operation]}, version=before["revision"]).error
        assert graph(lineage) == before
    assert run("ApplyLineageChanges", {**base, "operations": []}).error


def test_prompt_only_additions_get_owned_and_on_demand_memory_is_removed(lineage):
    worker = resident(lineage, "Worker")
    block = ok("CreateDuty", {"lineage_id": lineage, "holder_id": worker, "description": "Manual", "prompt": "Original"})["function_block"]
    before = graph(lineage)
    args = {"lineage_id": lineage, "function_block_id": block["id"],
            "operations": [{"op": "create", "kind": "component", "ref": "event", "value": {"kind": "event_source", "config": {"event_type": "manual.triggered"}}},
                           {"op": "create", "kind": "wire", "value": {"kind": "route", "source": "$ref:event", "target": worker}}],
            "duty_changes": {"prompt": "Read the manual", "memory_level": "on_demand"}}
    saved = ok("ApplyLineageChanges", args, version=before["revision"])
    assert {e["id"] for e in saved["function_block"]["elements"]} == set(saved["created_ids"])
    prompt = preview_parts(worker)["duty_memory"]["content"]
    from pathlib import Path
    path = Path(prompt.split("完整说明在 ")[1].split(" 的")[0])
    assert "Read the manual" in path.read_text()
    ok("ApplyLineageChanges", {"lineage_id": lineage, "function_block_id": block["id"], "operations": [],
                               "duty_changes": {"prompt": "Now per turn", "memory_level": "turn"}}, version=saved["revision"])
    assert "Now per turn" in preview_parts(worker)["duty_memory_turn"]["content"]
    assert not path.exists() or "Read the manual" not in path.read_text()


def test_organization_owned_effect_edits_and_permissions(lineage, monkeypatch):
    from management_portal.society_service import function_block_domain
    worker = resident(lineage, "Member")
    org = ok("Functionalize", {"lineage_id": lineage, "element_ids": [worker], "title": "Organization"})["function_block_id"]
    effect = ok("CreateComponent", {"lineage_id": lineage, "kind": "effect", "title": "Effect"})["component_id"]
    block = function_block_domain.functionalize(lineage, [effect], "sample_admin", title="Organization duty",
                                               duty={"holder_id": org, "description": "Effect chain", "category": "custom"})
    before = graph(lineage)
    args = {"lineage_id": lineage, "function_block_id": block["id"],
            "operations": [{"op": "update", "kind": "component", "id": effect, "changes": {"title": "Edited effect"}}]}
    from management_portal.society_service import lineage_aggregate
    def denied(*_args):
        raise PermissionError("This caller cannot manage the Lineage")
    # The fixture uses the open permission scheme; exercise the shared domain
    # guard explicitly for both preview and commit, without inventing a role.
    with monkeypatch.context() as guard:
        guard.setattr(lineage_aggregate, "ensure_manager", denied)
        for dry_run in (True, False):
            assert run("ApplyLineageChanges", {**args, "dry_run": dry_run}, version=before["revision"]).error
    assert graph(lineage) == before
    preview = ok("ApplyLineageChanges", {**args, "dry_run": True}, version=before["revision"])
    assert graph(lineage) == before
    assert preview["function_block"]["duty"]["holder_id"] == org
    ok("ApplyLineageChanges", args, version=before["revision"])
    after = graph(lineage)
    assert next(b for b in after["function_blocks"] if b["id"] == org) == next(b for b in before["function_blocks"] if b["id"] == org)
    assert after["nodes"] == before["nodes"]


def test_duty_capture_preserves_memory_without_concrete_holder(lineage):
    first, second = resident(lineage, "First"), resident(lineage, "Second")
    for structured in (False, True):
        if structured:
            block_id = apply(lineage, "fixed_reminder", first)
            ok("UpdateDuty", {"lineage_id": lineage, "function_block_id": block_id, "prompt": "Remember " + first, "memory_level": "turn"})
        else:
            block_id = ok("CreateDuty", {"lineage_id": lineage, "holder_id": first, "description": "Prompt duty", "prompt": "Remember " + first})["function_block"]["id"]
        original = graph(lineage)
        args = {"from_selection": {"lineage_id": lineage, "element_ids": [block_id]}}
        ok("CreateTemplate", {**args, "dry_run": True})
        template = ok("CreateTemplate", args)["template"]
        assert first not in str(template["body"])
        assert graph(lineage) == original
        result = ok("ApplyTemplate", {"lineage_id": lineage, "template_id": template["template_id"], "target": {"kind": "resident", "resident_ids": [second]}})
        block = next(b for b in graph(lineage)["function_blocks"] if b["id"] == result["function_block_ids"][0])
        assert block["duty"]["memory"]["prompt"] == "Remember " + second
        assert block["duty"]["holder_id"] == second
        assert len(block["elements"]) == len(next(b for b in original["function_blocks"] if b["id"] == block_id)["elements"])
