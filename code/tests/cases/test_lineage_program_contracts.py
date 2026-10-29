from __future__ import annotations

from tests.cases.society_service_test_support import install_resource_boundary

from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

import pytest

from management_portal.society_service import lineage_domain
from management_portal.society_service import lineage_program
from management_portal.society_service import lineage_recurring_schedule
from management_portal.society_service import lineage_aggregate
from tests.cases.effect_script_support import effect_config, lower_effect_call


ACTOR = "sample_admin"
ROLE_A = "resident_aaaaaaaaaaaa"
ROLE_B = "resident_bbbbbbbbbbbb"
REDUCER = "reducer_cccccccccccc"
WORK_B = "resident_dddddddddddd"
WORK_C = "resident_eeeeeeeeeeee"
SELECTOR = "selector_ffffffffffff"


def test_recurring_jitter_config_is_normalized_and_bounded() -> None:
    """周期调度规范化抖动值并拒绝越界或无效类型"""
    config = {
        "event_type": "time.reached",
        "schedule_mode": "recurring",
        "recurrence_kind": "interval",
        "interval_seconds": 60,
        "jitter_seconds": 10,
    }

    def normalize(value: dict) -> dict:
        return lineage_program.normalize_program(
            {"components": [{"id": "event_source_jitter", "kind": "event_source", "config": value}]},
            lineage_nodes=[],
        )["components"][0]["config"]

    assert normalize(config)["jitter_seconds"] == 10
    # Pausing is runtime state (PauseComponent); the definition has no enabled switch.
    for value in (True, False):
        with pytest.raises(ValueError, match="enabled"):
            normalize({**config, "enabled": value})
    assert normalize({
        "event_type": "time.reached",
        "schedule_mode": "recurring",
        "recurrence_kind": "calendar",
        "jitter_seconds": 86_400,
    })["jitter_seconds"] == 86_400
    for invalid in (-1, 60, 86_401, 1.5, True):
        with pytest.raises(ValueError, match="jitter_seconds"):
            normalize({**config, "jitter_seconds": invalid})
    assert lineage_recurring_schedule.fingerprint(
        {key: value for key, value in config.items() if key != "jitter_seconds"}
    ) == (
        lineage_recurring_schedule.fingerprint(
            {**config, "jitter_seconds": 0}
        )
    )
    assert lineage_recurring_schedule.fingerprint(config) != (
        lineage_recurring_schedule.fingerprint(
            {**config, "jitter_seconds": 5}
        )
    )
    assert lineage_recurring_schedule.fingerprint(config) == (
        lineage_recurring_schedule.fingerprint({**config, "enabled": False})
    )


def _worker(resident_id: str) -> dict:
    return {
        "id": resident_id,
        "title": resident_id,
    }


def _nodes() -> list[dict]:
    return [
        _worker(ROLE_A),
        _worker(ROLE_B),
        _worker(WORK_B),
        _worker(WORK_C),
    ]


def test_program_topology_can_exceed_previous_component_and_wire_caps() -> None:
    """程序接受超过旧组件数与连线数上限的拓扑"""
    component_heavy = lineage_program.normalize_program(
        {
            "components": [
                {
                    "id": f"event_source_unbounded_{index}",
                    "kind": "event_source",
                    "config": {"event_type": "manual.triggered"},
                }
                for index in range(193)
            ],
            "wires": [],
        },
        lineage_nodes=[],
    )
    assert len(component_heavy["components"]) == 193

    event_sources = [
        {
            "id": f"event_source_wire_{index}",
            "kind": "event_source",
            "config": {"event_type": "manual.triggered"},
        }
        for index in range(23)
    ]
    effects = [
        {
            "id": f"effect_emit_wire_{index}",
            "kind": "effect",
            "config": effect_config("TriggerEvent", {"event_name": {"value": f"unbounded.{index}"}}, []),
        }
        for index in range(23)
    ]
    wire_pairs = [
        (source["id"], target["id"])
        for source in event_sources
        for target in effects
    ][:513]
    wire_heavy = lineage_program.normalize_program(
        {
            "components": [*event_sources, *effects],
            "wires": [
                {'id': f'wire_unbounded_{index}', 'kind': 'route', 'source': source, 'target': target}
                for index, (source, target) in enumerate(wire_pairs)
            ],
        },
        lineage_nodes=[],
    )

    assert len(wire_heavy["wires"]) == 513



