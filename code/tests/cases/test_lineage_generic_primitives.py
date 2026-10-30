""":importance: P2"""
from __future__ import annotations

from tests.cases import society_service_test_support as lineage_test_support
from tests.cases.society_service_test_support import install_resource_boundary

import json
import math
from copy import deepcopy
from pathlib import Path

import pytest

from management_common.principal import OperationIdentity
from management_common.operation_errors import OperationError

from management_portal.society_service import (
    lineage_domain,
    lineage_graph_edits,
    lineage_lifecycle,
    lineage_program_edits,
    lineage_program_effect_execution,
    lineage_reads,
    lineage_state,
)
from management_portal.society_service import lineage_event_conditions, lineage_program_execution
from management_portal.society_service import component_runtime, lineage_graph_state
from management_portal.society_service import invariant_locks, state_store
from management_portal.society_service import lineage_primitives
from management_portal.society_service import lineage_program
from management_portal.society_service import lineage_program_runtime
from management_portal.society_service.lineage_effect_script import deliver_script_parts
from management_portal.society_service import lineage_aggregate
from tests.cases.effect_script_support import effect_config, lower_effect_call


ACTOR = "sample_admin"
# Generic Effects (decision_4adcc1ff2f2c): the instruction an Effect calls, its
# argument sources and named endpoints.
DELIVER = effect_config("DeliverMessage", {"resident_id": {"from": "endpoint.target"}}, ["target"])
# An Effect that succeeds without side effects, for outcome chains.
QUIET = {"script": "ListComponentTypes", "endpoints": []}
HANDOFF = {"script": "CreateWorkspaceHandoff", "endpoints": ["source", "target"]}
WORK_A = "resident_aaaaaaaaaaaa"
WORK_B = "resident_bbbbbbbbbbbb"
WORK_C = "resident_cccccccccccc"
WORK_D = "resident_dddddddddddd"
REDUCER = "reducer_eeeeeeeeeeee"
REDUCER_EXECUTOR = "resident_eeeeeeeeeeee"


def _event_payload_without_path_id(event: dict) -> dict:
    payload = dict(event["payload"])
    path_id = payload.pop("trigger_path_id")
    assert isinstance(path_id, str) and path_id.startswith("trigger_path_")
    return payload


def _packet_reply(prompt: str) -> str:
    return (
        "<packet>"
        + json.dumps(
            {'key': 'default', 'priority': 0, 'message': {'text': prompt, 'data': {}}},
            ensure_ascii=False,
        )
        + "</packet>"
    )


def _reducer_reply(prompt: str) -> str:
    return _packet_reply(prompt).replace("packet>", "reducer-result>")


def _worker(resident_id: str, title: str = "") -> dict:
    return {
        "id": resident_id,
        "title": title or resident_id,
        "status": "idle",
    }


def _reducer_executor() -> dict:
    return {
        **_worker(REDUCER_EXECUTOR, "Reducer executor"),
        "operator_executor": {
            "component_id": REDUCER,
            "kind": "reducer",
        },
    }


def _reducer_component() -> dict:
    return {
        "id": REDUCER,
        "kind": "reducer",
        "title": "Reducer",
        "config": {
            "operator_implementation": "resident",
            "resident_executor_id": REDUCER_EXECUTOR,
            "prompt": "Reduce.",
            "reduce_strategy": "join",
            "reduce_separator": "\n\n",
            "output_key": "reduced",
        },
    }


@pytest.fixture()
def primitive_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    root = tmp_path / "lineages"
    monkeypatch.setattr(lineage_aggregate, "DATA_ROOT", root)
    monkeypatch.setattr(lineage_state, "LINEAGE_FOLDER_ROOT", root / "lineages")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(root / "lineages"))
    install_resource_boundary(monkeypatch)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda _node, _actor: False,
    )
    monkeypatch.setattr(
        lineage_test_support,
        "LATEST_COMPLETED_MESSAGE_RESOLVER",
        lambda _resident_id, _actor: {},
    )
    monkeypatch.setattr(lineage_test_support, "RESIDENT_INPUT_STATE_RESOLVER", None)
    return root


@pytest.mark.parametrize("entry", ["create", "patch", "clone", "initial"])
def test_all_primitive_creation_paths_allocate_without_overlaps(primitive_store: Path, entry: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """新建、补丁、克隆和初始组件都自动摆在附近且互不重叠，手动位置保持不变"""
    clock = {"now": "2026-01-01T00:00:00Z"}
    monkeypatch.setattr(lineage_state, "_now_iso", lambda: clock["now"])
    monkeypatch.setattr(lineage_domain, "_now_iso", lambda: clock["now"])
    component = {
        "id": "event_source_placement_template", "kind": "event_source", "title": "验收完成→监督",
        "x": 300, "y": -250, "config": {"event_type": "manual.triggered"},
    }
    initial = [dict(component, id=f"event_source_initial_{index}") for index in range(5)] if entry == "initial" else [component]
    lineage = lineage_lifecycle.create_lineage({
        "title": "Primitive placement",
        "nodes": [{**_worker(WORK_A), "x": 300, "y": -250}], "edges": [],
        "program": {"components": initial, "wires": []},
    }, ACTOR)
    before = lineage_reads.get_lineage(lineage["lineage_id"], ACTOR)
    lineage_id = lineage["lineage_id"]
    store = lineage_aggregate.lineage_catalog_store().state_store
    residents_before = store.residents_by_id([WORK_A])
    clock["now"] = "2026-01-01T00:00:01Z"
    if entry == "create":
        for index in range(5):
            lineage = lineage_program_edits.create_lineage_program_component(lineage_id, {
                "kind": "event_source", "title": f"自动事件 {index}",
                "expected_revision": lineage["revision"], "x": None, "y": None,
                "created_by_principal_id": WORK_A,
                "config": {"event_type": "manual.triggered"},
            }, ACTOR)["lineage"]
    elif entry == "patch":
        lineage = lineage_graph_edits.apply_lineage_graph_patch(lineage_id, {
            "expected_revision": lineage["revision"],
            "patch": {"program": {"components": [
                *lineage["program"]["components"],
                *(dict(component, id=f"event_source_patch_{index}") for index in range(5)),
            ], "wires": []}},
        }, ACTOR)
    elif entry == "clone":
        lineage = lineage_graph_edits.apply_lineage_changes(lineage_id, {
            "program_operations": [
                {"op": "clone", "kind": "component", "id": component["id"], "new_id": f"event_source_clone_{index}"}
                for index in range(5)
            ],
        }, ACTOR)["lineage"]
    current = lineage_reads.get_lineage(lineage_id, ACTOR)
    objects = [*current["nodes"], *current["program"]["components"]]
    assert [{key: value for key, value in node.items() if key != "updated_at"} for node in current["nodes"]] == [
        {key: value for key, value in node.items() if key != "updated_at"} for node in before["nodes"]
    ]
    assert store.residents_by_id([WORK_A]) == residents_before
    original = before["program"]["components"][0]
    assert next(c for c in current["program"]["components"] if c["id"] == original["id"]) == original
    for index, item in enumerate(objects):
        assert math.dist((item["x"], item["y"]), (300, -250)) < 65
        width, height = (142, 76) if item.get("kind") else (154, 94)
        for other in objects[index + 1:]:
            other_width, other_height = (142, 76) if other.get("kind") else (154, 94)
            assert (
                abs(item["x"] - other["x"]) >= ((width + other_width) / 2 + 24) / 12.8
                or abs(item["y"] - other["y"]) >= ((height + other_height) / 2 + 24) / 9
            )
    # An explicit edit of an existing component remains a user-owned placement.
    moved = dict(current["program"]["components"][0], x=300, y=-250)
    lineage_lifecycle.update_lineage_layout(lineage_id, {
        "positions": [{"id": moved["id"], "x": 300, "y": -250}],
    }, ACTOR)
    result = lineage_program_edits.save_lineage_program_component(lineage_id, {
        "expected_revision": current["revision"], "component_id": moved["id"], "component": {"title": "保留手动位置"},
    }, ACTOR)
    assert result["component"]["x"] == 300 and result["component"]["y"] == -250


def test_event_routes_and_effect_endpoints_are_generic() -> None:
    """事件可同时路由到 Resident 和 Effect，Effect 端点按连线端口名解析"""
    program = lineage_program.normalize_program(
        {
            "components": [
                {
                    "id": "event_source_manual_generic",
                    "kind": "event_source",
                    "config": {
                        "event_type": "manual.triggered",
                    },
                },
                {
                    "id": "effect_emit_generic",
                    "kind": "effect",
                    "config": deepcopy(QUIET),
                },
                {
                    "id": "effect_handoff_generic",
                    "kind": "effect",
                    "config": deepcopy(HANDOFF),
                },
                {
                    "id": "event_source_emitted_generic",
                    "kind": "event_source",
                    "config": {
                        "event_type": "effect.succeeded",
                    },
                },
            ],
            "wires": [
                {'id': 'wire_event_worker', 'kind': 'route', 'source': 'event_source_manual_generic', 'target': WORK_A, 'config': {'invoke_profiles': {'agent': {'arguments': {'prompt': {'value': 'Run the worker.'}}}}}},
                {'id': 'wire_event_effect', 'kind': 'route', 'source': 'event_source_manual_generic', 'target': 'effect_emit_generic'},
                {
                    "id": "wire_watch_emitted_generic",
                    "kind": "watch",
                    "source": "event_source_emitted_generic",
                    "source_port": "watch",
                    "target": "effect_emit_generic",
                },
                {'id': 'wire_emitted_worker', 'kind': 'route', 'source': 'event_source_emitted_generic', 'target': WORK_B},
                {
                    "id": "wire_handoff_source",
                    "kind": "target",
                    "source": "effect_handoff_generic",
                    "source_port": "source",
                    "target": WORK_A,
                },
                {
                    "id": "wire_handoff_target",
                    "kind": "target",
                    "source": "effect_handoff_generic",
                    "source_port": "target",
                    "target": WORK_B,
                },
            ],
        },
        lineage_nodes=[_worker(WORK_A), _worker(WORK_B)],
    )
    plan = lineage_program.compile_program(
        program,
        [_worker(WORK_A), _worker(WORK_B)],
    )

    assert plan["event_sources"]["event_source_manual_generic"][
        "subscriber_resident_ids"
    ] == [WORK_A]
    assert plan["event_sources"]["event_source_manual_generic"][
        "subscriber_effect_ids"
    ] == ["effect_emit_generic"]
    assert plan["event_sources"]["event_source_emitted_generic"]["event_type"] == (
        "effect.succeeded"
    )
    assert plan["effects"]["effect_handoff_generic"]["endpoints"] == {
        "source": WORK_A,
        "target": WORK_B,
    }


def test_invoke_wire_mapping_combines_prompt_and_overrides_other_defaults() -> None:
    """调用连线的参数映射把 prompt 接在 Effect 默认消息之后，其他参数覆盖默认值"""
    plan = lineage_program.compile_program(
        {
            "components": [
                {
                    "id": "event_source_mapping",
                    "kind": "event_source",
                    "config": {
                        "event_type": "manual.triggered",
                    },
                },
                {
                    "id": "effect_mapping",
                    "kind": "effect",
                    "config": effect_config("DeliverMessage", {
                            "resident_id": {"from": "endpoint.target"},
                            "message": {"value": "Default task."},
                        }, ["target"]),
                },
            ],
            "wires": [
                {'id': 'wire_mapping_invoke', 'kind': 'route', 'source': 'event_source_mapping', 'target': 'effect_mapping', 'config': {'invoke_profiles': {'effect': {'arguments': {'prompt': {'from': 'event.payload.prompt'}, 'priority': {'value': 3}, 'created_resident_id': {'from': 'event.outputs.created_resident_id'}, 'optional': {'from': 'event.payload.absent', 'default': 'fallback'}}}}}},
                {
                    "id": "wire_mapping_endpoint",
                    "kind": "target",
                    "source": "effect_mapping",
                    "source_port": "target",
                    "target": WORK_A,
                },
            ],
        },
        [_worker(WORK_A)],
    )
    route = plan["event_sources"]["event_source_mapping"][
        "subscriber_routes"
    ][0]
    event = lineage_primitives.make_event(
        "manual.triggered",
        payload={
            "prompt": "Mapped task.",
            "effect_type": "fork_resident",
            "outputs": {"created_resident_id": WORK_B},
        },
        correlation="argument-mapping",
    )
    invocation = lineage_program_execution._make_program_effect_invocation(
        event,
        source_kind="packet",
        source_id="event_source_mapping",
        source_wire_id="wire_mapping_invoke",
        argument_mapping=route["invocation"]["arguments"],
    )
    call = lower_effect_call("lineage_aaaaaaaaaaaa", "effect_mapping", plan["effects"]["effect_mapping"], invocation)

    assert invocation.arguments == {
        "prompt": "Mapped task.",
        "priority": 3,
        "created_resident_id": WORK_B,
        "optional": "fallback",
    }
    assert call["instruction"] == "DeliverMessage"
    assert call["arguments"]["resident_id"] == WORK_A
    assert call["arguments"]["message"]["content_parts"][0]["text"] == "Default task.\n\nMapped task."
    endpoint = next(
        wire
        for wire in plan["program"]["wires"]
        if wire["id"] == "wire_mapping_endpoint"
    )
    assert "config" not in endpoint


def test_event_rejects_invalid_prompt_instead_of_stringifying_or_truncating(subtests, isolated_portal_case):
    """事件 prompt 不是字符串或超过长度上限时直接拒绝，不转字符串也不截断"""
    for request_prompt in [42, {"paper_id": "paper_example"}, "x" * (lineage_primitives.MAX_INVOCATION_PROMPT_CHARS + 1)]:
        with subtests.test(request_prompt=request_prompt), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match="prompt"):
                lineage_primitives.make_event("manual.triggered", payload={"prompt": request_prompt})


