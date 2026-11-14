"""Duties: a template applied to a Resident becomes one of its duties.

:importance: P2
"""
from management_portal.society_service import state_store
from tests.cases.isa_test_support import ok, run
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, graph, lineage, resident  # noqa: F401


def template(builtin_key):
    return next(item for item in ok("ListTemplates", {})["templates"] if item["builtin_key"] == builtin_key)


def apply(lineage_id, builtin_key, resident_id, **parameters):
    return ok("ApplyTemplate", {
        "lineage_id": lineage_id, "template_id": template(builtin_key)["template_id"],
        "target": {"kind": "resident", "resident_ids": [resident_id]}, "parameters": parameters,
    })["function_block_ids"][0]


def duties(lineage_id, holder_id):
    blocks = [b for b in graph(lineage_id)["function_blocks"] if (b.get("duty") or {}).get("holder_id") == holder_id]
    return sorted(blocks, key=lambda b: b["duty"]["position"])


def stored_duty_ids():
    with state_store.get_lineage_data_store().database.read() as connection:
        return {row["function_block_id"] for row in connection.execute(
            "SELECT function_block_id FROM function_block_duties").fetchall()}


def test_applied_template_is_a_duty_and_removal_leaves_the_rest(lineage):
    """施加到 Resident 的模板登记为职责，撤下一条只删它自己的元素"""
    worker, other = resident(lineage, "Worker"), resident(lineage, "Other")
    reminder = apply(lineage, "fixed_reminder", worker, message="继续", max_rounds=3)
    schedule = apply(lineage, "scheduled_work", worker, task="Check work")
    elsewhere = apply(lineage, "fixed_reminder", other)
    first, second = duties(lineage, worker)
    assert [first["id"], second["id"]] == [reminder, schedule]
    assert first["duty"] == {
        "holder_id": worker, "description": template("fixed_reminder")["description"],
        "category": "common", "position": 0, "parameters": {"message": "继续", "max_rounds": 3},
    }
    assert second["duty"]["position"] == 1
    assert stored_duty_ids() == {reminder, schedule, elsewhere}

    reminder_elements = {e["id"] for e in first["elements"]}
    ok("DeleteFunctionBlock", {"lineage_id": lineage, "function_block_id": reminder})
    after = graph(lineage)
    remaining = {c["id"] for c in after["program"]["components"]} | {w["id"] for w in after["program"]["wires"]}
    assert not reminder_elements & remaining
    assert {n["id"] for n in after["nodes"]} == {worker, other}
    assert [b["id"] for b in duties(lineage, worker)] == [schedule]
    assert [b["id"] for b in duties(lineage, other)] == [elsewhere]
    assert stored_duty_ids() == {schedule, elsewhere}


def test_order_and_update_duties(lineage):
    """职责可以调整顺序、修改描述和类别"""
    worker = resident(lineage, "Worker")
    first = apply(lineage, "fixed_reminder", worker)
    second = apply(lineage, "scheduled_work", worker, task="Check work")
    ok("OrderDuties", {"lineage_id": lineage, "holder_id": worker, "function_block_ids": [second, first]})
    assert [b["id"] for b in duties(lineage, worker)] == [second, first]
    assert run("OrderDuties", {"lineage_id": lineage, "holder_id": worker,
                               "function_block_ids": [second]}).error
    updated = ok("UpdateDuty", {"lineage_id": lineage, "function_block_id": first,
                                "description": "  每轮结束提醒\n继续  ", "category": "custom"})["function_block"]
    assert updated["duty"]["description"] == "每轮结束提醒 继续"
    assert updated["duty"]["category"] == "custom"
    assert duties(lineage, worker)[1]["duty"]["description"] == "每轮结束提醒 继续"


def test_duty_goes_with_its_holder(lineage):
    """持有者被删除后，块不再是职责，记录随之清除"""
    worker = resident(lineage, "Worker")
    block = apply(lineage, "scheduled_work", worker, task="Check work")
    ok("DeleteResident", {"lineage_id": lineage, "resident_id": worker})
    assert all("duty" not in b for b in graph(lineage)["function_blocks"])
    assert block not in stored_duty_ids()
