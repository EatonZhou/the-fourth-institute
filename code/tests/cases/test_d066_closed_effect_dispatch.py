"""A disposed Effect must still identify a legacy intent before it can retire it."""

import pytest

from management_common.json_columns import json_text
from management_portal.society_service import family_strategy_state, family_task_dispatch_storage
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_family_familize import charters, lineage  # noqa: F401
from tests.cases.test_family_tasks import configured_family  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


def test_closed_effect_with_same_event_does_not_prove_the_frozen_dispatch(configured_family):
    graph, block = configured_family
    lineage_id = graph["lineage_id"]
    facade, steward = (block["family"][key] for key in ("facade", "steward"))
    message_id = "msg_" + "d" * 32
    trigger_path_id = "trigger_path_aaaaaaaaaaaa"
    event = {"id": "event_aaaaaaaaaaaaaaaa", "type": "resident.queue_pressure_exceeded",
             "subject_resident_id": facade}
    intent = {
        "request": {
            "target": steward, "facade": facade, "trigger": "queue", "business_ref": "duty/family-queue",
            "facts": {"event": event, "facade": facade, "family_id": block["id"], "lineage_id": lineage_id},
        },
        "initiated_by": facade,
        "text": "Frozen queue reminder",
    }
    effect = {
        "calls": {"c1": {
            "instruction": "DispatchFamilyTask", "instruction_id": "inst_lin_" + "a" * 20,
            "status": "unresolved", "caller": facade, "business_ids": {},
            "error": {"code": "instruction.failed", "commit_state": "unknown"},
        }},
        "disposition": {"actor": "sample_admin", "status": "failed", "occurred_at": "2026-01-02T00:00:00Z"},
    }
    db = get_lineage_data_store().database
    with db.transaction() as c:
        family_task_dispatch_storage.create_schema(c)
        c.execute("INSERT INTO family_task_dispatches VALUES (%s,%s,%s)", (lineage_id, message_id, json_text(intent)))
        c.execute(
            "INSERT INTO trigger_paths (trigger_path_id,lineage_id,root_kind,occurrence_id,root_node_id,"
            "cause_kind,cause_id,occurred_at,input_json,status,finished_at) "
            "VALUES (%s,%s,'event',%s,'event_source_aaaaaaaaaaaa','external',%s,now(),%s,'failed',now())",
            (trigger_path_id, lineage_id, event["id"], event["id"], json_text(event)),
        )
        c.execute(
            "INSERT INTO trigger_path_steps (step_id,trigger_path_id,node_id,kind,activation_key,status,"
            "created_at,started_at,finished_at,result_json) "
            "VALUES ('trigger_step_aaaaaaaaaaaa',%s,'effect_aaaaaaaaaaaa','effect',%s,'failed',"
            "now(),now(),now(),%s)",
            (trigger_path_id, "effect:" + "a" * 40, json_text(effect)),
        )

    with db.readonly_snapshot() as c:
        assert family_strategy_state.check_legacy_dispatches(c) == [{
            "kind": "legacy_family_dispatch", "lineage_id": lineage_id,
            "message_id": message_id, "reason": "unaccepted_frozen_intent",
        }]
    with pytest.raises(ValueError, match="unaccepted_frozen_intent"):
        family_strategy_state.retire_legacy_dispatches()

    with db.transaction() as c:
        assert c.execute("SELECT intent_json FROM family_task_dispatches WHERE lineage_id=%s",
                         (lineage_id,)).fetchone()["intent_json"] == intent
        assert c.execute("SELECT result_json FROM trigger_path_steps WHERE trigger_path_id=%s",
                         (trigger_path_id,)).fetchone()["result_json"] == effect
        c.execute("DROP TABLE family_task_dispatches")