def test_event_prompt_survives_serialization_beyond_metadata_text_limit_contracts(subtests, isolated_portal_case):
    """事件 prompt 不截断且合并超限被拒，参数可映射 packet，watch 决定主体并可引用外部 Resident"""
    with subtests.test(contract='test_event_prompt_survives_serialization_beyond_metadata_text_limit'), isolated_portal_case() as case_path:
        prompt = "通用任务信息" * 2000
        event = lineage_primitives.make_event("manual.triggered", payload={"prompt": prompt})
        restored = lineage_primitives.normalize_event(json.loads(json.dumps(event.to_dict())))
        assert restored.payload["prompt"] == prompt
    with subtests.test(contract='test_effect_rejects_combined_prompt_overflow_without_changing_definition'), isolated_portal_case() as case_path:
        definition = {**effect_config("DeliverMessage", {
            "resident_id": {"from": "endpoint.target"}, "message": {"value": "x" * 16000}}), "endpoints": {"target": WORK_A}}
        original = deepcopy(definition)
        event = lineage_primitives.make_event("manual.triggered")
        invocation = lineage_primitives.make_effect_invocation(event, arguments={"prompt": "r" * (lineage_primitives.MAX_INVOCATION_PROMPT_CHARS - 16000)})
        with pytest.raises(ValueError, match=str(lineage_primitives.MAX_INVOCATION_PROMPT_CHARS)):
            lower_effect_call("lineage_aaaaaaaaaaaa", "effect_prompt", definition, invocation)
        assert definition == original
    with subtests.test(contract='test_packet_mapping_can_read_packet_envelope'), isolated_portal_case() as case_path:
        event = lineage_primitives.make_event(
            "manual.triggered",
            payload={"name": "runtime"},
            correlation="packet-mapping",
        )
        invocation = lineage_program_execution._make_program_effect_invocation(
            event,
            source_kind="packet",
            source_id=WORK_D,
            source_wire_id="wire_packet_mapping",
            prompt="Role packet.",
            from_resident_id=WORK_D,
            packet=lineage_primitives.packet_envelope(
                lineage_primitives.make_packet("role", "Role packet."),
                event_id=event.id, from_node_id=WORK_D, source_wire_id="wire_packet_mapping",
            ),
            argument_mapping={
                "prompt": {"from": "packet.message.text"},
                "author": {"from": "packet.from_resident_id"},
                "topic": {"from": "event.payload.name"},
            },
        )

        assert invocation.arguments == {
            "prompt": "Role packet.",
            "author": WORK_D,
            "topic": "runtime",
        }
    with subtests.test(contract='test_watch_wire_is_the_only_event_subject_authority'), isolated_portal_case() as case_path:
        program = lineage_program.normalize_program(
            {
                "components": [
                    {
                        "id": "event_source_observed",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    },
                    {
                        "id": "event_source_delivery_failure",
                        "kind": "event_source",
                        "config": {
                            "event_type": "event.delivery_failed",
                        },
                    },
                ],
                "wires": [
                    {'id': 'wire_observed_delivery', 'kind': 'route', 'source': 'event_source_observed', 'target': WORK_A},
                    {
                        "id": "wire_watch_exact_delivery",
                        "kind": "watch",
                        "source": "event_source_delivery_failure",
                        "source_port": "watch",
                        "target": "wire_observed_delivery",
                    },
                ],
            },
            lineage_nodes=[_worker(WORK_A)],
        )
        watcher = next(
            component
            for component in program["components"]
            if component["id"] == "event_source_delivery_failure"
        )
        assert not any(key.startswith("subject_") for key in watcher)
        compiled = lineage_program.compile_program(program, [_worker(WORK_A)])
        runtime_watcher = compiled["event_sources"][
            "event_source_delivery_failure"
        ]
        assert runtime_watcher["subject_wire_id"] == "wire_observed_delivery"
        assert runtime_watcher["watch_wire_id"] == "wire_watch_exact_delivery"
    with subtests.test(contract='test_program_accepts_external_resident_event_and_effect_references'), isolated_portal_case() as case_path:
        external_observer_subject = "resident_111111111111"
        external_effect_target = "resident_222222222222"
        plan = lineage_program.compile_program(
            {
                "components": [
                    {
                        "id": "event_source_external_done",
                        "kind": "event_source",
                        "config": {"event_type": "resident.work_finished"},
                    },
                    {
                        "id": "effect_external_message",
                        "kind": "effect",
                        "config": deepcopy(DELIVER),
                    },
                ],
                "wires": [
                    {
                        "id": "wire_external_done_watch",
                        "kind": "watch",
                        "source": "event_source_external_done",
                        "target": external_observer_subject,
                    },
                    {'id': 'wire_external_done_effect', 'kind': 'route', 'source': 'event_source_external_done', 'target': 'effect_external_message'},
                    {
                        "id": "wire_external_message_target",
                        "kind": "target",
                        "source": "effect_external_message",
                        "target": external_effect_target,
                    },
                ],
            },
            [_worker(WORK_A)],
        )

        assert plan["event_sources"]["event_source_external_done"][
            "subject_resident_id"
        ] == external_observer_subject
        assert plan["effects"]["effect_external_message"]["endpoints"] == {
            "target": external_effect_target
        }


def test_connecting_event_wires_preserves_authored_event_configuration(
    primitive_store: Path,
) -> None:
    """给事件源接 watch 和路由连线不改动其已写好的配置"""
    del primitive_store
    expected_config = {
        "event_type": "resident.state_changed",
        "max_fires": 2,
        "state_domain": "intake",
    }
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Event config survives connections",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_preserved_config",
                        "kind": "event_source",
                        "title": "Preserved Event",
                        "config": expected_config,
                    }
                ],
                "wires": [],
            },
        },
        ACTOR,
    )

    watched = lineage_program_edits.save_lineage_program_wire(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "kind": "watch",
            "source": "event_source_preserved_config",
            "source_port": "watch",
            "target": WORK_A,
        },
        ACTOR,
        operation="create",
    )
    component = next(
        item
        for item in watched["lineage"]["program"]["components"]
        if item["id"] == "event_source_preserved_config"
    )
    assert component["config"] == expected_config

    connected = lineage_program_edits.save_lineage_program_wire(
        created["lineage_id"],
        {'expected_revision': watched['lineage']['revision'], 'kind': 'route', 'source': 'event_source_preserved_config', 'source_port': 'output', 'target': WORK_B, 'target_port': 'invoke'},
        ACTOR,
        operation="create",
    )
    component = next(
        item
        for item in connected["lineage"]["program"]["components"]
        if item["id"] == "event_source_preserved_config"
    )
    assert component["config"] == expected_config


def test_permanent_resident_delete_keeps_the_seeds_it_wrote_or_started(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """永久删除 Resident 时保留它写下或作为起点的 Seed，只去掉以它为起点的候选分叉边

    Seeds stay when a Resident is deleted (decision_5b5653c3eaa5).

    Nothing on a Seed names the Resident that wrote its Markdown; a Seed whose
    candidate fork starts from the deleted Resident only loses that relation.
    """

    del primitive_store
    deleted_residents: list[str] = []
    monkeypatch.setattr(
        lineage_domain.resident_domain,
        "delete_resident_identities",
        lambda lineage_id, ids, _actor: deleted_residents.extend(ids),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Permanent Resident delete",
            "leader_resident_id": WORK_A,
            "nodes": [
                _worker(WORK_A, "Leader"),
                _worker(WORK_B, "Worker"),
            ],
            "program": {
                "components": [
                    {
                        "id": "seed_written_by_the_deleted",
                        "kind": "seed",
                        "title": "Written",
                        "config": {
                            "source_resident_id": WORK_A,
                            "artifact_path": f"seeds/{WORK_B}/idea-01.md",
                        },
                    },
                    {
                        "id": "seed_started_from_the_deleted",
                        "kind": "seed",
                        "title": "Started",
                        "config": {"source_resident_id": WORK_B},
                    },
                ],
                "wires": [],
            },
        },
        ACTOR,
    )

    result = lineage_graph_edits.delete_lineage_graph_objects(
        str(created["lineage_id"]),
        {
            "resident_ids": [WORK_B],
            "expected_revision": created["revision"],
        },
        ACTOR,
    )

    assert deleted_residents == [WORK_B]
    assert result["removed_seed_count"] == 0
    assert {
        item["id"] for item in result["lineage"]["program"]["components"]
    } == {"seed_written_by_the_deleted", "seed_started_from_the_deleted"}
    assert [
        (edge["source"], edge["target"])
        for edge in result["lineage"]["edges"]
        if edge["type"] == "candidate_fork"
    ] == [(WORK_A, "seed_written_by_the_deleted")]
    assert all(
        item.get("id") != WORK_B
        for item in result["lineage"]["nodes"]
    )


def test_graph_mutation_add_clone_remove_is_atomic(
    primitive_store: Path,
) -> None:
    """ApplyLineageChanges 的增加、克隆、设置和删除一次提交，任一步失败则整体回滚"""
    del primitive_store
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Atomic graph mutation",
            "nodes": [_worker(WORK_A)],
            "edges": [],
            "program": {
                "components": [
                    {
                        "id": "event_source_mutation_template",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    }
                ],
                "wires": [
                    {'id': 'wire_mutation_old_route', 'kind': 'route', 'source': 'event_source_mutation_template', 'target': WORK_A}
                ],
            },
        },
        ACTOR,
    )
    lineage_id = str(created["lineage_id"])
    before_revision = int(created["revision"])
    result = lineage_graph_edits.apply_lineage_changes(
        lineage_id,
        {
            "program_operations": [
                {
                    "op": "clone",
                    "kind": "component",
                    "id": "event_source_mutation_template",
                    "new_id": "event_source_mutation_branch",
                    "overrides": {"title": "Branch event"},
                },
                {
                    "op": "add",
                    "kind": "component",
                    "value": {
                                 "id": "effect_mutation_branch",
                                 "kind": "effect",
                                 "config": deepcopy(QUIET),
                             },
                },
                {
                    "op": "add",
                    "kind": "wire",
                    "value": {'id': 'wire_mutation_branch_effect', 'kind': 'route', 'source': 'event_source_mutation_branch', 'source_port': 'output', 'target': 'effect_mutation_branch', 'target_port': 'invoke'},
                },
                {
                    "op": "clone",
                    "kind": "wire",
                    "id": "wire_mutation_branch_effect",
                    "new_id": "wire_mutation_branch_agent",
                    "overrides": {"target": WORK_A},
                },
                {
                    "op": "set",
                    "kind": "wire",
                    "id": "wire_mutation_branch_agent",
                    "field": "config.invoke_profiles",
                    "value": {
                        "agent": {
                            "arguments": {
                                "prompt": {"value": "Run the branch."},
                            }
                        }
                    },
                },
                {
                    "op": "remove",
                    "kind": "wire",
                    "id": "wire_mutation_old_route",
                },
            ]
        },
        ACTOR,
    )

    assert result["revision"] == before_revision + 1
    evolved = lineage_reads.get_lineage(lineage_id, ACTOR)
    components = {
        str(item["id"]): item
        for item in evolved["program"]["components"]
    }
    wires = {
        str(item["id"]): item
        for item in evolved["program"]["wires"]
    }
    assert {
        "event_source_mutation_branch",
        "effect_mutation_branch",
    } <= set(components)
    assert "wire_mutation_old_route" not in wires
    assert wires["wire_mutation_branch_agent"]["config"][
        "invoke_profiles"
    ]["agent"]["arguments"]["prompt"] == {"value": "Run the branch."}

    failed_revision = int(evolved["revision"])
    with pytest.raises(ValueError):
        lineage_graph_edits.apply_lineage_changes(
            lineage_id,
            {
                "program_operations": [
                    {
                        "op": "set",
                        "kind": "component",
                        "id": "event_source_mutation_branch",
                        "field": "title",
                        "value": "Must roll back",
                    },
                    {
                        "op": "set",
                        "kind": "wire",
                        "id": "wire_mutation_branch_agent",
                        "field": "target",
                        "value": "resident_ffffffffffff",
                    },
                ]
            },
            ACTOR,
        )
    unchanged = lineage_reads.get_lineage(lineage_id, ACTOR)
    assert unchanged["revision"] == failed_revision
    unchanged_component = next(
        item
        for item in unchanged["program"]["components"]
        if item["id"] == "event_source_mutation_branch"
    )
    assert unchanged_component.get("title") == "Branch event"


