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


def preview_parts(resident_id):
    return {part["id"]: part for part in ok("PreviewPromptInjection", {"resident_id": resident_id})["parts"]}


def test_prompt_duty_enters_memory_and_leaves_with_removal(lineage):
    """只含提示词的职责进入所选记忆层级，撤下后随之消失"""
    from pathlib import Path

    worker = resident(lineage, "Worker")
    assert "duty_memory" not in preview_parts(worker)
    session = ok("CreateDuty", {"lineage_id": lineage, "holder_id": worker, "description": "看守发布",
                                "prompt": "发布后检查服务日志。"})["function_block"]
    assert session["elements"] == [] and session["duty"]["memory"] == {"level": "session", "prompt": "发布后检查服务日志。"}
    turn = ok("CreateDuty", {"lineage_id": lineage, "holder_id": worker, "description": "用中文",
                             "prompt": "回复使用中文。", "memory_level": "turn"})["function_block"]
    manual = ok("CreateDuty", {"lineage_id": lineage, "holder_id": worker, "description": "备份流程",
                               "prompt": "第一步：检查锁。", "memory_level": "on_demand"})["function_block"]
    assert [b["id"] for b in duties(lineage, worker)] == [session["id"], turn["id"], manual["id"]]

    parts = preview_parts(worker)
    listing = parts["duty_memory"]
    assert listing["timing"] == "session_start_and_after_compaction" and listing["label"] == "职责清单与说明"
    assert "1. 看守发布（说明见下）" in listing["content"] and "发布后检查服务日志。" in listing["content"]
    assert "2. 用中文（约束每轮附在消息前）" in listing["content"]
    assert parts["duty_memory_turn"]["timing"] == "every_turn" and "回复使用中文。" in parts["duty_memory_turn"]["content"]
    path = Path(listing["content"].split("完整说明在 ")[1].split(" 的")[0])
    assert path.name == "duty-manuals.md" and "第一步：检查锁。" in path.read_text(encoding="utf-8")
    assert "第一步：检查锁。" not in listing["content"]

    assert run("CreateDuty", {"lineage_id": lineage, "holder_id": worker, "description": "x",
                              "prompt": "y", "memory_level": "trigger"}).error
    assert run("UpdateDuty", {"lineage_id": lineage, "function_block_id": turn["id"], "prompt": ""}).error
    ok("UpdateDuty", {"lineage_id": lineage, "function_block_id": turn["id"], "memory_level": "session",
                      "prompt": "回复使用简体中文。"})
    for block in (session, turn, manual):
        ok("DeleteFunctionBlock", {"lineage_id": lineage, "function_block_id": block["id"]})
    parts = preview_parts(worker)
    assert "duty_memory" not in parts and "duty_memory_turn" not in parts
    assert not path.exists()
    assert {n["id"] for n in graph(lineage)["nodes"]} == {worker}


def test_trigger_prompt_joins_the_duty_wires(lineage):
    """“触发时”的提示词只随这条职责里触发持有者的连线送达"""
    from management_portal.society_service.duty_memory import trigger_prompt

    worker = resident(lineage, "Worker")
    block_id = apply(lineage, "scheduled_work", worker, task="Check work")
    updated = ok("UpdateDuty", {"lineage_id": lineage, "function_block_id": block_id,
                                "prompt": "先读昨天的记录。", "memory_level": "trigger"})["function_block"]
    body = graph(lineage)
    wires = {w["id"]: w for w in body["program"]["wires"]}
    owned = [e["id"] for e in updated["elements"] if e["kind"] == "wire" and wires[e["id"]]["target"] == worker]
    assert owned
    description = updated["duty"]["description"]
    assert trigger_prompt(body, owned[0], worker) == f"职责：{description}\n先读昨天的记录。"
    assert trigger_prompt(body, owned[0], "resident_000000000000") == ""
    assert trigger_prompt(body, "wire_other", worker) == ""
    assert "先读昨天的记录。" not in preview_parts(worker)["duty_memory"]["content"]


def test_first_step_duty_table_gains_memory_columns(lineage):
    """第一步建成的职责表在启动时补上提示词列，已有记录保留"""
    from management_portal.society_service import duty_storage

    worker = resident(lineage, "Worker")
    block = apply(lineage, "scheduled_work", worker, task="Check work")

    def downgrade_and_create(connection):
        connection.execute("DROP INDEX IF EXISTS function_block_duties_holder_idx")
        connection.execute("ALTER TABLE function_block_duties DROP COLUMN memory_level, DROP COLUMN memory_prompt")
        duty_storage.create_schema(connection)
        duty_storage.create_schema(connection)

    state_store.get_lineage_data_store().database.write(downgrade_and_create)
    with state_store.get_lineage_data_store().database.read() as connection:
        columns = {row["column_name"] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
            "AND table_name='function_block_duties'").fetchall()}
        assert columns == set(duty_storage.COLUMNS[duty_storage.TABLE])
        assert connection.execute("SELECT 1 FROM pg_indexes WHERE schemaname=current_schema() "
                                  "AND indexname='function_block_duties_holder_idx'").fetchone()
    assert [b["id"] for b in duties(lineage, worker)] == [block]


def test_duty_goes_with_its_holder(lineage):
    """持有者被删除后，块不再是职责，记录随之清除"""
    worker = resident(lineage, "Worker")
    block = apply(lineage, "scheduled_work", worker, task="Check work")
    ok("DeleteResident", {"lineage_id": lineage, "resident_id": worker})
    assert all("duty" not in b for b in graph(lineage)["function_blocks"])
    assert block not in stored_duty_ids()
