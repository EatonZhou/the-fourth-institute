from __future__ import annotations

import subprocess

import pytest


def test_provider_route_contract_reloads_every_direct_consumer():
    """ai_provider_registry 变更时重启全部组件"""
    from scripts.portal_dev_source_reloader import ALL_COMPONENTS, components_for_paths

    assert components_for_paths(["management_portal/ai_provider_registry.py"]) == ALL_COMPONENTS

from scripts import portal_dev_source_reloader


@pytest.mark.parametrize('path', [
    'management_portal/workbench_projections/native.py',
    'management_portal/workbench_projections/service.py',
    'management_portal/lineage_workflows.py',
    'management_portal/semantic_search_surfaces.py',
    'management_portal/paper_projection.py',
    'management_portal/wall_workflows.py',
    'management_portal/test_framework/workflows.py',
    'management_portal/test_framework/resource_index.py',
    # Package-root modules that ResourceService itself imports.
    'management_portal/resource_index_backend.py',
    'management_portal/resource_auto_registration.py',
])
def test_projection_contract_updates_reload_web_and_resource(path):
    """投影契约模块变更时重启 web 和 resource 组件，按依赖附带重启 lineage"""
    expected = {'web', 'resource'}
    if path not in {'management_portal/test_framework/resource_index.py', 'management_portal/resource_auto_registration.py'}:
        expected.add('lineage')
    assert portal_dev_source_reloader.components_for_paths({path}) == expected