def test_effect_outcome_chain_rejects_a_synchronous_cycle(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个 Effect 的成功事件互相触发形成同步循环时，再次进入被拒绝"""
    del primitive_store
    calls: list[str] = []
    original_definition = lineage_program_effect_execution.effect_definition

    def observed_definition(plan, effect_id):
        calls.append(effect_id)
        return original_definition(plan, effect_id)

    monkeypatch.setattr(lineage_program_effect_execution, "effect_definition", observed_definition)
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Outcome cycle fence",
            "nodes": [_worker(WORK_A)],
            "edges": [],
            "program": {
                "components": [
                    {
                        "id": "event_source_cycle_start",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    },
                    {
                        "id": "effect_cycle_a",
                        "kind": "effect",
                        "config": deepcopy(QUIET),
                    },
                    {
                        "id": "effect_cycle_b",
                        "kind": "effect",
                        "config": deepcopy(QUIET),
                    },
                    {
                        "id": "event_source_cycle_a_succeeded",
                        "kind": "event_source",
                        "config": {
                            "event_type": "effect.succeeded",
                        },
                    },
                    {
                        "id": "event_source_cycle_b_succeeded",
                        "kind": "event_source",
                        "config": {
                            "event_type": "effect.succeeded",
                        },
                    },
                ],
                "wires": [
                    {
                        "id": "wire_watch_cycle_a",
                        "kind": "watch",
                        "source": "event_source_cycle_a_succeeded",
                        "source_port": "watch",
                        "target": "effect_cycle_a",
                    },
                    {
                        "id": "wire_watch_cycle_b",
                        "kind": "watch",
                        "source": "event_source_cycle_b_succeeded",
                        "source_port": "watch",
                        "target": "effect_cycle_b",
                    },
                    {'id': 'wire_cycle_start_a', 'kind': 'route', 'source': 'event_source_cycle_start', 'target': 'effect_cycle_a'},
                    {'id': 'wire_cycle_a_b', 'kind': 'route', 'source': 'event_source_cycle_a_succeeded', 'source_port': 'output', 'target': 'effect_cycle_b', 'target_port': 'invoke'},
                    {'id': 'wire_cycle_b_a', 'kind': 'route', 'source': 'event_source_cycle_b_succeeded', 'source_port': 'output', 'target': 'effect_cycle_a', 'target_port': 'invoke'},
                ],
            },
        },
        ACTOR,
    )
    event = lineage_primitives.make_event(
        "manual.triggered",
        payload={"triggered_by": ACTOR},
        correlation="cycle",
    )
    result = lineage_domain.dispatch_lineage_program_event(
        str(created["lineage_id"]),
        "event_source_cycle_start",
        event,
        ACTOR,
        cursor="manual:cycle",
    )

    assert calls == ["effect_cycle_a", "effect_cycle_b"]
    nested = result["results"][0]["outcome_watcher_dispatches"][0][
        "results"
    ][0]["outcome_watcher_dispatches"][0]["results"][0]
    assert nested["status"] == "failed"
    assert "同步循环" in nested["error"]


def test_resident_state_events_observe_only_the_intake_domain() -> None:
    """resident.state_changed 只能观察 intake 状态域，其他状态域被拒绝"""
    nodes = [_worker(WORK_A)]
    program = lineage_program.normalize_program(
        {
            "components": [
                {
                    "id": "event_source_state_contract",
                    "kind": "event_source",
                    "config": {"event_type": "resident.state_changed", "state_domain": "intake", "max_fires": 3},
                },
                {
                    "id": "effect_state_contract",
                    "kind": "effect",
                    "config": effect_config("OpenIntake", {"resident_id": {"from": "endpoint.target"},
                                                           "open": {"value": False}}, ["target"]),
                },
            ],
            "wires": [
                {"id": "wire_state_effect_target", "kind": "target", "source": "effect_state_contract",
                 "source_port": "target", "target": WORK_A},
            ],
        },
        lineage_nodes=nodes,
    )
    components = {component["id"]: component for component in program["components"]}
    assert components["event_source_state_contract"]["config"]["max_fires"] == 3
    assert components["event_source_state_contract"]["config"]["state_domain"] == "intake"
    assert components["effect_state_contract"]["config"]["script"] == 'OpenIntake resident_id="${endpoint.target}" open:=false'
    with pytest.raises(ValueError, match="只能是 intake"):
        lineage_program.normalize_program(
            {"components": [{"id": "event_source_state_invalid", "kind": "event_source",
                             "config": {"event_type": "resident.state_changed", "state_domain": "role.lifecycle"}}],
             "wires": []},
            lineage_nodes=nodes,
        )


def test_all_idle_events_fire_only_on_nonzero_to_zero_transitions(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """residents.all_idle 只在全系统运行中的 Resident 数由非零变为零时触发"""
    outside = "resident_ffffffffffff"
    activity = {
        WORK_A: True,
        WORK_B: False,
        WORK_D: True,
        outside: True,
    }
    resolver_calls: list[list[str] | None] = []
    sent: list[str] = []

    def activity_snapshot(
        resident_ids: list[str] | None,
    ) -> dict:
        resolver_calls.append(resident_ids)
        selected_ids = (
            sorted(activity)
            if resident_ids is None
            else sorted(set(resident_ids))
        )
        active_ids = [
            resident_id
            for resident_id in selected_ids
            if activity.get(resident_id, False)
        ]
        return {
            "resident_ids": selected_ids,
            "active_resident_ids": active_ids,
            "residents": {
                resident_id: {
                    "resident_id": resident_id,
                    "active": resident_id in active_ids,
                    "status": (
                        "running"
                        if resident_id in active_ids
                        else "idle"
                    ),
                }
                for resident_id in selected_ids
            },
        }

    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_ACTIVITY_RESOLVER",
        activity_snapshot,
    )
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_idle_{len(sent)}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Resident activity Events",
            "nodes": [
                _worker(WORK_A),
                _worker(WORK_B),
                _worker(WORK_D, "Proposer"),
            ],
            "program": {
                "components": [
                    {
                        "id": "event_source_global_idle",
                        "kind": "event_source",
                        "config": {
                            "event_type": "residents.all_idle",
                        },
                    },
                ],
                "wires": [
                    {'id': 'wire_global_idle_target', 'kind': 'route', 'source': 'event_source_global_idle', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )

    baseline = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    activity[WORK_A] = False
    activity[WORK_D] = False
    local_lineage_idle = (
        lineage_domain.reconcile_lineage_system_events(
            created["lineage_id"],
            ACTOR,
        )
    )
    activity[outside] = False
    global_idle = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    unchanged = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )

    assert baseline["baseline_count"] == 1
    assert baseline["event_count"] == 0
    assert local_lineage_idle["event_count"] == 0
    assert global_idle["event_count"] == 1
    assert _event_payload_without_path_id(global_idle["events"][0]["event"]) == {
        "resident_scope": "system",
        "resident_count": 4,
        "previous_running_count": 1,
        "running_count": 0,
    }
    assert unchanged["event_count"] == 0
    assert sent == [WORK_B]
    assert resolver_calls == [None, None, None, None]


def test_context_pressure_event_fires_once_per_threshold_entry(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """context 占用每次越过阈值只触发一次，回落到阈值后再越过才再触发"""
    from management_portal.society_service import resident_directory_domain

    pressure: dict[str, float | None] = {"used_percent": None}
    observed_sessions: list[set[str]] = []
    sent: list[str] = []

    def context_pressures(
        sessions: list[str],
        actor: str,
    ) -> dict[str, dict[str, object]]:
        assert actor == WORK_A
        observed_sessions.append(set(sessions))
        return {
            WORK_A: {
                "context_tokens": 222_224,
                "model_context_window": 258_400,
                "used_percent": pressure["used_percent"],
                "heat_level": "hot",
                "updated_at": "2026-08-01T12:00:00Z",
            }
        }

    # The Event runtime reads pressures through the Resident domain port, which
    # delegates to the directory domain.
    monkeypatch.setattr(
        resident_directory_domain,
        "get_resident_context_pressures",
        context_pressures,
    )
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_pressure_{len(sent)}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Resident context pressure Event",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_context_pressure",
                        "kind": "event_source",
                        "config": {
                            "event_type": "resident.context_pressure_exceeded",
                            "threshold_percent": 80,
                        },
                    }
                ],
                "wires": [
                    {
                        "id": "wire_watch_context_pressure",
                        "kind": "watch",
                        "source": "event_source_context_pressure",
                        "source_port": "watch",
                        "target": WORK_A,
                    },
                    {'id': 'wire_context_pressure_target', 'kind': 'route', 'source': 'event_source_context_pressure', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )

    unavailable = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    pressure["used_percent"] = 86.0
    initially_exceeded = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    pressure["used_percent"] = 90.0
    still_exceeded = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    pressure["used_percent"] = 80.0
    exact_threshold = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    pressure["used_percent"] = 81.0
    exceeded_again = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    unchanged = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )

    assert unavailable["event_count"] == 0
    assert unavailable["baseline_count"] == 0
    assert initially_exceeded["event_count"] == 1
    first_event = initially_exceeded["events"][0]["event"]
    assert first_event["type"] == "resident.context_pressure_exceeded"
    assert first_event["subject_resident_id"] == WORK_A
    assert _event_payload_without_path_id(first_event) == {
        "threshold_percent": 80.0,
        "previous_used_percent": None,
        "used_percent": 86.0,
        "context_tokens": 222_224,
        "model_context_window": 258_400,
        "heat_level": "hot",
    }
    assert still_exceeded["event_count"] == 0
    assert exact_threshold["event_count"] == 0
    assert exceeded_again["event_count"] == 1
    assert exceeded_again["events"][0]["event"]["payload"][
        "previous_used_percent"
    ] == 80.0
    assert unchanged["event_count"] == 0
    assert sent == [WORK_B, WORK_B]
    assert observed_sessions == [
        {WORK_A},
    ] * 6
    runtime = lineage_reads.get_lineage(
        created["lineage_id"],
        ACTOR,
    )["program_event_source_runtime"][
        "event_source_context_pressure"
    ]
    assert runtime["baseline_established"] is True
    assert runtime["used_percent"] == 81.0
    assert runtime["threshold_percent"] == 80.0
    assert runtime["exceeded"] is True


def test_work_duration_event_is_edge_triggered_per_job(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工作时长事件每个任务只在越过阈值时触发一次，新任务已超时则立即触发"""
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_duration_{len(sent)}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Resident work duration Event",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_work_duration",
                        "kind": "event_source",
                        "config": {
                            "event_type": "resident.work_duration_exceeded",
                            "duration_seconds": 60,
                        },
                    }
                ],
                "wires": [
                    {
                        "id": "wire_watch_work_duration",
                        "kind": "watch",
                        "source": "event_source_work_duration",
                        "target": WORK_A,
                    },
                    {'id': 'wire_work_duration_target', 'kind': 'route', 'source': 'event_source_work_duration', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )

    def snapshot(
        *,
        message_id: str,
        captured_at: str,
        active: bool = True,
    ) -> dict[str, object]:
        state = {
            "resident_id": WORK_A,
            "active": active,
            "status": "running" if active else "idle",
        }
        if active:
            state.update(
                {
                    "message_id": message_id,
                    "started_at": "2026-08-02T10:00:00Z",
                    "queue_count": 0,
                }
            )
        return {
            "captured_at": captured_at,
            "resident_ids": [WORK_A],
            "active_resident_ids": [WORK_A] if active else [],
            "residents": {WORK_A: state},
        }

    exact_threshold = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            message_id="job_one",
            captured_at="2026-08-02T10:01:00Z",
        ),
    )
    crossed = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            message_id="job_one",
            captured_at="2026-08-02T10:01:01Z",
        ),
    )
    still_over = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            message_id="job_one",
            captured_at="2026-08-02T10:05:00Z",
        ),
    )
    idle = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            message_id="",
            captured_at="2026-08-02T10:05:01Z",
            active=False,
        ),
    )
    new_message_already_over = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            message_id="job_two",
            captured_at="2026-08-02T10:02:00Z",
        ),
    )

    assert exact_threshold["event_count"] == 0
    assert exact_threshold["baseline_count"] == 1
    assert crossed["event_count"] == 1
    crossed_event = crossed["events"][0]["event"]
    assert crossed_event["payload"]["trigger_path_id"].startswith("trigger_path_")
    assert crossed_event == {
        "id": crossed["events"][0]["event"]["id"],
        "identity": OperationIdentity.for_actor(WORK_A).to_dict(),
        "type": "resident.work_duration_exceeded",
        "subject_resident_id": WORK_A,
        "occurred_at": "2026-08-02T10:01:01Z",
        "payload": {
            "trigger_path_id": crossed_event["payload"]["trigger_path_id"],
            "message_id": "job_one",
            "input_state": "running",
            "started_at": "2026-08-02T10:00:00Z",
            "threshold_seconds": 60,
            "duration_seconds": 61,
        },
    }
    assert still_over["event_count"] == 0
    assert idle["event_count"] == 0
    assert new_message_already_over["event_count"] == 1
    assert new_message_already_over["events"][0]["event"][
        "payload"
    ]["message_id"] == "job_two"
    assert sent == [WORK_B, WORK_B]