def _composed_program() -> dict:
    return {
        "schema": "tfi.hatg-program.v2",
        "components": [
            {
                "id": "event_source_work_b_finished",
                "kind": "event_source",
                "config": {
                    "event_type": "resident.work_finished",
                },
            },
            {
                "id": "effect_send_c",
                "kind": "effect",
                "config": effect_config("DeliverMessage", {"resident_id": {"from": "endpoint.target"}}, ["target"]),
            },
            {
                "id": REDUCER,
                "kind": "reducer",
                "title": "Reducer",
                "config": {
                    "operator_implementation": "rules",
                    "prompt": "Reduce.",
                    "reduce_strategy": "join",
                    "reduce_separator": "\n\n",
                    "output_key": "reduced",
                },
            },
        ],
        "wires": [
            {
                "id": "wire_watch_work_b_finished",
                "kind": "watch",
                "source": "event_source_work_b_finished",
                "source_port": "watch",
                "target": WORK_B,
            },
            {'id': 'wire_event_role_a', 'kind': 'route', 'source': 'event_source_work_b_finished', 'target': ROLE_A},
            {'id': 'wire_event_role_b', 'kind': 'route', 'source': 'event_source_work_b_finished', 'target': ROLE_B},
            {'id': 'wire_a_reducer', 'kind': 'route', 'source': ROLE_A, 'target': REDUCER},
            {'id': 'wire_b_reducer', 'kind': 'route', 'source': ROLE_B, 'target': REDUCER},
            {'id': 'wire_reducer_effect', 'kind': 'route', 'source': REDUCER, 'target': 'effect_send_c'},
            {
                "id": "wire_effect_target",
                "kind": "target",
                "source": "effect_send_c",
                "target": WORK_C,
            },
        ],
    }


def _items_by_id(items: list[dict]) -> dict[str, dict]:
    return {str(item["id"]): item for item in items}


@pytest.fixture()
def backend_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    root = tmp_path / "lineages"
    monkeypatch.setattr(lineage_aggregate, "DATA_ROOT", root)
    monkeypatch.setattr(lineage_domain, "LINEAGE_FOLDER_ROOT", root / "lineages")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(root / "lineages"))
    install_resource_boundary(monkeypatch)
    return root


def _authoring_round_trip_lineage() -> dict:
    return lineage_domain.create_lineage(
        {
            "title": "Program authoring round trip",
            "project_id": "project_test",
            "nodes": _nodes(),
            "program": {
                "components": [
                    {
                        "id": "event_source_round_trip",
                        "kind": "event_source",
                        "config": {
                            "event_type": "resident.work_finished",
                        },
                        "title": "B 完成一轮",
                    }
                ],
                "wires": [
                    {
                        "id": "wire_watch_round_trip",
                        "kind": "watch",
                        "source": "event_source_round_trip",
                        "source_port": "watch",
                        "target": WORK_B,
                    },
                ],
            },
        },
        ACTOR,
    )


def _stored_program_item(
    lineage: dict,
    collection: str,
    item_id: str,
) -> dict:
    return next(
        item
        for item in lineage["program"][collection]
        if item["id"] == item_id
    )


