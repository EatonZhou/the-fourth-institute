from __future__ import annotations


from management_portal import component_lifecycle


def test_component_lifecycle_round_trip_contracts(tmp_path, subtests, isolated_portal_case):
    """组件生命周期状态往返保留启动时间并忽略无效文件"""
    with subtests.test(contract='test_component_lifecycle_round_trip'), isolated_portal_case() as case_path:
        tmp_path = case_path
        restarting = component_lifecycle.publish_component_state(
            "dev",
            "lineage",
            "restarting",
            message="SocietyService 正在重启",
            operation_id="operation-1",
            instance_root=tmp_path,
        )

        assert restarting["state"] == "restarting"
        assert component_lifecycle.read_component_state(
            "dev",
            "lineage",
            instance_root=tmp_path,
        ) == restarting

        ready = component_lifecycle.publish_component_state(
            "dev",
            "lineage",
            "ready",
            message="SocietyService 已就绪",
            operation_id="operation-1",
            instance_root=tmp_path,
        )

        assert ready["state"] == "ready"
        assert ready["started_at"] == restarting["started_at"]
        assert component_lifecycle.read_lifecycle(
            "dev",
            instance_root=tmp_path,
        )["schema"] == component_lifecycle.SCHEMA
    with subtests.test(contract='test_component_lifecycle_ignores_invalid_file'), isolated_portal_case() as case_path:
        tmp_path = case_path
        path = component_lifecycle.lifecycle_path("dev", instance_root=tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")

        assert component_lifecycle.read_component_state(
            "dev",
            "lineage",
            instance_root=tmp_path,
        ) == {}