def test_queue_pressure_event_supports_both_metrics_and_rearms(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """队列压力事件支持队列长度和最久等待两种指标，回落后可再次触发，重放不重复投递"""
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_queue_{len(sent)}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Resident queue pressure Events",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_queue_length",
                        "kind": "event_source",
                        "config": {
                            "event_type": "resident.queue_pressure_exceeded",
                            "pressure_metric": "queue_length",
                            "queue_length_threshold": 2,
                        },
                    },
                    {
                        "id": "event_source_queue_wait",
                        "kind": "event_source",
                        "config": {
                            "event_type": "resident.queue_pressure_exceeded",
                            "pressure_metric": "oldest_wait_seconds",
                            "wait_seconds_threshold": 30,
                        },
                    },
                ],
                "wires": [
                    *[
                        {
                            "id": f"wire_watch_{suffix}",
                            "kind": "watch",
                            "source": source_id,
                            "target": WORK_A,
                        }
                        for suffix, source_id in (
                            ("queue_length", "event_source_queue_length"),
                            ("queue_wait", "event_source_queue_wait"),
                        )
                    ],
                    *[
                        {'id': f'wire_route_{suffix}', 'kind': 'route', 'source': source_id, 'target': WORK_B}
                        for suffix, source_id in (
                            ("queue_length", "event_source_queue_length"),
                            ("queue_wait", "event_source_queue_wait"),
                        )
                    ],
                ],
            },
        },
        ACTOR,
    )

    def snapshot(
        *,
        captured_at: str,
        queue_count: int,
        oldest_queued_at: str = "",
    ) -> dict[str, object]:
        return {
            "captured_at": captured_at,
            "resident_ids": [WORK_A],
            "active_resident_ids": [WORK_A] if queue_count else [],
            "residents": {
                WORK_A: {
                    "resident_id": WORK_A,
                    "active": bool(queue_count),
                    "status": "running" if queue_count else "idle",
                    "message_id": "job_queue_subject" if queue_count else "",
                    "queue_count": queue_count,
                    "oldest_queued_at": oldest_queued_at,
                }
            },
        }

    baseline = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            captured_at="2026-08-02T10:01:00Z",
            queue_count=0,
        ),
    )
    # The graph can finish before its observation cursor is acknowledged.
    # A later sample has different queue details but must not deliver again.
    commit_observation = lineage_program_runtime.commit_event_source_value
    monkeypatch.setattr(lineage_program_runtime, "commit_event_source_value", lambda *args, **kwargs: False)
    length_crossed = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            captured_at="2026-08-02T10:01:00Z",
            queue_count=3,
            oldest_queued_at="2026-08-02T10:00:40Z",
        ),
    )
    monkeypatch.setattr(lineage_program_runtime, "commit_event_source_value", commit_observation)
    replay = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"], ACTOR,
        resident_activity_snapshot=snapshot(
            captured_at="2026-08-02T10:01:05Z", queue_count=4,
            oldest_queued_at="2026-08-02T10:00:40Z"),
    )
    assert replay["event_count"] == 0
    assert replay["events"][0]["duplicate"] is True
    assert replay["events"][0]["event"]["id"] == length_crossed["events"][0]["event"]["id"]
    assert replay["events"][0]["event"]["payload"]["queue_count"] == 3
    assert len(sent) == 1
    wait_crossed = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            captured_at="2026-08-02T10:01:15Z",
            queue_count=3,
            oldest_queued_at="2026-08-02T10:00:40Z",
        ),
    )
    still_over = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            captured_at="2026-08-02T10:01:20Z",
            queue_count=5,
            oldest_queued_at="2026-08-02T10:00:40Z",
        ),
    )
    recovered = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            captured_at="2026-08-02T10:02:00Z",
            queue_count=0,
        ),
    )
    both_crossed_again = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
        resident_activity_snapshot=snapshot(
            captured_at="2026-08-02T10:03:00Z",
            queue_count=4,
            oldest_queued_at="2026-08-02T10:02:20Z",
        ),
    )

    assert baseline["baseline_count"] == 2
    assert length_crossed["event_count"] == 1
    assert _event_payload_without_path_id(length_crossed["events"][0]["event"]) == {
        "message_id": "job_queue_subject",
        "input_state": "running",
        "oldest_queued_at": "2026-08-02T10:00:40Z",
        "pressure_metric": "queue_length",
        "pressure_threshold": 2,
        "pressure_value": 3,
        "queue_count": 3,
        "oldest_wait_seconds": 20,
    }
    assert wait_crossed["event_count"] == 1
    assert wait_crossed["events"][0]["event"]["payload"][
        "pressure_metric"
    ] == "oldest_wait_seconds"
    assert wait_crossed["events"][0]["event"]["payload"][
        "pressure_value"
    ] == 35
    assert still_over["event_count"] == 0
    assert recovered["event_count"] == 0
    assert both_crossed_again["event_count"] == 2
    assert {
        dispatch["event"]["payload"]["pressure_metric"]
        for dispatch in both_crossed_again["events"]
    } == {"queue_length", "oldest_wait_seconds"}
    assert sent == [WORK_B, WORK_B, WORK_B, WORK_B]