def test_normalize_program_preserves_one_event_fanout_and_exact_effect_target_contracts(subtests, isolated_portal_case):
    """程序规范化保留事件分发与精确目标并约束连线策略"""
    with subtests.test(contract='test_normalize_program_preserves_one_event_fanout_and_exact_effect_target'), isolated_portal_case() as case_path:
        normalized = lineage_program.normalize_program(
            _composed_program(),
            lineage_nodes=_nodes(),
        )

        assert normalized["schema"] == "tfi.hatg-program.v2"
        wires = _items_by_id(normalized["wires"])
        assert {
            wires["wire_event_role_a"]["target"],
            wires["wire_event_role_b"]["target"],
        } == {ROLE_A, ROLE_B}
        assert wires["wire_event_role_a"]["config"] == {
            "unavailable_policy": "drop",
            "expires_after_seconds": 0,
        }
        assert wires["wire_effect_target"] == {
            "id": "wire_effect_target",
            "kind": "target",
            "source": "effect_send_c",
            "source_port": "target",
            "target": WORK_C,
        }
    with subtests.test(contract='test_context_pressure_event_normalizes_a_bounded_percentage_threshold'), isolated_portal_case() as case_path:
        program = lineage_program.normalize_program(
            {
                "schema": "tfi.hatg-program.v2",
                "components": [
                    {
                        "id": "event_source_context_pressure",
                        "kind": "event_source",
                        "config": {
                            "event_type": (
                                "resident.context_pressure_exceeded"
                            ),
                            "threshold_percent": 82.54,
                        },
                    }
                ],
                "wires": [],
            },
            lineage_nodes=_nodes(),
        )

        assert program["components"][0]["config"]["threshold_percent"] == 82.5

        with pytest.raises(ValueError, match="0 到 100"):
            lineage_program.normalize_program(
                {
                    "schema": "tfi.hatg-program.v2",
                    "components": [
                        {
                            "id": "event_source_context_pressure_invalid",
                            "kind": "event_source",
                            "config": {
                                "event_type": (
                                    "resident.context_pressure_exceeded"
                                ),
                                "threshold_percent": 100.1,
                            },
                        }
                    ],
                    "wires": [],
                },
                lineage_nodes=_nodes(),
            )
    with subtests.test(contract='test_generic_effect_keeps_component_identity_across_round_trips'), isolated_portal_case() as case_path:
        config = effect_config("CreateResident", {"title": {"value": "Builder"}, "provider": {"value": "codex"}}, [])
        authored = {
            "schema": "tfi.hatg-program.v2",
            "components": [{"id": "effect_create_builder", "kind": "effect", "config": config}],
            "wires": [],
        }
        first = lineage_program.normalize_program(authored, lineage_nodes=_nodes())
        second = lineage_program.normalize_program(first, lineage_nodes=_nodes())
        component = second["components"][0]
        assert component["kind"] == "effect"
        assert component["config"] == config
        compiled = lineage_program.compile_program(second, _nodes())["effects"]["effect_create_builder"]
        assert compiled["script"].startswith("CreateResident ")
    with subtests.test(contract='test_event_wire_normalizes_unavailable_policy_queue_limit_and_ttl'), isolated_portal_case() as case_path:
        raw = _composed_program()
        event_wire = next(
            item
            for item in raw["wires"]
            if item["id"] == "wire_event_role_a"
        )
        event_wire["config"] = {
            "unavailable_policy": "queue",
            "queue_limit": 999,
            "expires_after_seconds": 9999999,
        }

        normalized = lineage_program.normalize_program(
            raw,
            lineage_nodes=_nodes(),
        )
        stored = _items_by_id(normalized["wires"])[
            "wire_event_role_a"
        ]
        assert stored["config"] == {
            "unavailable_policy": "queue",
            "queue_limit": 100,
            "expires_after_seconds": 604800,
        }

        event_wire["config"] = {
            "unavailable_policy": "latest",
            "queue_limit": 50,
            "expires_after_seconds": 45,
        }
        normalized = lineage_program.normalize_program(
            raw,
            lineage_nodes=_nodes(),
        )
        assert _items_by_id(normalized["wires"])[
            "wire_event_role_a"
        ]["config"] == {
            "unavailable_policy": "latest",
            "expires_after_seconds": 45,
        }
    with subtests.test(contract='test_wire_ports_are_canonicalized_and_invalid_anchors_are_rejected'), isolated_portal_case() as case_path:
        raw = _composed_program()
        normalized = lineage_program.normalize_program(
            raw,
            lineage_nodes=_nodes(),
        )
        event_wire = _items_by_id(normalized["wires"])[
            "wire_event_role_a"
        ]
        assert event_wire["source_port"] == "output"
        assert event_wire["target_port"] == "invoke"

        next(
            wire
            for wire in raw["wires"]
            if wire["id"] == "wire_event_role_a"
        )["source_port"] = "packet"
        with pytest.raises(ValueError, match="Route 必须来自 output 出口"):
            lineage_program.normalize_program(
                raw,
                lineage_nodes=_nodes(),
            )