def test_source_reloader_routes_domain_changes_to_required_components_contracts(subtests, isolated_portal_case):
    """源码变更按模块依赖路由到需重启的组件，运维脚本和测试源码不触发重启"""
    with subtests.test(contract='test_source_reloader_routes_domain_changes_to_required_components'), isolated_portal_case() as case_path:
        # ResourceService runs SocietyService in its own process.
        assert portal_dev_source_reloader.components_for_paths({'management_portal/society_service/server.py'}) == {'lineage', 'resource'}
        assert portal_dev_source_reloader.components_for_paths({'management_portal/execution/worker.py'}) == {'execution-service', 'execution-worker'}
        assert portal_dev_source_reloader.components_for_paths({'management_portal/execution/event_projection.py'}) == {'web', 'execution-service', 'execution-worker', 'lineage', 'resource'}
        assert portal_dev_source_reloader.components_for_paths({'management_portal/static/portal_nav.js'}) == {'web'}
        # Web reads Codex accounts through the account adapters; ExecutionService
        # reads the Provider declarations, whose Adapters import the client.
        assert portal_dev_source_reloader.components_for_paths({'management_portal/codex_app_server_client.py'}) == portal_dev_source_reloader.ALL_COMPONENTS
        assert portal_dev_source_reloader.components_for_paths({'management_portal/execution/harness_inline_media.py'}) == {'web', 'execution-service', 'execution-worker', 'lineage', 'resource'}
        assert portal_dev_source_reloader.components_for_paths({'management_portal/execution/harness_read_dedup.py'}) == {'web', 'execution-service', 'execution-worker', 'lineage', 'resource'}
        assert portal_dev_source_reloader.components_for_paths({'management_portal/test_framework/page/test_workbench.js'}) == {'web'}
        # The Test Catalog is read from the test sources whenever they change.
        assert portal_dev_source_reloader.components_for_paths({'tests/cases/case_ids.json', 'tests/cases/test_example.py'}) == set()
        # The background services import management_common too (database access,
        # identities, errors); only a few modules are Web's alone.
        assert portal_dev_source_reloader.components_for_paths({'management_common/portal_database.py'}) == portal_dev_source_reloader.ALL_COMPONENTS
        assert portal_dev_source_reloader.components_for_paths({'management_common/operation_errors.py'}) == portal_dev_source_reloader.ALL_COMPONENTS
        assert portal_dev_source_reloader.components_for_paths({'management_common/knowledge_metadata.py'}) == {'web', 'resource', 'lineage'}
    with subtests.test(contract='test_source_reloader_does_not_restart_for_operational_script_edits'), isolated_portal_case() as case_path:
        assert portal_dev_source_reloader.components_for_paths({'scripts/codex_weekly_quota_usage.py'}) == set()
        # Web's restart and publish instructions import these scripts.
        assert portal_dev_source_reloader.components_for_paths({'scripts/portal_services.py'}) == {'web'}
    with subtests.test(contract='test_changed_paths_detects_add_modify_and_delete'), isolated_portal_case() as case_path:
        before = (('kept.py', 1, 2), ('modified.py', 1, 2), ('deleted.py', 1, 2))
        after = (('kept.py', 1, 2), ('modified.py', 2, 2), ('added.py', 1, 2))
        assert portal_dev_source_reloader.changed_paths(before, after) == {'modified.py', 'deleted.py', 'added.py'}
    with subtests.test(contract='test_component_source_states_roll_execution_package_as_one_unit'), isolated_portal_case() as case_path:
        states = portal_dev_source_reloader.component_source_states((('management_portal/society_service/server.py', 1, 10), ('management_portal/execution/worker.py', 2, 20), ('management_portal/static/portal_nav.js', 3, 30)))
        assert states['lineage'] == (('management_portal/society_service/server.py', 1, 10),)
        assert states['execution-worker'] == (('management_portal/execution/worker.py', 2, 20),)
        assert states['execution-service'] == (('management_portal/execution/worker.py', 2, 20),)
        assert states['web'] == (('management_portal/static/portal_nav.js', 3, 30),)
    with subtests.test(contract='test_execution_service_source_change_also_rolls_worker'), isolated_portal_case() as case_path:
        assert portal_dev_source_reloader.components_for_paths({'management_portal/execution/service.py'}) == portal_dev_source_reloader.ALL_COMPONENTS
    with subtests.test(contract='shared_permission_change_keeps_every_consumer_pending'), isolated_portal_case() as case_path:
        for path in (
            'management_portal/permission_system/__init__.py',
            'management_portal/permission_system/par.py',
            'management_portal/portal_access_control.py',
            'management_common/principal.py',
            'management_common/execution_context_file.py',
            'management_portal/hatg/kernel.py',
        ):
            baseline = portal_dev_source_reloader.component_source_states(((path, 1, 10),))
            current = portal_dev_source_reloader.component_source_states(((path, 2, 20),))
            assert portal_dev_source_reloader.changed_components(baseline, current) == portal_dev_source_reloader.ALL_COMPONENTS
            partially_deployed = {**baseline, 'web': current['web']}
            assert portal_dev_source_reloader.changed_components(partially_deployed, current) == portal_dev_source_reloader.ALL_COMPONENTS - {'web'}
    with subtests.test(contract='test_restart_signal_wakes_coordinator_without_request_polling'), isolated_portal_case() as case_path:
        portal_dev_source_reloader._WAKE_EVENT.clear()
        portal_dev_source_reloader._wake_for_restart(0, None)
        assert portal_dev_source_reloader._WAKE_EVENT.is_set()
        portal_dev_source_reloader._WAKE_EVENT.clear()