def test_state_condition_event_initial_policy_and_false_to_true_edges(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状态条件事件按 fire_on_initial_match 决定初始满足是否触发，之后只在由假变真时触发"""
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_state_{len(sent)}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Graph State condition Events",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_state_edge",
                        "kind": "event_source",
                        "config": {
                            "event_type": "state.condition_met",
                            "key": "review.score",
                            "operator": "greater_or_equal",
                            "expected_value": 8,
                            "fire_on_initial_match": False,
                        },
                    },
                    {
                        "id": "event_source_state_initial",
                        "kind": "event_source",
                        "config": {
                            "event_type": "state.condition_met",
                            "key": "review.score",
                            "operator": "greater_or_equal",
                            "expected_value": 8,
                            "fire_on_initial_match": True,
                        },
                    },
                ],
                "wires": [
                    {'id': 'wire_state_edge_target', 'kind': 'route', 'source': 'event_source_state_edge', 'target': WORK_A},
                    {'id': 'wire_state_initial_target', 'kind': 'route', 'source': 'event_source_state_initial', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )
    lineage_graph_state.update_cell(
        created["lineage_id"],
        "review.score",
        9,
    )

    initial = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    unchanged = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    lineage_graph_state.update_cell(
        created["lineage_id"],
        "review.score",
        7,
    )
    recovered = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    lineage_graph_state.update_cell(
        created["lineage_id"],
        "review.score",
        8,
    )
    crossed = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )

    assert initial["event_count"] == 1
    assert _event_payload_without_path_id(initial["events"][0]["event"]) == {
        "key": "review.score",
        "operator": "greater_or_equal",
        "state_revision": 1,
        "matched": True,
    }
    assert unchanged["event_count"] == 0
    assert recovered["event_count"] == 0
    assert crossed["event_count"] == 2
    assert all(
        dispatch["event"]["payload"]["previous_matched"] is False
        for dispatch in crossed["events"]
    )
    assert sent == [WORK_B, WORK_A, WORK_B]
    runtime = lineage_reads.get_lineage(
        created["lineage_id"],
        ACTOR,
    )["program_event_source_runtime"]
    assert runtime["event_source_state_edge"]["condition_met"] is True
    assert runtime["event_source_state_initial"]["condition_met"] is True


def test_state_condition_comparison_is_json_typed(subtests, isolated_portal_case):
    """状态条件按 JSON 类型比较，布尔值和字符串不与数字比较大小"""
    for existed, value, operator, expected, matched in [
        (True, {"ready": True}, "exists", None, True),
        (False, None, "missing", None, True),
        (False, None, "not_equals", 1, False),
        (True, [1, 2], "equals", [1, 2], True),
        (True, {"a": 1}, "not_equals", {"a": 2}, True),
        (True, 8, "greater_or_equal", 8, True),
        (True, 7.5, "less_than", 8, True),
        (True, True, "greater_than", 0, False),
        (True, "9", "greater_than", 8, False),
    ]:
        with subtests.test(existed=existed, value=value, operator=operator, expected=expected, matched=matched), isolated_portal_case() as case_path:
            assert lineage_event_conditions._state_condition_matches(
                existed=existed,
                value=value,
                operator=operator,
                expected_value=expected,
            ) is matched


def test_event_wire_and_effect_envelope_opt_out_round_trip_through_program() -> None:
    """连线上关闭 include_envelope 的设置经规范化和编译后保留，组件上不出现该字段"""
    program = lineage_program.normalize_program(
        {
            "components": [
                {
                    "id": "event_source_plain",
                    "kind": "event_source",
                    "config": {
                        "event_type": "manual.triggered",
                    },
                },
                {
                    "id": "effect_plain",
                    "kind": "effect",
                    "config": effect_config("DeliverMessage", {
                            "resident_id": {"from": "endpoint.target"},
                            "message": {"value": "Effect default task."},
                        }, ["target"]),
                },
                {
                    "id": "event_source_compatible_default",
                    "kind": "event_source",
                    "config": {
                        "event_type": "manual.triggered",
                    },
                },
                {
                    "id": "effect_compatible_default",
                    "kind": "effect",
                    "config": deepcopy(DELIVER),
                },
            ],
            "wires": [
                {'id': 'wire_plain_event_effect', 'kind': 'route', 'source': 'event_source_plain', 'target': 'effect_plain', 'config': {'include_envelope': False}},
                {
                    "id": "wire_plain_effect_target",
                    "kind": "target",
                    "source": "effect_plain",
                    "target": WORK_A,
                },
                {'id': 'wire_default_event_effect', 'kind': 'route', 'source': 'event_source_compatible_default', 'target': 'effect_compatible_default'},
                {
                    "id": "wire_default_effect_target",
                    "kind": "target",
                    "source": "effect_compatible_default",
                    "target": WORK_B,
                },
            ],
        },
        lineage_nodes=[_worker(WORK_A), _worker(WORK_B)],
    )
    components = {
        component["id"]: component
        for component in program["components"]
    }
    wires = {
        wire["id"]: wire
        for wire in program["wires"]
    }
    plan = lineage_program.compile_program(
        program,
        [_worker(WORK_A), _worker(WORK_B)],
    )

    assert "include_envelope" not in components["event_source_plain"]
    assert "include_envelope" not in components["effect_plain"]
    assert "message" not in components["effect_plain"]
    assert "include_envelope" not in components["event_source_compatible_default"]
    assert "include_envelope" not in components["effect_compatible_default"]
    assert wires["wire_plain_event_effect"]["config"] == {
        "include_envelope": False,
        "unavailable_policy": "drop",
        "expires_after_seconds": 0,
    }
    assert deliver_script_parts(components["effect_plain"]["config"]["script"])[1] == "Effect default task."
    assert "config" not in wires["wire_plain_effect_target"]
    assert wires["wire_default_event_effect"]["config"] == {
        "unavailable_policy": "drop",
        "expires_after_seconds": 0,
    }
    assert "config" not in wires["wire_default_effect_target"]
    assert plan["event_sources"]["event_source_plain"][
        "subscriber_routes"
    ][0]["config"] == {
        "include_envelope": False,
        "unavailable_policy": "drop",
        "expires_after_seconds": 0,
    }
    assert deliver_script_parts(plan["effects"]["effect_plain"]["script"])[1] == "Effect default task."
    assert "message=" not in plan["effects"]["effect_compatible_default"]["script"]


def test_reference_bindings_reject_invocation_configuration_contracts(subtests, isolated_portal_case):
    """端点连线不接受调用配置，同一事件到同一端点的重复路由即使 prompt 不同也被拒绝"""
    with subtests.test(contract='test_reference_bindings_reject_invocation_configuration'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="target wire config 不接受字段"):
            lineage_program.compile_program(
                {
                    "components": [{
                                       "id": "effect_delivery_send",
                                       "kind": "effect",
                                       "config": deepcopy(DELIVER),
                                   }],
                    "wires": [{
                        "id": "wire_delivery_send",
                        "kind": "target",
                        "source": "effect_delivery_send",
                        "target": WORK_A,
                        "config": {"include_envelope": False},
                    }],
                },
                [_worker(WORK_A)],
            )
    with subtests.test(contract='test_duplicate_event_route_is_not_hidden_by_a_different_prompt'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="相同端点"):
            lineage_program.normalize_program(
                {
                    "components": [
                        {
                            "id": "event_source_duplicate",
                            "kind": "event_source",
                            "config": {
                                "event_type": "manual.triggered",
                            },
                        }
                    ],
                    "wires": [
                        {'id': 'wire_duplicate_one', 'kind': 'route', 'source': 'event_source_duplicate', 'target': WORK_A, 'config': {'invoke_profiles': {'agent': {'arguments': {'prompt': {'value': 'First prompt.'}}}}}},
                        {'id': 'wire_duplicate_two', 'kind': 'route', 'source': 'event_source_duplicate', 'target': WORK_A, 'config': {'invoke_profiles': {'agent': {'arguments': {'prompt': {'value': 'Second prompt.'}}}}}},
                    ],
                },
                lineage_nodes=[_worker(WORK_A)],
            )


def test_manual_event_directly_starts_ordinary_resident(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """手动事件直接启动普通 Resident，消息带 HATG Packet 信封且发送时不持锁"""
    sent: list[tuple[str, str]] = []

    def send(node: dict, prompt: str, _actor: str) -> dict:
        assert not invariant_locks.lock_held()
        sent.append((str(node["id"]), prompt))
        return {"message_id": f"job_{len(sent)}"}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Generic Event to Resident",
            "nodes": [_worker(WORK_A, "Source"), _worker(WORK_B, "Worker B")],
            "program": {
                "components": [
                    {
                        "id": "event_source_manual_worker",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    }
                ],
                "wires": [
                    {'id': 'wire_manual_worker', 'kind': 'route', 'source': 'event_source_manual_worker', 'target': WORK_B, 'config': {'invoke_profiles': {'agent': {'arguments': {'prompt': {'value': 'Execute the real business task.'}}}}}}
                ],
            },
        },
        ACTOR,
    )

    emitted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {
            "event_source_id": "event_source_manual_worker",
            "expected_revision": created["revision"],
        },
        ACTOR,
    )

    assert emitted["matched_count"] == 1
    assert [resident_id for resident_id, _prompt in sent] == [WORK_B]
    assert "Execute the real business task." in sent[0][1]
    assert "[HATG Packet]" in sent[0][1]


def test_event_fire_limit_counts_occurrences_before_fanout_and_can_reset(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事件次数上限按触发次数计而不按投递数，用尽后不再投递，重置后新一代重新计数"""
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_fire_{len(sent)}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Bounded Event fire",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_bounded_fanout",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                            "max_fires": 2,
                        },
                    }
                ],
                "wires": [
                    {'id': 'wire_bounded_a', 'kind': 'route', 'source': 'event_source_bounded_fanout', 'target': WORK_A},
                    {'id': 'wire_bounded_b', 'kind': 'route', 'source': 'event_source_bounded_fanout', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )

    first = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_bounded_fanout"},
        ACTOR,
    )
    second = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_bounded_fanout"},
        ACTOR,
    )
    exhausted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_bounded_fanout"},
        ACTOR,
    )

    assert first["fire"]["occurrence_count"] == 1
    assert second["fire"]["occurrence_count"] == 2
    assert second["fire"]["exhausted"] is True
    assert exhausted["exhausted"] is True
    assert exhausted["matched_count"] == 0
    assert sent == [WORK_A, WORK_B, WORK_A, WORK_B]
    runtime = exhausted["lineage"]["program_event_source_runtime"][
        "event_source_bounded_fanout"
    ]
    assert runtime == {
        "paused": False,
        "state": "exhausted",
        "max_fires": 2,
        "occurrence_count": 2,
        "remaining_fires": 0,
        "generation": 0,
        "last_fired_at": runtime["last_fired_at"],
        "last_delivery": runtime["last_delivery"],
        "exhausted": True,
    }
    assert runtime["last_delivery"]["total_count"] == 2
    assert runtime["last_delivery"]["delivered_count"] == 2
    assert runtime["last_delivery"]["statuses"] == {"sent": 2}

    reset = component_runtime.reset(created["lineage_id"], "event_source_bounded_fanout", ACTOR)
    after_reset = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_bounded_fanout"},
        ACTOR,
    )

    assert reset["component_id"] == "event_source_bounded_fanout"
    assert (reset["occurrence_count"], reset["generation"]) == (0, 1)
    assert after_reset["fire"]["occurrence_count"] == 1
    assert after_reset["fire"]["generation"] == 1
    assert sent[-2:] == [WORK_A, WORK_B]


def test_unbounded_unrouted_event_counts_each_accepted_occurrence(
    primitive_store: Path,
) -> None:
    """不限次数且没有下游的事件源也累计每次触发"""
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Unrouted Event occurrence count",
            "nodes": [],
            "program": {
                "components": [
                    {
                        "id": "event_source_unrouted_unbounded",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                            "max_fires": None,
                        },
                    }
                ],
                "wires": [],
            },
        },
        ACTOR,
    )

    first = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_unrouted_unbounded"},
        ACTOR,
    )
    second = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_unrouted_unbounded"},
        ACTOR,
    )

    assert first["matched_count"] == 0
    assert first["fire"]["occurrence_count"] == 1
    assert second["matched_count"] == 0
    assert second["fire"]["occurrence_count"] == 2
    runtime = second["lineage"]["program_event_source_runtime"][
        "event_source_unrouted_unbounded"
    ]
    assert runtime["occurrence_count"] == 2
    assert runtime["max_fires"] is None
    assert runtime["remaining_fires"] is None
    assert runtime["exhausted"] is False


def test_zero_fire_limit_disables_event_without_becoming_unbounded(
    primitive_store: Path,
) -> None:
    """次数上限为 0 的事件源不触发，重置后仍为 0 而不会变成不限次数"""
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Zero Event budget",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    {
                        "id": "event_source_zero_budget",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                            "max_fires": 0,
                        },
                    }
                ],
                "wires": [],
            },
        },
        ACTOR,
    )

    result = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_zero_budget"},
        ACTOR,
    )

    assert result["exhausted"] is True
    assert result["matched_count"] == 0
    assert result["fire"]["max_fires"] == 0
    runtime = result["lineage"]["program_event_source_runtime"][
        "event_source_zero_budget"
    ]
    assert runtime["occurrence_count"] == 0
    assert runtime["max_fires"] == 0
    assert runtime["remaining_fires"] == 0
    assert runtime["exhausted"] is True
    # Resetting a zero budget leaves it disabled; it never becomes unbounded.
    reset = component_runtime.reset(created["lineage_id"], "event_source_zero_budget", ACTOR)
    assert reset["remaining_fires"] == 0
    again = lineage_domain.emit_program_manual_event(
        created["lineage_id"], {"event_source_id": "event_source_zero_budget"}, ACTOR,
    )
    assert again["matched_count"] == 0


def test_budget_exhaustion_routes_through_an_explicit_event_source(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """触发次数用尽后，由 watch 该事件源的 budget.exhausted 事件源发出耗尽事件"""
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_budget_{len(sent)}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Explicit budget watcher",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_bounded_once",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                            "max_fires": 1,
                        },
                    },
                    {
                        "id": "event_source_budget_observer",
                        "kind": "event_source",
                        "config": {
                            "event_type": "budget.exhausted",
                        },
                    },
                ],
                "wires": [
                    {
                        "id": "wire_watch_budget_source",
                        "kind": "watch",
                        "source": "event_source_budget_observer",
                        "source_port": "watch",
                        "target": "event_source_bounded_once",
                    },
                    {'id': 'wire_bounded_once_work', 'kind': 'route', 'source': 'event_source_bounded_once', 'target': WORK_A},
                    {'id': 'wire_budget_observer_notice', 'kind': 'route', 'source': 'event_source_budget_observer', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )

    emitted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_bounded_once"},
        ACTOR,
    )

    assert sent == [WORK_A, WORK_B]
    watcher_result = emitted["budget_exhausted_events"][0]
    assert watcher_result["event_source_id"] == (
        "event_source_budget_observer"
    )
    assert watcher_result["event"]["type"] == "budget.exhausted"
    assert _event_payload_without_path_id(watcher_result["event"]) == {
        "event_source_id": "event_source_bounded_once",
        "source_event_id": emitted["event"]["id"],
        "fire_count": 1,
        "max_fires": 1,
        "generation": 0,
        "runtime_event_depth": 1,
    }


def test_delivery_failure_can_be_observed_for_one_exact_event_wire(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """event.delivery_failed 可只观察一条路由连线，投递失败时带出连线和诊断信息"""
    sent: list[str] = []

    def send(node: dict, _prompt: str, _actor: str) -> dict:
        if str(node["id"]) == WORK_A:
            raise RuntimeError("worker transport unavailable")
        sent.append(str(node["id"]))
        return {"message_id": "job_failure_observer"}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Exact delivery failure watcher",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_failure_origin",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    },
                    {
                        "id": "event_source_failure_observer",
                        "kind": "event_source",
                        "config": {
                            "event_type": "event.delivery_failed",
                        },
                    },
                ],
                "wires": [
                    {
                        "id": "wire_watch_delivery_failure_path",
                        "kind": "watch",
                        "source": "event_source_failure_observer",
                        "source_port": "watch",
                        "target": "wire_delivery_failure_path",
                    },
                    {'id': 'wire_delivery_failure_path', 'kind': 'route', 'source': 'event_source_failure_origin', 'target': WORK_A},
                    {'id': 'wire_delivery_failure_notice', 'kind': 'route', 'source': 'event_source_failure_observer', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )

    emitted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_failure_origin"},
        ACTOR,
    )

    assert sent == [WORK_B]
    watcher_result = emitted["delivery_failed_events"][0]
    assert watcher_result["event_source_id"] == (
        "event_source_failure_observer"
    )
    payload = watcher_result["event"]["payload"]
    assert payload["event_source_id"] == "event_source_failure_origin"
    assert payload["wire_id"] == "wire_delivery_failure_path"
    assert payload["wire_ids"] == ["wire_delivery_failure_path"]
    assert payload["failure_count"] == 1
    assert payload["diagnostics"] == ["worker transport unavailable"]