def test_event_source_uses_null_for_unbounded_and_keeps_zero_literal() -> None:
    """事件源以 null 表示无上限并保留零次限制"""
    def normalized_limit(value: object) -> object:
        program = lineage_program.normalize_program(
            {
                "schema": "tfi.hatg-program.v2",
                "components": [
                    {
                        "id": "event_source_manual_limit",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                            "max_fires": value,
                        },
                    }
                ],
                "wires": [],
            },
            lineage_nodes=_nodes(),
        )
        return program["components"][0]["config"]["max_fires"]

    assert normalized_limit(None) is None
    assert normalized_limit(0) == 0
    assert normalized_limit(3) == 3


def test_new_system_events_normalize_exact_typed_configuration() -> None:
    """系统事件按类型保留时长队列与状态条件配置"""
    program = lineage_program.normalize_program(
        {
            "schema": "tfi.hatg-program.v2",
            "components": [
                {
                    "id": "event_source_work_duration",
                    "kind": "event_source",
                    "config": {
                        "event_type": "resident.work_duration_exceeded",
                        "duration_seconds": 120,
                    },
                },
                {
                    "id": "event_source_queue_wait",
                    "kind": "event_source",
                    "config": {
                        "event_type": "resident.queue_pressure_exceeded",
                        "pressure_metric": "oldest_wait_seconds",
                        "wait_seconds_threshold": 45,
                    },
                },
                {
                    "id": "event_source_state_condition",
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
            "wires": [],
        },
        lineage_nodes=_nodes(),
    )
    components = {
        component["id"]: component
        for component in program["components"]
    }

    assert (
        components["event_source_work_duration"]["config"]["duration_seconds"]
        == 120
    )
    assert components["event_source_queue_wait"] == {
        "id": "event_source_queue_wait",
        "kind": "event_source",
        "config": {
            "event_type": "resident.queue_pressure_exceeded",
            "max_fires": None,
            "pressure_metric": "oldest_wait_seconds",
            "wait_seconds_threshold": 45,
        },
    }
    assert components["event_source_state_condition"] == {
        "id": "event_source_state_condition",
        "kind": "event_source",
        "config": {
            "event_type": "state.condition_met",
            "max_fires": None,
            "key": "review.score",
            "operator": "greater_or_equal",
            "expected_value": 8,
            "fire_on_initial_match": True,
        },
    }


def test_new_system_events_reject_invalid_configuration(subtests, isolated_portal_case):
    """系统事件拒绝无效时长压力指标与状态条件"""
    for config, message in [
        (
            {
                "event_type": "resident.work_duration_exceeded",
                "duration_seconds": 0,
            },
            "1 到 604800",
        ),
        (
            {
                "event_type": "resident.queue_pressure_exceeded",
                "pressure_metric": "unknown",
            },
            "压力指标无效",
        ),
        (
            {
                "event_type": "state.condition_met",
                "key": "",
                "operator": "equals",
                "expected_value": None,
                "fire_on_initial_match": False,
            },
            "有效的 key",
        ),
        (
            {
                "event_type": "state.condition_met",
                "key": "review.score",
                "operator": "contains",
                "expected_value": 8,
                "fire_on_initial_match": False,
            },
            "operator 无效",
        ),
    ]:
        with subtests.test(config=config, message=message), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match=message):
                lineage_program.normalize_program(
                    {
                        "schema": "tfi.hatg-program.v2",
                        "components": [
                            {
                                "id": "event_source_invalid_system_event",
                                "kind": "event_source",
                                "config": config,
                            }
                        ],
                        "wires": [],
                    },
                    lineage_nodes=_nodes(),
                )


def test_program_rejects_removed_function_block_owner_field() -> None:
    """程序拒绝已移除的块实例归属字段"""
    with pytest.raises(ValueError, match="function_block_instance_id"):
        lineage_program.normalize_program({"components": [{"id": "event_source_0123456789ab", "kind": "event_source",
            "config": {"event_type": "manual.triggered"}, "function_block_instance_id": "fbi_0123456789ab"}], "wires": []}, lineage_nodes=[])
    with pytest.raises(ValueError, match="function_block_instances"):
        lineage_program.normalize_program({"function_block_instances": []}, lineage_nodes=[])
    raw = _composed_program()
    raw['wires'][0]['function_block_instance_id'] = 'fbi_0123456789ab'
    with pytest.raises(ValueError, match="function_block_instance_id"):
        lineage_program.normalize_program(raw, lineage_nodes=_nodes())


def test_event_wire_rejects_unknown_policy_and_accepts_file_changed_queue() -> None:
    """事件连线拒绝未知策略并接受文件变化排队策略"""
    raw = _composed_program()
    event_wire = next(
        item
        for item in raw["wires"]
        if item["id"] == "wire_event_role_a"
    )
    event_wire["config"] = {"unavailable_policy": "forever"}
    with pytest.raises(ValueError, match="drop、latest 或 queue"):
        lineage_program.normalize_program(raw, lineage_nodes=_nodes())

    raw = _composed_program()
    raw["components"].append(
        {
            "id": "event_source_report_changed",
            "kind": "event_source",
            "config": {
                "event_type": "file.changed",
                "path": "output/report.md",
            },
        }
    )
    raw["wires"].append(
        {
            "id": "wire_watch_report_changed",
            "kind": "watch",
            "source": "event_source_report_changed",
            "source_port": "watch",
            "target": WORK_B,
        }
    )
    raw["wires"].append(
        {'id': 'wire_file_changed_role', 'kind': 'route', 'source': 'event_source_report_changed', 'target': ROLE_A, 'config': {'unavailable_policy': 'queue', 'queue_limit': 5}}
    )
    normalized = lineage_program.normalize_program(
        raw,
        lineage_nodes=_nodes(),
    )
    stored = _items_by_id(normalized["wires"])[
        "wire_file_changed_role"
    ]
    assert stored["config"] == {
        "unavailable_policy": "queue",
        "expires_after_seconds": 0,
        "queue_limit": 5,
    }




def test_system_event_sources_are_persisted_as_locations_not_event_snapshots(subtests, isolated_portal_case):
    """系统事件源保存定位配置并排除事件快照字段"""
    for component in [
        {
            "id": "event_source_deadline",
            "kind": "event_source",
            "config": {
                "event_type": "time.reached",
                "scheduled_at": "2099-01-01T00:00:00Z",
            },
        },
        {
            "id": "event_source_report",
            "kind": "event_source",
            "config": {
                "event_type": "file.changed",
                "path": "output/report.md",
            },
        },
    ]:
        with subtests.test(component=component), isolated_portal_case() as case_path:
            raw = _composed_program()
            raw["components"].append(component)

            normalized = lineage_program.normalize_program(
                raw,
                lineage_nodes=_nodes(),
            )
            stored = _items_by_id(normalized["components"])[component["id"]]

            assert stored["config"]["event_type"] == component["config"]["event_type"]
            assert not {
                "payload",
                "reply",
                "workspace",
                "snapshot",
                "receipt",
            } & set(stored)


def test_wire_client_key_change_preserves_committed_graph(
    backend_store: Path,
) -> None:
    """连线拒绝修改已保存的 client_key 且保留已提交图"""
    created = _authoring_round_trip_lineage()
    lineage_id = created["lineage_id"]
    saved = lineage_domain.save_lineage_program_wire(
        lineage_id,
        {
            "expected_revision": created["revision"],
            "wire_id": "wire_watch_round_trip",
            "wire": {"client_key": "watch-one"},
        },
        ACTOR,
        operation="update",
    )
    with pytest.raises(ValueError, match="client_key"):
        lineage_domain.save_lineage_program_wire(
            lineage_id,
            {
                "expected_revision": saved["lineage"]["revision"],
                "wire_id": "wire_watch_round_trip",
                "wire": {"client_key": "watch-two"},
            },
            ACTOR,
            operation="update",
        )
    current = lineage_domain.get_lineage(lineage_id, ACTOR)
    assert current["revision"] == saved["lineage"]["revision"]
    assert next(
        wire for wire in current["program"]["wires"]
        if wire["id"] == "wire_watch_round_trip"
    )["client_key"] == "watch-one"


def test_event_component_save_config_is_authoritative_across_type_changes(
    backend_store: Path,
) -> None:
    """切换事件类型时保存配置清除旧类型字段"""
    created = _authoring_round_trip_lineage()
    lineage_id = created["lineage_id"]

    file_changed = lineage_domain.save_lineage_program_component(
        lineage_id,
        {
            "expected_revision": created["revision"],
            "component_id": "event_source_round_trip",
            "component": {
                "title": "报告发生变化",
                "config": {
                    "event_type": "file.changed",
                    "path": "output/report.md",
                },
            },
            "watch_subject": {"kind": "resident", "id": WORK_C},
        },
        ACTOR,
    )
    file_component = file_changed["component"]
    assert file_component["config"]["event_type"] == "file.changed"
    assert file_component["config"]["path"] == "output/report.md"
    file_watch = next(
        wire
        for wire in file_changed["lineage"]["program"]["wires"]
        if wire["kind"] == "watch"
        and wire["source"] == "event_source_round_trip"
    )
    assert file_watch["target"] == WORK_C
    assert (
        _stored_program_item(
            lineage_domain.get_lineage(lineage_id, ACTOR),
            "components",
            "event_source_round_trip",
        )
        == file_component
    )

    time_reached = lineage_domain.save_lineage_program_component(
        lineage_id,
        {
            "expected_revision": file_changed["lineage"]["revision"],
            "component_id": "event_source_round_trip",
            "component": {
                "config": {
                    "event_type": "time.reached",
                    "scheduled_at": "2099-01-02T03:04:05Z",
                },
            },
            "watch_subject": {},
        },
        ACTOR,
    )
    time_component = time_reached["component"]
    assert time_component["config"]["event_type"] == "time.reached"
    assert time_component["config"]["scheduled_at"] == (
        "2099-01-02T03:04:05Z"
    )
    assert not {
        "subject_resident_id",
        "path",
    } & set(time_component["config"])
    assert not any(
        wire["kind"] == "watch"
        and wire["target"] == "event_source_round_trip"
        for wire in time_reached["lineage"]["program"]["wires"]
    )

    manual = lineage_domain.save_lineage_program_component(
        lineage_id,
        {
            "expected_revision": time_reached["lineage"]["revision"],
            "component_id": "event_source_round_trip",
            "component": {
                "config": {
                    "event_type": "manual.triggered",
                },
            },
            "watch_subject": {},
        },
        ACTOR,
    )
    manual_component = manual["component"]
    assert manual_component["config"]["event_type"] == "manual.triggered"
    assert not {
        "subject_resident_id",
        "path",
        "scheduled_at",
    } & set(manual_component["config"])
    assert (
        _stored_program_item(
            lineage_domain.get_lineage(lineage_id, ACTOR),
            "components",
            "event_source_round_trip",
        )
        == manual_component
    )




def test_program_rejects_non_component_kinds(subtests, isolated_portal_case):
    """程序拒绝将非组件类型作为组件"""
    for unsupported_kind in ["projection", "receipt", "loop_instance", "packet"]:
        with subtests.test(unsupported_kind=unsupported_kind), isolated_portal_case() as case_path:
            raw = _composed_program()
            raw["components"].append(
                {
                    "id": f"unsupported_{unsupported_kind}",
                    "kind": unsupported_kind,
                }
            )

            with pytest.raises(ValueError, match="component|组件|kind|类型"):
                lineage_program.normalize_program(raw, lineage_nodes=_nodes())


def test_normalize_program_rejects_typed_wire_endpoint_mismatches(subtests, isolated_portal_case):
    """程序拒绝连线类型与端口角色不匹配"""
    for wire, message in [
        (
            {'id': 'wire_bad_event', 'kind': 'route', 'source': 'effect_send_c', 'target': WORK_C},
            "Route 的来源必须具有包输出端口",
        ),
        (
            {
                "id": "wire_bad_target",
                "kind": "target",
                "source": ROLE_A,
                "target": WORK_C,
            },
            "Effect",
        ),
    ]:
        with subtests.test(wire=wire, message=message), isolated_portal_case() as case_path:
            raw = _composed_program()
            raw["wires"].append(wire)

            with pytest.raises(ValueError, match=message):
                lineage_program.normalize_program(raw, lineage_nodes=_nodes())


def test_normalize_program_allows_composed_cycle_without_loop_instance() -> None:
    """程序允许通过事件组合形成无循环实例的闭环"""
    raw = _composed_program()
    raw["components"].extend(
        [
            {
                "id": "event_source_work_c_finished",
                "kind": "event_source",
                "config": {
                    "event_type": "resident.work_finished",
                },
            },
            {
                "id": "effect_send_b",
                "kind": "effect",
                "config": effect_config("DeliverMessage", {"resident_id": {"from": "endpoint.target"}}, ["target"]),
            },
        ]
    )
    raw["wires"].extend(
        [
            {
                "id": "wire_watch_work_c_finished",
                "kind": "watch",
                "source": "event_source_work_c_finished",
                "source_port": "watch",
                "target": WORK_C,
            },
            {'id': 'wire_c_event_a', 'kind': 'route', 'source': 'event_source_work_c_finished', 'target': ROLE_A},
            {'id': 'wire_a_effect_b', 'kind': 'route', 'source': ROLE_A, 'target': 'effect_send_b'},
            {
                "id": "wire_effect_b_target",
                "kind": "target",
                "source": "effect_send_b",
                "target": WORK_B,
            },
        ]
    )

    normalized = lineage_program.normalize_program(
        raw,
        lineage_nodes=_nodes(),
    )

    assert "loop_instance" not in repr(normalized).lower()
    assert "effect_send_b" in _items_by_id(normalized["components"])


def test_selector_and_reducer_form_an_acyclic_conjugate_operator_chain() -> None:
    """选择器与归约器可串联且拒绝算子之间形成循环"""
    nodes = _nodes()
    program = _composed_program()
    program["components"].append(
        {
            "id": SELECTOR,
            "kind": "selector",
            "title": "Selector",
            "config": {
                "operator_implementation": "rules",
                "prompt": "Select.",
                "branches": [
                    {
                        "id": "branch_a",
                        "label": "A",
                        "description": "A branch",
                        "match": {
                            "mode": "all",
                            "conditions": [],
                            "order_by": "input",
                            "limit": None,
                        },
                    },
                    {
                        "id": "branch_b",
                        "label": "B",
                        "description": "B branch",
                        "match": {
                            "mode": "all",
                            "conditions": [],
                            "order_by": "input",
                            "limit": None,
                        },
                    },
                ],
            },
        }
    )
    for wire in program["wires"]:
        if wire["id"] in {"wire_a_reducer", "wire_b_reducer"}:
            wire["target"] = SELECTOR
    program["wires"].append(
        {'id': 'wire_selector_reducer', 'kind': 'route', 'source': SELECTOR, 'source_port': 'branch:branch_a', 'target': REDUCER, 'target_port': 'invoke', 'config': {'invoke_profiles': {'reducer': {'arguments': {'prompt': {'concat': [{'value': '已选择：'}, {'from': 'packet.message.text'}], 'separator': ''}}}}}}
    )

    normalized = lineage_program.normalize_program(
        program,
        lineage_nodes=nodes,
    )
    plan = lineage_program.compile_program(normalized, nodes)

    assert plan["packet_operators"][SELECTOR]["kind"] == "selector"
    assert plan["packet_operators"][SELECTOR][
        "incoming_packet_wire_ids"
    ] == ["wire_a_reducer", "wire_b_reducer"]
    assert plan["packet_operators"][SELECTOR][
        "outgoing_packet_wire_ids"
    ] == ["wire_selector_reducer"]
    assert plan["packet_operators"][SELECTOR][
        "outgoing_packet_ports"
    ] == {"branch:branch_a": ["wire_selector_reducer"]}
    assert plan["packet_operators"][REDUCER][
        "incoming_packet_wire_ids"
    ] == ["wire_selector_reducer"]
    selector_route = plan["invoke_routes"]["wire_selector_reducer"]
    assert "packet_filter" not in selector_route["delivery"]
    assert selector_route["invocation"]["arguments"]["prompt"] == {
        "concat": [
            {"value": "已选择："},
            {"from": "packet.message.text"},
        ],
        "separator": "",
    }

    cyclic = deepcopy(normalized)
    cyclic["wires"].append(
        {'id': 'wire_reducer_selector_cycle', 'kind': 'route', 'source': REDUCER, 'source_port': 'output', 'target': SELECTOR, 'target_port': 'invoke'}
    )
    with pytest.raises(ValueError, match="不能形成循环"):
        lineage_program.normalize_program(cyclic, lineage_nodes=nodes)


def test_equal_programs_share_one_compiled_plan_until_their_content_changes(monkeypatch) -> None:
    """内容相同的程序只编译一次并共用计划，成员运行状态变化不算内容变化；内容一变就重新编译，已有计划不随输入改动"""
    monkeypatch.setattr(lineage_program, "_COMPILED_PLANS", OrderedDict())
    compiles = []
    compile_plan = lineage_program._compile_program
    monkeypatch.setattr(
        lineage_program, "_compile_program", lambda *args: compiles.append(1) or compile_plan(*args),
    )
    nodes = _nodes()
    program = _composed_program()

    plan = lineage_program.compile_program(program, nodes)
    unchanged = deepcopy(plan)
    assert lineage_program.compile_program(deepcopy(program), deepcopy(nodes)) is plan
    # A member's run changes its runtime fields, not the plan.
    running = [{**node, "status": "running", "last_execution": {"message_id": "msg_1"}} for node in nodes]
    assert lineage_program.compile_program(program, running) is plan
    assert len(compiles) == 1

    next(item for item in program["components"] if item["id"] == REDUCER)["title"] = "Renamed"
    nodes[0]["title"] = "renamed"
    renamed = lineage_program.compile_program(program, nodes)
    assert len(compiles) == 2
    assert renamed["packet_operators"][REDUCER]["title"] == "Renamed"
    assert plan == unchanged


def test_target_can_bind_any_reference_but_instruction_checks_resource_kind() -> None:
    """目标连线可绑定任意引用并由指令检查资源类型"""
    program = lineage_program.normalize_program(
        {
            "components": [
                {
                    "id": "event_source_activation",
                    "kind": "event_source",
                    "config": {
                        "event_type": "manual.triggered",
                    },
                },
                {
                    "id": "event_source_reference",
                    "kind": "event_source",
                    "config": {
                        "event_type": "manual.triggered",
                    },
                },
                {
                    "id": "effect_send_reference",
                    "kind": "effect",
                    "config": effect_config("DeliverMessage", {"resident_id": {"from": "endpoint.target"}}, ["target"]),
                },
            ],
            "wires": [
                {'id': 'wire_effect_activation', 'kind': 'route', 'source': 'event_source_activation', 'target': 'effect_send_reference'},
                {
                    "id": "wire_effect_reference",
                    "kind": "target",
                    "source": "effect_send_reference",
                    "target": "event_source_reference",
                },
            ],
        },
        lineage_nodes=[],
    )
    reference_wire = _items_by_id(program["wires"])[
        "wire_effect_reference"
    ]
    assert "target_port" not in reference_wire

    # The endpoint may hold any reference; DeliverMessage checks at run time
    # that it is a Resident.
    assert lineage_program.compile_program(program, [])["effects"]["effect_send_reference"]["endpoints"] == {
        "target": "event_source_reference",
    }
