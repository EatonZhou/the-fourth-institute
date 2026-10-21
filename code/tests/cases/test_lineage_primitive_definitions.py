from __future__ import annotations

import json
from pathlib import Path

import pytest

from management_portal.society_service import (
    lineage_primitive_definitions,
    lineage_program,
)


def test_canonical_definition_file_is_the_complete_browser_manifest_source_contracts(subtests, isolated_portal_case):
    """统一组件定义提供完整浏览器清单并约束配置字段"""
    with subtests.test(contract='test_canonical_definition_file_is_the_complete_browser_manifest_source'), isolated_portal_case() as case_path:
        path = (
            Path(__file__).parents[2]
            / "management_portal"
            / "static"
            / "lineage_primitive_definitions.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))

        assert payload["schema"] == lineage_primitive_definitions.SCHEMA
        assert lineage_primitive_definitions.manifest_fragment() == {
            key: payload[key]
            for key in (
                "default_types",
                "event_type_categories",
                "event_common_fields",
                "event_types",
                "component_types",
            )
        }
        assert set(payload["event_types"]) == lineage_program.EVENT_SOURCE_TYPES
        assert (
            lineage_primitive_definitions.default_type("event")
            == "resident.work_finished"
        )
    with subtests.test(contract='test_watch_subject_restrictions_come_from_the_same_contract'), isolated_portal_case() as case_path:
        assert lineage_primitive_definitions.event_types_watching("wire") == {
            "event.delivery_failed",
            "event.delivery_expired",
        }
        assert lineage_program.EVENT_SOURCE_EFFECT_OBSERVER_TYPES == (
            set(lineage_primitive_definitions.event_types_watching("effect"))
        )
    with subtests.test(contract='test_definition_accessors_do_not_leak_mutable_canonical_state'), isolated_portal_case() as case_path:
        definitions = lineage_primitive_definitions.type_definitions("event")
        definitions["manual.triggered"]["title"] = "mutated"

        assert (
            lineage_primitive_definitions.type_definition(
                "event",
                "manual.triggered",
            )["title"]
            == "用户手动触发"
        )
    with subtests.test(contract='test_authoritative_config_rejects_fields_owned_by_another_selected_type'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="manual.triggered config 不接受字段"):
            lineage_program.normalize_program(
                {
                    "components": [{
                        "id": "event_source_manual",
                        "kind": "event_source",
                        "config": {
                            "event_type": "manual.triggered",
                            "path": "not-owned-here.md",
                        },
                    }],
                    "wires": [],
                },
            lineage_nodes=[],
        )

        with pytest.raises(ValueError, match="Effect config 只包含 script、endpoints"):
            lineage_program.normalize_program(
                {
                    "components": [{
                        "id": "effect_message",
                        "kind": "effect",
                        "config": {
                            "instruction": "DeliverMessage",
                            "fork_mode": "full",
                        },
                    }],
                    "wires": [],
                },
                lineage_nodes=[],
            )


def test_each_type_owns_an_exact_non_overlapping_authoring_shape(subtests, isolated_portal_case):
    """各事件类型的编写字段完整且路径不重复"""
    for kind in ["event"]:
        with subtests.test(kind=kind), isolated_portal_case() as case_path:
            common_fields = lineage_primitive_definitions.common_fields(kind)
            for type_id in lineage_primitive_definitions.type_definitions(kind):
                definition = lineage_primitive_definitions.type_definition(
                    kind,
                    type_id,
                )
                omitted = {
                    str(item)
                    for item in definition.get("omit_common_fields") or []
                }
                expected_common_paths = {
                    field["path"]
                    for field in common_fields
                    if (
                        str(field.get("name") or "")
                        or str(field["path"]).removeprefix("config.")
                    ) not in omitted
                }
                omitted_common_paths = {
                    field["path"]
                    for field in common_fields
                    if (
                        str(field.get("name") or "")
                        or str(field["path"]).removeprefix("config.")
                    ) in omitted
                }
                fields = lineage_primitive_definitions.fields_for(kind, type_id)
                paths = [field["path"] for field in fields]
                assert len(paths) == len(set(paths))
                assert expected_common_paths <= set(paths)
                assert omitted_common_paths.isdisjoint(paths)
                assert lineage_primitive_definitions.authoring_config_fields(
                    kind,
                    type_id,
                ) == {
                    "event_type",
                    *(
                        path.removeprefix("config.")
                        for path in paths
                        if path.startswith("config.")
                    ),
                }


def test_authoritative_config_rejects_inactive_conditional_fields(subtests, isolated_portal_case):
    """事件配置拒绝当前调度模式未启用的字段"""
    for config, inactive_field in [
        (
            {
                "event_type": "time.reached",
                "schedule_mode": "at",
                "scheduled_at": "2035-04-05T01:30:00Z",
                "delay_seconds": 60,
            },
            "delay_seconds",
        ),
        (
            {
                "event_type": "time.reached",
                "schedule_mode": "countdown",
                "delay_seconds": 60,
                "scheduled_at": "2035-04-05T01:30:00Z",
            },
            "scheduled_at",
        ),
    ]:
        with subtests.test(config=config, inactive_field=inactive_field), isolated_portal_case() as case_path:
            with pytest.raises(
                ValueError,
                match=rf"time\.reached config 不接受字段：{inactive_field}",
            ):
                lineage_program.normalize_program(
                    {
                        "components": [{
                            "id": "event_source_clock",
                            "kind": "event_source",
                            "config": config,
                        }],
                        "wires": [],
                    },
                    lineage_nodes=[],
                )


def test_authoritative_config_keeps_only_the_active_conditional_shape(subtests, isolated_portal_case):
    """事件配置只保留当前调度模式启用的字段"""
    for config, present, absent in [
        (
            {
                "event_type": "time.reached",
                "schedule_mode": "at",
                "scheduled_at": "2035-04-05T01:30:00Z",
            },
            "scheduled_at",
            "delay_seconds",
        ),
        (
            {
                "event_type": "time.reached",
                "schedule_mode": "countdown",
                "delay_seconds": 60,
            },
            "delay_seconds",
            "scheduled_at",
        ),
    ]:
        with subtests.test(config=config, present=present, absent=absent), isolated_portal_case() as case_path:
            program = lineage_program.normalize_program(
                {
                    "components": [{
                        "id": "event_source_clock",
                        "kind": "event_source",
                        "config": config,
                    }],
                    "wires": [],
                },
                lineage_nodes=[],
            )

            config = program["components"][0]["config"]
            assert present in config
            assert absent not in config


def test_program_rejects_components_without_canonical_config(subtests, isolated_portal_case):
    """程序拒绝未将编写字段放入 config 的组件"""
    for component in [
        {
            "id": "event_source_flat",
            "kind": "event_source",
            "event_type": "manual.triggered",
        },
        {
            "id": "effect_flat",
            "kind": "effect",
            "script": "DeliverMessage",
        },
    ]:
        with subtests.test(component=component), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match="作者配置放在 config"):
                lineage_program.normalize_program(
                    {"components": [component], "wires": []},
                    lineage_nodes=[],
                )


def test_program_rejects_authoring_fields_beside_canonical_config_contracts(subtests, isolated_portal_case):
    """程序拒绝与 config 并列的编写字段及废弃类型字段"""
    with subtests.test(contract='test_program_rejects_authoring_fields_beside_canonical_config'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="不接受字段：event_type"):
            lineage_program.normalize_program(
                {
                    "components": [{
                        "id": "event_source_mixed",
                        "kind": "event_source",
                        "event_type": "resident.work_finished",
                        "config": {"event_type": "manual.triggered"},
                    }],
                    "wires": [],
                },
                lineage_nodes=[],
            )
    with subtests.test(contract='test_program_rejects_the_retired_effect_type_field'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="不接受：effect_type"):
            lineage_program.normalize_program(
                {
                    "components": [{
                        "id": "effect_runtime_name",
                        "kind": "effect",
                        "config": {"effect_type": "resident.message"},
                    }],
                    "wires": [],
                },
                lineage_nodes=[],
            )