def test_deleting_an_observed_source_cascades_its_runtime_watchers(
    primitive_store: Path,
) -> None:
    """删除被观察的事件源时，一并删除观察它的预算耗尽和投递失败事件源及连线"""
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Runtime watcher deletion closure",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_delete_origin",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                            "max_fires": 1,
                        },
                    },
                    {
                        "id": "event_source_delete_budget",
                        "kind": "event_source",
                        "config": {
                            "event_type": "budget.exhausted",
                        },
                    },
                    {
                        "id": "event_source_delete_failure",
                        "kind": "event_source",
                        "config": {
                            "event_type": "event.delivery_failed",
                        },
                    },
                ],
                "wires": [
                    {
                        "id": "wire_watch_delete_budget",
                        "kind": "watch",
                        "source": "event_source_delete_budget",
                        "source_port": "watch",
                        "target": "event_source_delete_origin",
                    },
                    {
                        "id": "wire_watch_delete_failure",
                        "kind": "watch",
                        "source": "event_source_delete_failure",
                        "source_port": "watch",
                        "target": "wire_delete_origin_work",
                    },
                    {'id': 'wire_delete_origin_work', 'kind': 'route', 'source': 'event_source_delete_origin', 'target': WORK_A},
                    {'id': 'wire_delete_budget_notice', 'kind': 'route', 'source': 'event_source_delete_budget', 'target': WORK_B},
                    {'id': 'wire_delete_failure_notice', 'kind': 'route', 'source': 'event_source_delete_failure', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )

    deleted = lineage_program_edits.delete_lineage_program_component(
        created["lineage_id"],
        {
            "component_id": "event_source_delete_origin",
            "expected_revision": created["revision"],
        },
        ACTOR,
    )

    assert deleted["deleted_component_ids"] == [
        "event_source_delete_budget",
        "event_source_delete_failure",
        "event_source_delete_origin",
    ]
    assert deleted["lineage"]["program"]["components"] == []
    assert deleted["lineage"]["program"]["wires"] == []


def test_resident_state_event_observes_intake_changes(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resident.state_changed 在 Resident 接收状态变化时触发一次，带出前后状态"""
    sent: list[str] = []
    intake = {"value": "open"}

    monkeypatch.setattr(
        lineage_domain.resident_protocol,
        "get_resident_state",
        lambda resident_id, actor: {
            "resident_id": resident_id,
            "state": {
                "work_state": "idle",
                "active_message_id": "",
                "queued_count": 0,
                "intake_state": intake["value"],
            },
        },
    )
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_state_{len(sent)}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Resident state Events",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_intake_state",
                        "kind": "event_source",
                        "config": {
                            "event_type": "resident.state_changed",
                            "state_domain": "intake",
                        },
                    },
                ],
                "wires": [
                    {
                        "id": "wire_watch_intake_state",
                        "kind": "watch",
                        "source": "event_source_intake_state",
                        "source_port": "watch",
                        "target": WORK_A,
                    },
                    {'id': 'wire_intake_state_target', 'kind': 'route', 'source': 'event_source_intake_state', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )

    baseline = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    intake["value"] = "closed"
    intake_changed = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    unchanged = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )

    assert baseline["baseline_count"] == 1
    assert intake_changed["event_count"] == 1
    intake_event = intake_changed["events"][0]["event"]
    assert _event_payload_without_path_id(intake_event) == {
        "state_domain": "intake",
        "previous_state": "open",
        "state": "closed",
    }
    assert unchanged["event_count"] == 0
    assert sent == [WORK_B]


def test_event_without_envelope_sends_the_authored_prompt_verbatim(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关闭信封的事件连线原样发送设定的 prompt"""
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda _node, prompt, _actor: (
            sent.append(prompt)
            or {"message_id": "job_plain_event"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Plain Event delivery",
            "nodes": [_worker(WORK_B, "Worker B")],
            "program": {
                "components": [
                    {
                        "id": "event_source_plain_delivery",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    }
                ],
                "wires": [
                    {'id': 'wire_plain_event_worker', 'kind': 'route', 'source': 'event_source_plain_delivery', 'target': WORK_B, 'config': {'invoke_profiles': {'agent': {'arguments': {'prompt': {'value': '你好啊'}}}}, 'include_envelope': False}}
                ],
            },
        },
        ACTOR,
    )

    emitted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_plain_delivery"},
        ACTOR,
    )

    assert emitted["matched_count"] == 1
    assert sent == ["你好啊"]


def test_one_event_can_fan_out_with_different_envelopes_per_edge(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一事件扇出时，每条连线可分别决定是否带 HATG Packet 信封"""
    sent: dict[str, str] = {}
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, prompt, _actor: (
            sent.__setitem__(str(node["id"]), prompt)
            or {"message_id": f"job_{node['id'][-4:]}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Per-edge Event envelopes",
            "nodes": [_worker(WORK_A, "Worker A"), _worker(WORK_B, "Worker B")],
            "program": {
                "components": [
                    {
                        "id": "event_source_fanout_envelope",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    }
                ],
                "wires": [
                    {'id': 'wire_fanout_plain', 'kind': 'route', 'source': 'event_source_fanout_envelope', 'target': WORK_A, 'config': {'invoke_profiles': {'agent': {'arguments': {'prompt': {'value': '继续同一项工作。'}}}}, 'include_envelope': False}},
                    {'id': 'wire_fanout_wrapped', 'kind': 'route', 'source': 'event_source_fanout_envelope', 'target': WORK_B, 'config': {'invoke_profiles': {'agent': {'arguments': {'prompt': {'value': '继续同一项工作。'}}}}}},
                ],
            },
        },
        ACTOR,
    )

    emitted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_fanout_envelope"},
        ACTOR,
    )

    assert emitted["matched_count"] == 2
    assert sent[WORK_A] == "继续同一项工作。"
    assert sent[WORK_B].startswith("[HATG Packet]")
    assert "来源组件：event_source_fanout_envelope" in sent[WORK_B]
    assert "继续同一项工作。" in sent[WORK_B]


def test_effect_success_composes_into_second_resident_route(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Effect 成功事件可再路由到 Resident，下游投递记在新的子触发路径上"""
    sent: list[tuple[str, str]] = []

    def send(node: dict, prompt: str, _actor: str) -> dict:
        sent.append((str(node["id"]), prompt))
        return {"message_id": f"job_{len(sent)}"}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    created = lineage_lifecycle.create_lineage(
        {
            "title": "TriggerEvent composition",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_manual_emit",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    },
                    {
                        "id": "effect_emit_next",
                        "kind": "effect",
                        "config": deepcopy(QUIET),
                    },
                    {
                        "id": "event_source_emit_next",
                        "kind": "event_source",
                        "config": {
                            "event_type": "effect.succeeded",
                        },
                    },
                ],
                "wires": [
                    {'id': 'wire_manual_emit', 'kind': 'route', 'source': 'event_source_manual_emit', 'target': 'effect_emit_next'},
                    {
                        "id": "wire_watch_emit_next",
                        "kind": "watch",
                        "source": "event_source_emit_next",
                        "source_port": "watch",
                        "target": "effect_emit_next",
                    },
                    {'id': 'wire_emit_worker', 'kind': 'route', 'source': 'event_source_emit_next', 'target': WORK_B, 'config': {'invoke_profiles': {'agent': {'arguments': {'prompt': {'value': 'Continue from the emitted event.'}}}}, 'include_envelope': False}},
                ],
            },
        },
        ACTOR,
    )

    emitted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {
            "event_source_id": "event_source_manual_emit",
            "expected_revision": created["revision"],
        },
        ACTOR,
    )

    assert emitted["matched_count"] == 1
    assert sent and sent[0][0] == WORK_B
    assert sent[0][1] == "Continue from the emitted event."
    from management_portal.society_service import trigger_paths
    runtime = lineage_program_runtime.read_runtime(created["lineage_id"])
    source_path = emitted["trigger_path_id"]
    output_path = runtime["event_sources"]["event_source_emit_next"]["last_delivery"]["trigger_path_id"]
    assert source_path != output_path
    child = trigger_paths.get(created["lineage_id"], output_path)
    assert child["cause_kind"] == "step"
    assert {row["trigger_path_id"] for row in trigger_paths.resident_inputs(created["lineage_id"])} == {output_path}