def test_accepting_one_component_preserves_other_pending_changes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只接受一个组件的源码状态时，其他组件的待重启变更保留"""
    baseline = portal_dev_source_reloader.component_source_states(
        (("management_portal/society_service/server.py", 1, 10),)
    )
    current = portal_dev_source_reloader.component_source_states(
        (
            ("management_portal/society_service/server.py", 2, 10),
            ("management_portal/execution/worker.py", 2, 20),
        )
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "DEPLOYED_SOURCE_STATE",
        tmp_path / "deployed.json",
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "write_deployed_source_states",
        lambda states: None,
    )

    accepted = portal_dev_source_reloader.accept_components(
        baseline,
        current,
        {"lineage"},
    )

    assert portal_dev_source_reloader.changed_components(
        accepted,
        current,
    ) == {"execution-service", "execution-worker", "resource"}


def test_a_file_a_service_loads_at_start_restarts_that_service():
    """服务启动时加载的文件变化后，这个服务随 Dev 源码重启

    Dev 源码重载按每个后台服务启动时导入的仓库文件决定重启范围：agent_data_store 变化时重启 SocietyService 和 ResourceService（它在自己进程内运行 SocietyService），只在函数内导入的模块不计入。

    :importance: P1
    """
    # SocietyService (and ResourceService, which runs it in-process) import
    # agent_data_store at start; no path rule names it for either.
    assert portal_dev_source_reloader.components_for_paths(
        {"management_portal/execution/agent_data_store.py"}
    ) == {"web", "execution-service", "execution-worker", "lineage", "resource"}
    assert portal_dev_source_reloader.components_for_paths(
        {"management_portal/resource_refs.py"}
    ) == {"web", "resource", "lineage"}
    imports = portal_dev_source_reloader.service_imports()
    assert "management_portal/society_service/server.py" in imports["resource"]
    assert "management_portal/execution/agent_data_store.py" in imports["lineage"]
    # A module imported only inside a function is not loaded at start.
    assert "management_portal/society_service/server.py" not in imports["execution-service"]


def test_a_module_a_provider_declaration_names_restarts_its_readers():
    """Provider 声明按路径引用的模块变更时，重启所有读取声明的服务"""
    # The OpenCode declaration names its catalog and credential paths by
    # string; every service reads the declarations, and a new route must reach
    # all of them.
    for path in (
        "management_portal/opencode_provider_catalog.py",
        "management_portal/opencode_client.py",
    ):
        assert portal_dev_source_reloader.components_for_paths({path}) == {
            "web", "execution-service", "execution-worker", "lineage", "resource",
        }
    imports = portal_dev_source_reloader.service_imports()
    # The Adapter's name ends in its class; the module is what a service loads.
    assert "management_portal/opencode_backend.py" in imports["execution-worker"]


def test_a_module_a_feature_declaration_names_restarts_its_readers():
    """功能声明引用的接入点模块变更时，重启读取这个接入点的服务

    :module: platform.core
    :design: decision_15aab18c0ab3
    """
    imports = portal_dev_source_reloader.service_imports()
    # ResourceService builds its index from the features' index Adapters.
    assert "management_portal/paper_resource_index.py" in imports["resource"]
    assert "management_portal/article_resource_index.py" in imports["resource"]
    # Only Web serves the features' routes; SocietyService does not load them.
    assert "management_portal/http_paper.py" in imports["web"]
    assert "management_portal/http_paper.py" not in imports["lineage"]
    assert "management_portal/paper_resource_index.py" not in imports["lineage"]
    # A hook no reader rule names (Mailbox sources) reaches every reader of the declarations.
    assert "management_portal/notice_board_backend.py" in imports["lineage"]
    assert portal_dev_source_reloader.components_for_paths(
        {"management_portal/http_paper.py"}) == {"web"}
    # Instruction declarations join every service's catalog, like hatg/catalog;
    # their Web handlers load in Web alone and keep the Web shell out of the others.
    assert portal_dev_source_reloader.components_for_paths(
        {"management_portal/chatroom_instructions.py"}) == portal_dev_source_reloader.ALL_COMPONENTS
    assert portal_dev_source_reloader.components_for_paths(
        {"management_portal/chatroom_web_isa.py"}) == {"web"}
    assert "management_portal/serve_management_portal.py" not in imports["lineage"]


def test_a_module_web_loads_while_it_runs_restarts_web():
    """Web 运行中加载的 Lineage 模块变更时重启 Web，Web 不导入 SocietyService 服务端"""
    # Web imports the state store at start and the conversation projection on
    # the first Chat read receipt; both belong to society_service.
    for path in (
        "management_portal/society_service/state_store.py",
        "management_portal/society_service/resident_conversation_projection_domain.py",
    ):
        assert "web" in portal_dev_source_reloader.components_for_paths({path})
    imports = portal_dev_source_reloader.service_imports()
    # Web reaches SocietyService over its socket, never the in-process server.
    assert "management_portal/society_service/server.py" not in imports["web"]
    assert "management_portal/execution/worker.py" not in imports["web"]


def test_deployed_source_states_round_trip(tmp_path) -> None:
    """已部署的组件源码状态写出后能原样读回"""
    path = tmp_path / "deployed.json"
    current = portal_dev_source_reloader.component_source_states(
        (("management_portal/execution/worker.py", 2, 20),)
    )

    portal_dev_source_reloader.write_deployed_source_states(current, path)

    assert portal_dev_source_reloader.read_deployed_source_states(
        {}, path
    ) == current


def test_source_reloader_failure_and_explicit_repair_contracts(monkeypatch, subtests, isolated_portal_case):
    """重启失败后同一源码状态不再自动重试，显式请求可修复过期组件，默认每小时扫描"""
    with subtests.test(contract='test_source_reloader_circuit_breaks_failed_hourly_source_state'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            baseline_source = (('management_portal/app.py', 1, 1),)
            changed = (('management_portal/app.py', 2, 1),)
            baseline = portal_dev_source_reloader.component_source_states(baseline_source)
            states = iter([changed, changed])
            restarts: list[set[str]] = []
            monkeypatch.setattr(portal_dev_source_reloader, 'source_state', lambda: next(states))
            monkeypatch.setattr(portal_dev_source_reloader, 'restart_dev', lambda components: restarts.append(set(components)) or portal_dev_source_reloader.RESTART_FAILED)
            accepted, failed = portal_dev_source_reloader.scan_source_fallback(baseline, None)
            accepted, failed = portal_dev_source_reloader.scan_source_fallback(accepted, failed)
            assert restarts == [{'web'}]
            assert accepted == baseline
            assert failed == changed
    with subtests.test(contract='test_source_reloader_explicit_request_repairs_unchanged_stale_component'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            write_deployed = portal_dev_source_reloader.write_deployed_source_states
            monkeypatch.setattr(portal_dev_source_reloader, 'write_deployed_source_states', lambda states: write_deployed(states, path=case_path / 'deployed-component-source-state.json'))
            source = (('management_portal/static/portal_nav.js', 2, 1),)
            accepted = portal_dev_source_reloader.component_source_states(source)
            acknowledged: list[tuple[str, int]] = []
            restarts: list[set[str]] = []
            monkeypatch.setattr(portal_dev_source_reloader, 'read_restart_request', lambda: {'request_id': 'request-coalesced', 'revision': 2, 'components': ['web']})
            monkeypatch.setattr(portal_dev_source_reloader, 'source_state', lambda: source)
            monkeypatch.setattr(portal_dev_source_reloader, 'restart_dev', lambda components: restarts.append(set(components)) or portal_dev_source_reloader.RESTART_OK)
            monkeypatch.setattr(portal_dev_source_reloader, 'acknowledge_restart_request', lambda request_id, revision: acknowledged.append((request_id, revision)) or True)
            result = portal_dev_source_reloader.process_restart_request(accepted, '')
            assert result == (accepted, '', portal_dev_source_reloader.RESTART_OK)
            assert restarts == [{'web'}]
            assert acknowledged == [('request-coalesced', 2)]
    with subtests.test(contract='test_source_reloader_defaults_to_hourly_fallback_scan'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            calls: list[float] = []
            monkeypatch.setattr(portal_dev_source_reloader, 'watch', lambda **kwargs: calls.append(kwargs['source_scan_interval_seconds']))
            assert portal_dev_source_reloader.main([]) == 0
            assert calls == [3600.0]


def test_source_reloader_processes_explicit_request_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式重启请求只处理一次，重启后接受相应组件的源码状态并确认"""
    baseline = portal_dev_source_reloader.component_source_states(
        (
            ("management_portal/app.py", 1, 1),
            ("management_portal/society_service/server.py", 1, 1),
        )
    )
    current = (
        ("management_portal/app.py", 2, 1),
        ("management_portal/society_service/server.py", 2, 1),
    )
    acknowledged: list[tuple[str, int]] = []
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "read_restart_request",
        lambda: {
            "request_id": "request-1",
            "revision": 1,
            "components": ["lineage", "web"],
        },
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "source_state",
        lambda: current,
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "restart_dev",
        lambda components: (
            portal_dev_source_reloader.RESTART_OK
            if components == {"lineage", "web"}
            else portal_dev_source_reloader.RESTART_FAILED
        ),
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "acknowledge_restart_request",
        lambda request_id, revision: acknowledged.append(
            (request_id, revision)
        ) or True,
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "write_deployed_source_states",
        lambda states: None,
    )

    accepted, failed_request_id, outcome = (
        portal_dev_source_reloader.process_restart_request(baseline, "")
    )

    current_states = portal_dev_source_reloader.component_source_states(
        current
    )
    assert accepted["lineage"] == current_states["lineage"]
    assert accepted["web"] == current_states["web"]
    assert failed_request_id == ""
    assert outcome == portal_dev_source_reloader.RESTART_OK
    assert acknowledged == [("request-1", 1)]