def test_effect_failure_routes_through_an_explicit_event_source(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Effect 不可重试的失败经 watch 它的 effect.failed 事件源通知，触发路径记录调用错误"""
    lineage_test_support.install_registered_lineage_members(monkeypatch)
    sent: list[str] = []

    def send(node: dict, _prompt: str, _actor: str) -> dict:
        resident_id = str(node["id"])
        if resident_id == WORK_A:
            # This case exercises an owner-confirmed terminal rejection. An
            # unqualified transport exception leaves commit state unknown.
            raise OperationError("target transport unavailable", code="instruction.failed",
                                 retryable=False, commit_state="not_committed")
        sent.append(resident_id)
        return {"message_id": "job_failure_observer"}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Explicit Effect failure observer",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_failure_start",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    },
                    {
                        "id": "effect_failure_subject",
                        "kind": "effect",
                        "config": deepcopy(DELIVER),
                    },
                    {
                        "id": "event_source_effect_failure",
                        "kind": "event_source",
                        "config": {
                            "event_type": "effect.failed",
                        },
                    },
                ],
                "wires": [
                    {
                        "id": "wire_watch_effect_failure",
                        "kind": "watch",
                        "source": "event_source_effect_failure",
                        "source_port": "watch",
                        "target": "effect_failure_subject",
                    },
                    {'id': 'wire_failure_start', 'kind': 'route', 'source': 'event_source_failure_start', 'target': 'effect_failure_subject'},
                    {
                        "id": "wire_failure_target",
                        "kind": "target",
                        "source": "effect_failure_subject",
                        "target": WORK_A,
                    },
                    {'id': 'wire_failure_observer', 'kind': 'route', 'source': 'event_source_effect_failure', 'target': WORK_B, 'config': {'invoke_profiles': {'agent': {'arguments': {'prompt': {'value': 'Handle the failed Effect.'}}}}, 'include_envelope': False}},
                ],
            },
        },
        ACTOR,
    )

    emitted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_failure_start"},
        ACTOR,
    )

    failed = emitted["results"][0]
    assert failed["status"] == "failed"
    assert failed["typed_outputs"]["retryable"] is False
    assert "target transport unavailable" in failed["error"]
    assert failed["failure_event"]["type"] == "effect.failed"
    assert failed["watcher_dispatches"][0]["matched_count"] == 1
    assert sent == [WORK_B]
    from management_portal.society_service import trigger_paths
    path = trigger_paths.get(created["lineage_id"], emitted["trigger_path_id"])
    [effect] = [step for step in path["steps"] if step["kind"] == "effect"]
    finished = trigger_paths.effect_finished(created["lineage_id"], effect["activation_key"])
    assert finished["status"] == "failed"
    assert finished["outputs"] == failed["typed_outputs"]
    [call] = finished["calls"].values()
    assert call["instruction"] == "DeliverMessage" and call["instruction_id"]
    assert call["error"]["commit_state"] == "not_committed"
    assert call["error"]["retryable"] is False
    runtime = lineage_program_runtime.read_runtime(
        created["lineage_id"]
    )
    assert "wire_failure_start" not in runtime["event_wires"]


def test_system_event_worker_baselines_history_and_recovers_only_a_new_completion(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """系统事件轮询把已有的完成记为基线，只为 watch 之后的新完成投递一次"""
    lineage_test_support.install_registered_lineage_members(monkeypatch)
    sent: list[str] = []
    latest_completion = {
        "message_id": "job_source_before_watch",
        "completed_at": "2026-07-30T11:00:00Z",
    }
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_target_{len(sent)}"}
        ),
    )
    monkeypatch.setattr(
        lineage_test_support,
        "LATEST_COMPLETED_MESSAGE_RESOLVER",
        lambda resident_id, _actor: (
            dict(latest_completion) if resident_id == WORK_A else {}
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Recover generic completion route",
            "nodes": [
                _worker(WORK_A, "Source worker"),
                _worker(WORK_B, "Target worker"),
            ],
            "program": {
                "components": [
                    {
                        "id": "event_source_recovered_work",
                        "kind": "event_source",
                        "config": {
                            "event_type": "resident.work_finished",
                        },
                    }
                ],
                "wires": [
                    {
                        "id": "wire_watch_recovered_work",
                        "kind": "watch",
                        "source": "event_source_recovered_work",
                        "source_port": "watch",
                        "target": WORK_A,
                    },
                    {'id': 'wire_recovered_work_target', 'kind': 'route', 'source': 'event_source_recovered_work', 'target': WORK_B}
                ],
            },
        },
        ACTOR,
    )

    historical = lineage_test_support.emit_resident_completion(
        created["lineage_id"],
        WORK_A,
        "job_source_before_watch",
        ACTOR,
        completed_at="2026-07-30T11:00:00Z",
        ports=lineage_domain._program_terminal_ports(),
    )
    assert len(historical) == 1
    assert historical[0]["matched_count"] == 0
    assert historical[0]["duplicate"] is True
    baseline = lineage_domain.poll_all_lineage_system_events()
    unchanged = lineage_domain.poll_all_lineage_system_events()
    latest_completion.update(
        {
            "message_id": "job_source_after_watch",
            "completed_at": "2026-07-30T12:00:00Z",
        }
    )
    lineage_test_support.record_terminal_execution(WORK_A, latest_completion["message_id"], ACTOR, at=latest_completion["completed_at"])
    recovered = lineage_domain.poll_all_lineage_system_events()
    duplicate = lineage_domain.poll_all_lineage_system_events()

    assert baseline["checked"] == 1
    assert baseline["emitted"] == 0
    assert unchanged["emitted"] == 0
    assert recovered["emitted"] == 1
    assert duplicate["emitted"] == 0
    assert sent == [WORK_B]


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
def test_non_success_resident_terminal_never_emits_work_finished(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    """Resident 输入失败或取消时不发出 resident.work_finished，之后成功完成才发出"""
    del primitive_store
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_target_{len(sent)}"}
        ),
    )
    monkeypatch.setattr(
        lineage_test_support,
        "LATEST_COMPLETED_MESSAGE_RESOLVER",
        lambda resident_id, _actor: (
            {
                "job_id": "job_historical_success",
                "completed_at": "2026-07-30T10:00:00Z",
            }
            if resident_id == WORK_A
            else {}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": f"{terminal_status} is not completion",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_success_only",
                        "kind": "event_source",
                        "config": {
                            "event_type": "resident.work_finished",
                        },
                    }
                ],
                "wires": [
                    {
                        "id": "wire_watch_success_only",
                        "kind": "watch",
                        "source": "event_source_success_only",
                        "source_port": "watch",
                        "target": WORK_A,
                    },
                    {'id': 'wire_success_only_target', 'kind': 'route', 'source': 'event_source_success_only', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )
    lineage_id = str(created["lineage_id"])

    # The completion that predates this Watch is only the initial cursor.
    assert lineage_domain.poll_all_lineage_system_events()["emitted"] == 0
    terminal = lineage_domain.handle_resident_message_terminal(
        WORK_A,
        ACTOR,
        f"job_{terminal_status}",
        completed_at="2026-07-30T11:00:00Z",
        lineage_id=lineage_id,
        terminal_status=terminal_status,
    )
    assert terminal["terminal_status"] == terminal_status
    assert sent == []
    assert lineage_domain.poll_all_lineage_system_events()["emitted"] == 0

    lineage_test_support.complete_resident_message(
        WORK_A,
        ACTOR,
        "job_future_success",
        completed_at="2026-07-30T12:00:00Z",
        lineage_id=lineage_id,
    )
    assert sent == [WORK_B]


def test_completion_without_an_output_wire_is_not_replayed_after_connecting(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有输出连线时完成的工作在接上连线后不补发，之后的完成才投递"""
    lineage_test_support.install_registered_lineage_members(monkeypatch)
    del primitive_store
    sent: list[str] = []
    latest_completion = {
        "message_id": "job_without_subscriber",
        "completed_at": "2026-07-30T10:00:00Z",
    }
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_target_{len(sent)}"}
        ),
    )
    monkeypatch.setattr(
        lineage_test_support,
        "LATEST_COMPLETED_MESSAGE_RESOLVER",
        lambda resident_id, _actor: (
            dict(latest_completion) if resident_id == WORK_A else {}
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Unrouted completion disappears",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_unrouted_completion",
                        "kind": "event_source",
                        "config": {
                            "event_type": "resident.work_finished",
                        },
                    }
                ],
                "wires": [
                    {
                        "id": "wire_watch_unrouted_completion",
                        "kind": "watch",
                        "source": "event_source_unrouted_completion",
                        "source_port": "watch",
                        "target": WORK_A,
                    }
                ],
            },
        },
        ACTOR,
    )

    connected = lineage_program_edits.save_lineage_program_wire(
        created["lineage_id"],
        {'expected_revision': created['revision'], 'kind': 'route', 'source': 'event_source_unrouted_completion', 'source_port': 'output', 'target': WORK_B, 'target_port': 'invoke'},
        ACTOR,
        operation="create",
    )
    assert connected["wire"]["kind"] == "route"
    historical = lineage_test_support.emit_resident_completion(
        created["lineage_id"],
        WORK_A,
        "job_without_subscriber",
        ACTOR,
        completed_at="2026-07-30T10:00:00Z",
        ports=lineage_domain._program_terminal_ports(),
    )
    assert len(historical) == 1
    assert historical[0]["matched_count"] == 0
    assert historical[0]["duplicate"] is True
    assert lineage_domain.poll_all_lineage_system_events()["emitted"] == 0
    assert sent == []

    runtime = lineage_program_runtime.read_runtime(
        created["lineage_id"]
    )
    source_runtime = runtime["event_sources"][
        "event_source_unrouted_completion"
    ]
    assert source_runtime["fire_count"] == 0
    assert source_runtime["fired_cursors"] == [
        f"message:{WORK_A}:job_without_subscriber"
    ]

    latest_completion.update(
        {
            "message_id": "job_after_subscriber",
            "completed_at": "2026-07-30T11:00:00Z",
        }
    )
    lineage_test_support.record_terminal_execution(WORK_A, "job_after_subscriber")
    assert lineage_domain.poll_all_lineage_system_events()["emitted"] == 1
    assert sent == [WORK_B]


@pytest.mark.parametrize("leader", [WORK_A, WORK_B])
def test_time_and_file_events_directly_trigger_an_ordinary_resident_once(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    leader: str,
) -> None:
    """到点事件和文件变更事件各直接触发普通 Resident 一次，均以当前 Leader 身份执行"""
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_system_{len(sent)}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Foundational system Events",
            "leader_resident_id": leader,
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "event_source_generic_time",
                        "kind": "event_source",
                        "config": {
                            "event_type": "time.reached",
                            "scheduled_at": "2000-01-01T00:00:00Z",
                        },
                    },
                    {
                        "id": "event_source_generic_file",
                        "kind": "event_source",
                        "config": {
                            "event_type": "file.changed",
                            "path": "signal.md",
                        },
                    },
                ],
                "wires": [
                    {
                        "id": "wire_watch_generic_file",
                        "kind": "watch",
                        "source": "event_source_generic_file",
                        "source_port": "watch",
                        "target": WORK_A,
                    },
                    {'id': 'wire_generic_time_worker', 'kind': 'route', 'source': 'event_source_generic_time', 'target': WORK_B},
                    {'id': 'wire_generic_file_worker', 'kind': 'route', 'source': 'event_source_generic_file', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )
    signal = (
        primitive_store
        / "lineages"
        / created["lineage_id"]
        / "residents"
        / WORK_A
        / "workspace"
        / "signal.md"
    )
    signal.parent.mkdir(parents=True, exist_ok=True)
    signal.write_text("baseline", encoding="utf-8")

    first = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    signal.write_text("changed", encoding="utf-8")
    second = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    third = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )

    assert first["baseline_count"] == 1
    assert first["event_count"] == 1
    assert second["event_count"] == 1
    assert third["event_count"] == 0
    assert sent == [WORK_B, WORK_B]


def test_effect_endpoint_references_edit_the_same_named_target_wires(
    primitive_store: Path,
) -> None:
    """Effect 端点引用新建、改指或清空同名端点连线，也可指向其他 Lineage 的 Resident"""
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Effect endpoint Resident IDs",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    {
                        "id": "effect_direct_endpoint",
                        "kind": "effect",
                        "config": deepcopy(DELIVER),
                    }
                ],
                "wires": [],
            },
        },
        ACTOR,
    )

    bound = lineage_program_edits.save_lineage_program_component(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "component_id": "effect_direct_endpoint",
            "component": {
                "config": deepcopy(DELIVER),
            },
            "endpoint_reference_ids": {"target": WORK_A},
        },
        ACTOR,
    )
    assert len(bound["created_wire_ids"]) == 1
    endpoint_wire_id = bound["created_wire_ids"][0]
    assert bound["lineage"]["program"]["wires"] == [
        {
            "id": endpoint_wire_id,
            "kind": "target",
            "source": "effect_direct_endpoint",
            "target": WORK_A,
            "source_port": "target",
        }
    ]

    retargeted = lineage_program_edits.save_lineage_program_component(
        created["lineage_id"],
        {
            "expected_revision": bound["lineage"]["revision"],
            "component_id": "effect_direct_endpoint",
            "component": {
                "config": deepcopy(DELIVER),
            },
            "endpoint_reference_ids": {"target": WORK_B},
        },
        ACTOR,
    )
    assert retargeted["created_wire_ids"] == []
    assert retargeted["updated_wire_ids"] == [endpoint_wire_id]
    assert retargeted["lineage"]["program"]["wires"][0]["target"] == WORK_B

    handed_off = lineage_program_edits.save_lineage_program_component(
        created["lineage_id"],
        {
            "expected_revision": retargeted["lineage"]["revision"],
            "component_id": "effect_direct_endpoint",
            "component": {
                "config": deepcopy(HANDOFF),
            },
            "endpoint_reference_ids": {
                "source": WORK_A,
                "target": WORK_B,
            },
        },
        ACTOR,
    )
    assert len(handed_off["created_wire_ids"]) == 1
    assert {
        wire["source_port"]: wire["target"]
        for wire in handed_off["lineage"]["program"]["wires"]
    } == {
        "source": WORK_A,
        "target": WORK_B,
    }

    source_wire_id = next(
        wire["id"]
        for wire in handed_off["lineage"]["program"]["wires"]
        if wire["source_port"] == "source"
    )
    cleared = lineage_program_edits.save_lineage_program_component(
        created["lineage_id"],
        {
            "expected_revision": handed_off["lineage"]["revision"],
            "component_id": "effect_direct_endpoint",
            "component": {
                "config": deepcopy(HANDOFF),
            },
            "endpoint_reference_ids": {
                "source": "",
                "target": WORK_B,
            },
        },
        ACTOR,
    )
    assert cleared["removed_wire_ids"] == [source_wire_id]
    assert {
        wire["source_port"]: wire["target"]
        for wire in cleared["lineage"]["program"]["wires"]
    } == {"target": WORK_B}

    lineage_lifecycle.create_lineage({"title": "External source", "nodes": [_worker(WORK_C)]}, ACTOR)
    external_source = lineage_program_edits.save_lineage_program_component(
        created["lineage_id"],
        {
            "expected_revision": cleared["lineage"]["revision"],
            "component_id": "effect_direct_endpoint",
            "component": {
                "config": deepcopy(HANDOFF),
            },
            "endpoint_reference_ids": {
                "source": WORK_C,
                "target": WORK_B,
            },
        },
        ACTOR,
    )
    assert {
        wire["source_port"]: wire["target"]
        for wire in external_source["lineage"]["program"]["wires"]
    } == {"source": WORK_C, "target": WORK_B}