def test_source_reloader_automatic_request_restarts_only_changed_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自动重启请求只重启源码有变化的组件"""
    baseline = portal_dev_source_reloader.component_source_states(
        (
            ("management_portal/society_service/server.py", 1, 1),
            ("management_portal/execution/worker.py", 1, 1),
        )
    )
    current = (
        ("management_portal/society_service/server.py", 2, 1),
        ("management_portal/execution/worker.py", 1, 1),
    )
    restarted: list[set[str]] = []
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "read_restart_request",
        lambda: {
            "request_id": "request-auto",
            "revision": 1,
            "components": [],
        },
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "source_state",
        lambda: current,
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "restart_dev",
        lambda components: (
            restarted.append(set(components))
            or portal_dev_source_reloader.RESTART_OK
        ),
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "acknowledge_restart_request",
        lambda _request_id, _revision: True,
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "write_deployed_source_states",
        lambda _states: None,
    )

    accepted, failed_request_id, outcome = (
        portal_dev_source_reloader.process_restart_request(baseline, "")
    )

    assert restarted == [{"lineage", "resource"}]
    assert failed_request_id == ""
    assert outcome == portal_dev_source_reloader.RESTART_OK
    assert portal_dev_source_reloader.changed_components(
        accepted,
        portal_dev_source_reloader.component_source_states(current),
    ) == set()


def test_source_reloader_preserves_new_source_change_submitted_during_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重启期间提交的新源码变更会触发再次重启，并确认最新修订号"""
    baseline_source = (("management_portal/static/portal_nav.js", 1, 1),)
    first_source = (("management_portal/static/portal_nav.js", 2, 1),)
    second_source = (("management_portal/static/portal_nav.js", 3, 1),)
    accepted = portal_dev_source_reloader.component_source_states(baseline_source)
    request = {
        "request_id": "request-racing",
        "revision": 1,
        "components": ["web"],
    }
    current_source = first_source
    restarts: list[set[str]] = []
    acknowledged: list[tuple[str, int]] = []

    def restart(components: set[str]) -> str:
        nonlocal current_source
        restarts.append(set(components))
        if len(restarts) == 1:
            request["revision"] = 2
            current_source = second_source
        return portal_dev_source_reloader.RESTART_OK

    def acknowledge(request_id: str, revision: int) -> bool:
        acknowledged.append((request_id, revision))
        return revision == request["revision"]

    monkeypatch.setattr(
        portal_dev_source_reloader,
        "read_restart_request",
        lambda: dict(request),
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "source_state",
        lambda: current_source,
    )
    monkeypatch.setattr(portal_dev_source_reloader, "restart_dev", restart)
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "acknowledge_restart_request",
        acknowledge,
    )
    monkeypatch.setattr(
        portal_dev_source_reloader,
        "write_deployed_source_states",
        lambda _states: None,
    )

    accepted, failed, outcome = portal_dev_source_reloader.process_restart_request(
        accepted, ""
    )
    accepted, failed, outcome = portal_dev_source_reloader.process_restart_request(
        accepted, failed
    )

    assert restarts == [{"web"}, {"web"}]
    assert acknowledged == [
        ("request-racing", 1),
        ("request-racing", 2),
    ]
    assert failed == ""
    assert outcome == portal_dev_source_reloader.RESTART_OK
    assert portal_dev_source_reloader.changed_components(
        accepted,
        portal_dev_source_reloader.component_source_states(second_source),
    ) == set()


@pytest.fixture(autouse=True)
def isolated_deployed_state(monkeypatch, tmp_path):
    monkeypatch.setattr(portal_dev_source_reloader, 'DEPLOYED_SOURCE_STATE', tmp_path / 'deployed.json')