def test_packet_retargets_the_same_typed_wire(
    primitive_store: Path,
) -> None:
    """修改路由连线目标时沿用同一连线 ID，可在 Effect 和 Resident 之间切换"""
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Typed wire endpoint editing",
            "nodes": [
                _worker(WORK_A, "Worker A"),
                _worker(WORK_B, "Worker B"),
                _worker(WORK_D, "Critic"),
                _reducer_executor(),
            ],
            "program": {
                "components": [
                    _reducer_component(),
                    {
                        "id": "effect_endpoint_edit",
                        "kind": "effect",
                        "config": deepcopy(DELIVER),
                    }
                ],
                "wires": [
                    {'id': 'wire_packet_endpoint_edit', 'kind': 'route', 'source': WORK_D, 'target': REDUCER},
                ],
            },
        },
        ACTOR,
    )

    proposed = lineage_program_edits.save_lineage_program_wire(
        created["lineage_id"],
        {
            "expected_revision": created["revision"],
            "wire_id": "wire_packet_endpoint_edit",
            "wire": {'kind': 'route', 'source': WORK_D, 'source_port': 'output', 'target': 'effect_endpoint_edit', 'target_port': 'invoke'},
        },
        ACTOR,
    )
    assert proposed["wire"]["id"] == "wire_packet_endpoint_edit"
    assert proposed["wire"]["target"] == "effect_endpoint_edit"

    direct = lineage_program_edits.save_lineage_program_wire(
        created["lineage_id"],
        {
            "expected_revision": proposed["lineage"]["revision"],
            "wire_id": "wire_packet_endpoint_edit",
            "wire": {'kind': 'route', 'source': WORK_D, 'source_port': 'output', 'target': WORK_A, 'target_port': 'invoke'},
        },
        ACTOR,
    )
    assert direct["wire"]["target"] == WORK_A
    assert direct["wire"]["target_port"] == "invoke"


@pytest.mark.parametrize(
    ("effect_type", "obsolete_config"),
    [
        ("resident.fork", {"launch_after_fork": True}),
        (
            "resident.fork",
            {"default_arguments": {"prompt": "Legacy implicit first task."}},
        ),
        ("workspace.handoff", {"launch_after_ready": True}),
    ],
)
def test_effect_authoring_rejects_obsolete_implicit_launch_config(
    primitive_store: Path,
    effect_type: str,
    obsolete_config: dict[str, object],
) -> None:
    """Effect 配置拒绝 launch_after_fork、默认 prompt 等旧的隐式启动字段"""
    del primitive_store
    obsolete_field = next(iter(obsolete_config))
    with pytest.raises(ValueError, match=obsolete_field):
        lineage_program.normalize_program(
            {
                "components": [
                    {
                        "id": "effect_fork_without_prompt",
                        "kind": "effect",
                        "config": {
                            "effect_type": effect_type,
                            **obsolete_config,
                        },
                    },
                ],
                "wires": [],
            },
            lineage_nodes=[_worker(WORK_A, "Fork source")],
        )


def test_event_can_drive_reducer_then_effect(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事件先驱动 Reducer，Reducer 完成后触发 Effect 并投递到下游 Resident"""
    lineage_test_support.install_registered_lineage_members(monkeypatch)
    sent: list[tuple[str, str]] = []
    dispatch_actors: list[str] = []

    def send(node: dict, _prompt: str, _actor: str) -> dict:
        job_id = f"job_{len(sent) + 1}"
        sent.append((str(node["id"]), job_id))
        dispatch_actors.append(_actor)
        return {"message_id": job_id}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_MESSAGE_EVIDENCE_RESOLVER",
        lambda _resident_id, job_id, _actor: {
            "job_id": job_id,
            "reply": _reducer_reply("Reducer transformed the Event input."),
        },
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Event to Reducer to Effect",
            "nodes": [
                _reducer_executor(),
                _worker(WORK_B, "Downstream worker"),
            ],
            "program": {
                "components": [
                    _reducer_component(),
                    {
                        "id": "event_source_direct_reducer",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    },
                    {
                        "id": "effect_emit_direct_reducer",
                        "kind": "effect",
                        "config": deepcopy(QUIET),
                    },
                    {
                        "id": "event_source_direct_reducer_emitted",
                        "kind": "event_source",
                        "config": {
                            "event_type": "effect.succeeded",
                        },
                    },
                ],
                "wires": [
                    {'id': 'wire_event_direct_reducer', 'kind': 'route', 'source': 'event_source_direct_reducer', 'target': REDUCER},
                    {'id': 'wire_direct_reducer_emit', 'kind': 'route', 'source': REDUCER, 'target': 'effect_emit_direct_reducer'},
                    {
                        "id": "wire_watch_direct_reducer_emit",
                        "kind": "watch",
                        "source": "event_source_direct_reducer_emitted",
                        "source_port": "watch",
                        "target": "effect_emit_direct_reducer",
                    },
                    {'id': 'wire_direct_reducer_worker', 'kind': 'route', 'source': 'event_source_direct_reducer_emitted', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )

    emitted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_direct_reducer"},
        ACTOR,
    )
    assert emitted["matched_count"] == 1
    assert sent == [(REDUCER_EXECUTOR, "job_1")]

    completed = lineage_test_support.complete_resident_message(
        REDUCER_EXECUTOR,
        ACTOR,
        "job_1",
        lineage_id=created["lineage_id"],
    )
    assert completed["operator_result_count"] == 1
    assert sent == [
        (REDUCER_EXECUTOR, "job_1"),
        (WORK_B, "job_2"),
    ], json.dumps(completed, ensure_ascii=False)
    assert completed["operator_results"][0]["status"] == "reduced"
    assert dispatch_actors[-1] == REDUCER_EXECUTOR


def test_resident_packet_can_drive_reducer_then_endpoint_free_effect(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 回复的 packet 可经 Reducer 驱动无端点的 Effect，再投递到下游 Resident"""
    lineage_test_support.install_registered_lineage_members(monkeypatch)
    sent: list[tuple[str, str]] = []
    dispatch_actors: list[str] = []

    def send(node: dict, _prompt: str, _actor: str) -> dict:
        job_id = f"job_{len(sent) + 1}"
        sent.append((str(node["id"]), job_id))
        dispatch_actors.append(_actor)
        return {"message_id": job_id}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_MESSAGE_EVIDENCE_RESOLVER",
        lambda resident_id, job_id, _actor: {
            "job_id": job_id,
            "reply": (_reducer_reply if resident_id == REDUCER_EXECUTOR else _packet_reply)(
                "Merged instruction from the reducer."
            ),
        },
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Reducer to endpoint-free Effect",
            "nodes": [
                _worker(WORK_D, "Proposer"),
                _reducer_executor(),
                _worker(WORK_B, "Downstream worker"),
            ],
            "program": {
                "components": [
                    _reducer_component(),
                    {
                        "id": "event_source_reducer",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    },
                    {
                        "id": "effect_emit_reduced",
                        "kind": "effect",
                        "config": deepcopy(QUIET),
                    },
                    {
                        "id": "event_source_reduced_emitted",
                        "kind": "event_source",
                        "config": {
                            "event_type": "effect.succeeded",
                        },
                    },
                ],
                "wires": [
                    {'id': 'wire_event_producer', 'kind': 'route', 'source': 'event_source_reducer', 'target': WORK_D},
                    {'id': 'wire_producer_reducer', 'kind': 'route', 'source': WORK_D, 'target': REDUCER},
                    {'id': 'wire_reducer_emit', 'kind': 'route', 'source': REDUCER, 'target': 'effect_emit_reduced'},
                    {
                        "id": "wire_watch_reduced_emit",
                        "kind": "watch",
                        "source": "event_source_reduced_emitted",
                        "source_port": "watch",
                        "target": "effect_emit_reduced",
                    },
                    {'id': 'wire_emit_worker', 'kind': 'route', 'source': 'event_source_reduced_emitted', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )
    assert lineage_program.program_diagnostics(
        created["program"],
        created["nodes"],
    ) == []

    emitted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_reducer"},
        ACTOR,
    )
    assert emitted["matched_count"] == 1
    assert sent == [(WORK_D, "job_1")]

    proposed = lineage_test_support.complete_resident_message(
        WORK_D,
        ACTOR,
        "job_1",
        lineage_id=created["lineage_id"],
    )
    assert proposed["agent_result_count"] == 1
    assert sent == [(WORK_D, "job_1"), (REDUCER_EXECUTOR, "job_2")]

    completed = lineage_test_support.complete_resident_message(
        REDUCER_EXECUTOR,
        ACTOR,
        "job_2",
        lineage_id=created["lineage_id"],
    )
    assert completed["operator_result_count"] == 1
    assert sent == [
        (WORK_D, "job_1"),
        (REDUCER_EXECUTOR, "job_2"),
        (WORK_B, "job_3"),
    ]
    reducer_result = completed["operator_results"][0]
    assert reducer_result["status"] == "reduced"
    assert dispatch_actors[-1] == REDUCER_EXECUTOR


def test_pre_dispatch_reducer_failure_releases_generic_event_route_claim(
    primitive_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reducer 在派发前失败时释放事件路由占用，存储恢复后由积压重试投递到全部目标"""
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_{len(sent)}"}
        ),
    )
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Retry before executable intent",
            "nodes": [
                _reducer_executor(),
                _worker(WORK_A),
                _worker(WORK_B),
            ],
            "program": {
                "components": [
                    _reducer_component(),
                    {
                        "id": "event_source_retry",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                        },
                    },
                    {
                        "id": "effect_emit_retry",
                        "kind": "effect",
                        "config": deepcopy(QUIET),
                    },
                    {
                        "id": "event_source_retry_emitted",
                        "kind": "event_source",
                        "config": {
                            "event_type": "effect.succeeded",
                        },
                    },
                ],
                "wires": [
                    # The barrier belongs to this Event's operator branch.
                    # A Resident reply would start a separate occurrence.
                    {'id': 'wire_retry_producer', 'kind': 'route', 'source': 'event_source_retry', 'target': REDUCER},
                    {'id': 'wire_retry_worker', 'kind': 'route', 'source': 'event_source_retry', 'target': WORK_A},
                    {'id': 'wire_retry_emit', 'kind': 'route', 'source': REDUCER, 'target': 'effect_emit_retry'},
                    {
                        "id": "wire_watch_retry_emit",
                        "kind": "watch",
                        "source": "event_source_retry_emitted",
                        "source_port": "watch",
                        "target": "effect_emit_retry",
                    },
                    {'id': 'wire_retry_emitted_worker', 'kind': 'route', 'source': 'event_source_retry_emitted', 'target': WORK_B},
                ],
            },
        },
        ACTOR,
    )
    event = lineage_primitives.make_event(
        "manual.triggered",
        correlation="generic-route-retry",
    )
    real_prepare = (
        lineage_state.lineage_operator_runtime.prepare_inbox
    )

    def fail_prepare(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("reducer storage unavailable")

    monkeypatch.setattr(
        lineage_state.lineage_operator_runtime,
        "prepare_inbox",
        fail_prepare,
    )
    with pytest.raises(RuntimeError, match="reducer storage unavailable"):
        lineage_domain.dispatch_lineage_program_event(
            created["lineage_id"],
            "event_source_retry",
            event,
            ACTOR,
            cursor="manual:retry-same-occurrence",
        )
    assert sent == []

    monkeypatch.setattr(
        lineage_state.lineage_operator_runtime,
        "prepare_inbox",
        real_prepare,
    )
    # Storage recovery does not bypass the failed attempt's retry delay.
    from management_portal.society_service import lineage_program_backlog
    waiting = lineage_program_backlog.drain_event_backlog(
        created["lineage_id"], ACTOR, authority=lineage_domain._program_control_authority(),
    )
    assert waiting["attempted_count"] == 0
    with lineage_program_runtime._runtime_transaction(created["lineage_id"]) as runtime:
        for wire in runtime["event_wires"].values():
            for pending in wire["pending_events"]:
                assert pending.get("retry_at")
                pending.pop("retry_at")
    retried = lineage_program_backlog.drain_event_backlog(
        created["lineage_id"], ACTOR, authority=lineage_domain._program_control_authority(),
    )
    assert retried["delivered_count"] == 2, json.dumps(retried)
    assert sorted(sent) == sorted([WORK_A, REDUCER_EXECUTOR])
