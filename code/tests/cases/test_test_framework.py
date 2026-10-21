from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest
from management_portal.society_service import SocietyServiceUnavailable
from management_portal.resources.errors import ResourceServiceUnavailable

from management_portal.portal_deployment_registry import PortalDeployment
from management_portal.test_framework.catalog import ROOT, CatalogRepository
from management_portal.test_framework.catalog_source import build_catalog
from management_portal.test_framework.planner import RunPlanner
from management_portal.test_framework.records import RunCoordinator, RunRecords, TERMINAL, now_iso
from management_portal.test_framework.registry import RunnerRegistry
from management_portal.test_framework.tau import TauConfig, build_prompt, parse_result
from management_portal.test_framework.dev_target import DevBuildMismatch, DevTargetError, DevTargetVerifier, VerifiedDevTarget
from management_portal.test_framework.runner_adapters import AdapterResult, TauAdapter, _terminate_artifact_helpers
from management_portal.test_framework import slots
from management_portal.test_framework.executor import LocalRunExecutor, launch_unit
from management_portal.test_framework.local_runs import PytestGroup, plan_groups
from management_portal.test_framework.service import TestFrameworkService as FrameworkService
from management_portal.test_framework.tau_campaign import TauCampaignExecutor
from management_portal.test_framework.tau_limits import tau_max_parallel_modules
from management_portal.test_framework.tools.provision_portal_test_profile import storage_state_payload
from management_portal.test_framework.taxonomy import MODULE_GROUPS, MODULES
from management_portal.test_framework.tau_residents import TauResidentAssignments
from management_portal.test_framework.models import CatalogError, Implementation, TestCase as FrameworkTestCase


@pytest.fixture(autouse=True)
def available_tau_runtime(monkeypatch):
    """Adapter unit tests own their runtime prerequisites; they launch no browser."""
    from management_portal.test_framework import tau

    monkeypatch.setattr(tau, "executor_runtime_issue", lambda: "")
    monkeypatch.setattr(tau, "browser_runtime_issue", lambda: "")


def _tau_config(storage: Path) -> TauConfig:
    registry = storage.parent / "deployments.json"
    registry.write_text(json.dumps({
        "schema": "tfi.portal-deployments.v1",
        "instances": [{"enabled": True, "id": "dev", "kind": "dev",
                       "label": "Dev", "origin": "http://127.0.0.1:18086"}],
    }), encoding="utf-8")
    return TauConfig(storage, deployment_registry=registry)


class RecordingContext:
    """A GroupContext that keeps what a group reports."""

    def __init__(self, run_dir: Path, stop: str = ""):
        self.run_dir = run_dir
        self.results: dict[int, tuple[str, int | None, dict]] = {}
        self.started_items: list[int] = []
        self.patches: list[dict] = []
        self.text: list[str] = []
        self.stop = stop

    def output(self, text):
        self.text.append(text)

    def started(self, item_index):
        self.started_items.append(item_index)

    def result(self, item_index, status, returncode, details):
        self.results[item_index] = (status, returncode, details)

    def patch(self, fields):
        self.patches.append(fields)

    def stop_reason(self):
        return self.stop


def _local_item(entrypoint: str, runner: str = "pytest", profiles: list[str] | None = None) -> dict:
    source = entrypoint.partition("::")[0]
    return {
        "case_id": "test_" + hashlib.sha256(entrypoint.encode()).hexdigest()[:12],
        "case_title": entrypoint,
        "implementation": {"id": "impl-" + hashlib.sha256(entrypoint.encode()).hexdigest()[:8], "runner": runner,
                           "entrypoint": entrypoint, "source": source, "profiles": profiles or []},
        "runner": {"id": runner},
        "module": "operations.test-framework",
    }


def test_every_discovered_implementation_has_one_explicit_classified_case_contracts(subtests, isolated_portal_case):
    """每个测试实现归属唯一已分类用例，模块分类覆盖全部 SPEC 设计，筛选维度取交、aspects 取并

    :design: decision_8273271b67b1
    :design-point:
    """
    with subtests.test(contract='test_every_discovered_implementation_has_one_explicit_classified_case'), isolated_portal_case():
        repository = CatalogRepository()
        cases = repository.all()
        implementations = [item for case in cases for item in case.implementations]
        source_entrypoints = {item.entrypoint for item in implementations if item.runner == 'pytest'}
        assert source_entrypoints == build_catalog(ROOT).discovered
        assert all((item.entrypoint.startswith('tau:') for item in implementations if item.runner == 'tau'))
        declared_tau = json.loads((ROOT / 'tests/cases/tau_cases.json').read_text(encoding='utf-8'))['cases']
        compiled = {item.id: item for item in implementations}
        for case in declared_tau:
            for implementation in case['implementations']:
                assert compiled[implementation['id']].task_prompt == implementation['task_prompt'], case['id']
        assert all((case.title.strip() for case in cases))
        assert all((case.importance in {'P0', 'P1', 'P2', 'P3'} for case in cases))
    with subtests.test(contract='test_tau_prompts_observe_accepted_work_until_terminal_without_fixed_wall_clock_limits'), isolated_portal_case():
        declared_tau = json.loads((ROOT / 'tests/cases/tau_cases.json').read_text(encoding='utf-8'))['cases']
        prompts = [
            implementation['task_prompt']
            for case in declared_tau
            for implementation in case['implementations']
        ]
        fixed_wait = re.compile(r'(?:最多等待|等待最长|等待最多)\s*\d+\s*(?:秒|分钟)')
        for prompt in prompts:
            assert not fixed_wait.search(prompt)
            assert '总预算约' not in prompt
            assert '选择 four' not in prompt
        runner_prompt = (ROOT / 'management_portal/test_framework/config/tau_runner_prompt.md').read_text(encoding='utf-8')
        assert 'TAU 是无人值守运行' in runner_prompt
        assert '禁止向用户提问' in runner_prompt
        assert '始终以 `PORTAL_TAU_RESULT` 结束本次用例' in runner_prompt
    with subtests.test(contract='test_module_taxonomy_covers_all_spec_domains_and_every_case_uses_it'), isolated_portal_case() as case_path:
        cases = CatalogRepository().all()
        assert [group.id for group in MODULE_GROUPS] == ['spec', 'isa', 'security', 'resource', 'resident', 'lineage', 'execution', 'workflow', 'workbench', 'platform', 'operations']
        assert all((case.module in MODULES for case in cases))
        assert all((any((case.module.startswith(f'{group.id}.') for case in cases)) for group in MODULE_GROUPS))
        # The taxonomy covers the Design index; a module without current cases
        # remains visible as a coverage gap, including after a suite is retired.
        projection = FrameworkService(state_root=case_path / 'catalog').query_workspace()
        case_counts = Counter(case.module for case in cases)
        assert projection['module_counts'] == {
            module_id: case_counts[module_id] for module_id in MODULES
        }
        assert {
            module['id']
            for group in projection['filters']['modules']
            for module in group['modules']
        } == set(MODULES)
        assert all((case.module.strip() and case.aspects for case in cases))
        assert all((not case.is_design_point or case.design_refs for case in cases))
        assert all(('architecture-conformance' not in case.aspects or case.design_refs for case in cases))
    with subtests.test(contract='test_module_taxonomy_projects_every_current_spec_design'), isolated_portal_case():
        spec = (ROOT / 'SPEC.md').read_text(encoding='utf-8')
        # A release SPEC is a flat resource index; chapter layout is not a contract.
        all_indexed = set(re.findall(r"\]\(/r/(decision_[0-9a-f]+)\)", spec))
        all_projected = {ref for _group, module in MODULES.values() for ref in module.design_refs}
        assert all_indexed, "SPEC must expose its Design resources"
        assert all_indexed == all_projected, {
            "missing_from_test_taxonomy": sorted(all_indexed - all_projected),
            "removed_from_spec": sorted(all_projected - all_indexed),
        }
    with subtests.test(contract='test_live_definition_is_playable_before_it_enters_the_catalog'), isolated_portal_case():
        raw = {'id': 'test_0123456789ab', 'key': 'live.contract', 'title': 'Live 契约', 'description': '验证 Live 契约。', 'importance': 'P1', 'module': 'operations.test-workbench', 'aspects': ['ui', 'behavior'], 'is_design_point': False, 'design_refs': [], 'lifecycle': 'active', 'implementations': [], 'live': {'title': '真实页面 Live', 'target': '/wall?view=test', 'mode': 'direct', 'risk': 'read-only', 'adapter': 'test-lab', 'direct_steps': [{'capability': 'test.workbench.inspect', 'caption': '查看工作台。'}]}}
        case = FrameworkTestCase.parse(raw)
        assert case.live is not None
        assert case.live['adapter_version'] == 1
        del raw['live']['adapter']
        with pytest.raises(CatalogError, match='必须指定 adapter'):
            FrameworkTestCase.parse(raw)
    with subtests.test(contract='test_sandbox_live_requires_executable_steps'), isolated_portal_case():
        raw = {'id': 'test_abcdef012345', 'key': 'live.sandbox.contract', 'title': '沙盒 Live 契约', 'description': '验证沙盒 Live 契约。', 'importance': 'P2', 'module': 'operations.test-workbench', 'aspects': ['ui'], 'is_design_point': False, 'design_refs': [], 'lifecycle': 'active', 'implementations': [], 'live': {'target': '/test-demo/components', 'mode': 'sandbox', 'steps': []}}
        with pytest.raises(CatalogError, match='必须包含可执行 steps'):
            FrameworkTestCase.parse(raw)
    with subtests.test(contract='test_catalog_combines_dimensions_with_and_and_aspects_with_or'), isolated_portal_case():
        repository = CatalogRepository()
        candidates = repository.query({'modules': ['workflow.knowledge'], 'aspects': ['ui', 'performance']})
        assert candidates
        assert all((case.module == 'workflow.knowledge' for case in candidates))
        assert all(({'ui', 'performance'}.intersection(case.aspects) for case in candidates))
        live = repository.query({'has_live': True})
        assert live
        assert all((case.live for case in live))


def test_catalog_replacement_refreshes_existing_repository_without_restart(tmp_path):
    """测试源码改动后，已有的 CatalogRepository 无需重启即读到新内容"""
    source = tmp_path / "tests/cases/test_sample.py"
    source.parent.mkdir(parents=True)
    source.write_text('def test_sample():\n    """示例测试\n\n    原说明。\n    """\n', encoding="utf-8")
    repository = CatalogRepository(root=tmp_path)
    before, errors = repository.projection()
    assert not errors
    source.write_text('def test_sample():\n    """示例测试\n\n    改写后的说明无需重启即可见。\n    """\n', encoding="utf-8")
    after, errors = repository.projection()
    assert not errors
    assert after[0].description == "改写后的说明无需重启即可见。"
    assert before[0].description != after[0].description
    assert repository.get(after[0].id).description == after[0].description


def test_catalog_search_matches_full_implementation_entrypoint(monkeypatch, subtests):
    """测试目录支持完整实现入口检索

    完整 pytest 入口、函数名与用例 ID 均可检索，并继续遵守模块交集筛选。

    :module: operations.test-workbench
    :importance: P1
    """
    from dataclasses import replace

    repository = CatalogRepository()
    original = next(case for case in repository.all() if any(
        implementation.entrypoint.endswith("::test_pytest_process_removes_its_database_temp_root_contracts")
        for implementation in case.implementations
    ))
    candidate = replace(original, title="Fixture", description="Fixture description", key="fixture.search")
    monkeypatch.setattr(repository, "projection", lambda: ((candidate,), []))
    entrypoint = candidate.implementations[0].entrypoint
    for search in (entrypoint, entrypoint.rsplit("::", 1)[-1].upper(), candidate.id):
        with subtests.test(search=search):
            assert repository.query({"search": search}) == (candidate,)
            assert repository.query({"search": search, "modules": ["workflow.paper"]}) == ()
    assert repository.query({"search": "unrelated nonexistent entrypoint"}) == ()


def test_pytest_process_removes_its_database_temp_root_contracts(tmp_path, subtests, isolated_portal_case):
    """pytest 进程删除自己的数据库临时目录，损坏的运行历史不被清空，TAU Resident 分配在轮换前不变

    :risk: no-external-side-effects"""
    with subtests.test(contract='test_pytest_process_removes_its_database_temp_root'), isolated_portal_case() as case_path:
        tmp_path = case_path
        isolated_keys = (
            'PORTAL_SITE_CONFIG',
            'PORTAL_CODEX_APP_SERVER_THREAD_LOCK_ROOT',
            'PORTAL_LINEAGE_ROOT',
            'PORTAL_TWO_FACTOR_KEY_PATH', 'PORTAL_EXECUTION_STATE_ROOT',
            'PORTAL_ARTICLE_BUILD_ROOT',
        )
        isolated_roots = {Path(os.environ[key]).parent for key in isolated_keys}
        assert len(isolated_roots) == 1
        process_root = isolated_roots.pop()
        assert process_root.is_dir() and process_root.name.startswith('tfi-portal-pytest-')
        temp_root = tmp_path / 'process-temp'
        temp_root.mkdir()
        inherited_site = tmp_path / 'deployment-site.json'
        deployment_database = tmp_path / 'deployment-postgres.env'
        deployment_database.write_text('PORTAL_DATABASE_URL=postgresql://fixture@127.0.0.1:65431/deployed\n')
        native_root = tmp_path / 'installed-native-programs'
        inherited_site.write_text(json.dumps({'deployment': {'postgres_env_file': str(deployment_database),
            'providers': {'runtime_root': str(native_root)}, 'compatibility': {
            'domain_values': {'test_framework.actor': 'deployment-only-actor'},
        }}}), encoding='utf-8')
        inherited_before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (inherited_site, deployment_database)
        }
        environment = {**os.environ, 'TMPDIR': str(temp_root), 'PYTHONDONTWRITEBYTECODE': '1',
                       'PORTAL_SITE_CONFIG': str(inherited_site)}
        environment.pop('PORTAL_POSTGRES_ENV_FILE', None)
        rejected = subprocess.run(
            [sys.executable, '-c', 'import tests.cases.conftest'], cwd=ROOT,
            env={**environment, 'PORTAL_POSTGRES_TEST_DSN': 'postgresql://fixture@localhost:65431/other'},
            text=True, capture_output=True, check=False,
        )
        assert rejected.returncode != 0
        assert 'points at the Portal database server' in rejected.stderr
        assert not list(temp_root.glob('tfi-portal-pytest-*'))
        bootstrap = '''
import json, os, sys
from pathlib import Path
import pytest
class CheckIsolatedSite:
    def pytest_sessionstart(self, session):
        from tests.cases import postgres_test_database
        from management_portal.test_framework.compatibility import test_actor
        from management_portal.execution import provider_updates
        from management_common.portal_paths import path as portal_path
        assert postgres_test_database.PRODUCTION_ENVIRONMENT_FILE == Path(os.environ['TEST_EXPECTED_DATABASE_CONFIG'])
        assert provider_updates.RUNTIMES == portal_path('staging_root') / 'provider-runtimes'
        site = Path(os.environ['PORTAL_SITE_CONFIG'])
        assert site.parent.name.startswith('tfi-portal-pytest-')
        config = json.loads(site.read_text())
        assert set(config) == {'deployment'}
        assert set(config['deployment']) == {'postgres'}
        assert set(config['deployment']['postgres']) == {'bin', 'lib'}
        assert Path(config['deployment']['postgres']['bin']).is_dir()
        assert test_actor() == 'test-actor'
sys.exit(pytest.main(['-q', 'tests/cases/test_permission_engine.py::test_default_scheme_places_objects_by_owner'], plugins=[CheckIsolatedSite()]))
'''
        completed = subprocess.run([sys.executable, '-c', bootstrap], cwd=ROOT,
                                   env={**environment, 'TEST_EXPECTED_DATABASE_CONFIG': str(deployment_database)},
                                   text=True, capture_output=True, check=False)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        for path, before in inherited_before.items():
            assert (path.read_bytes(), path.stat().st_mtime_ns) == before
        assert not list(temp_root.glob('tfi-portal-pytest-*'))
    with subtests.test(contract='test_service_projects_canonical_catalog_query_as_ids'), isolated_portal_case() as case_path:
        tmp_path = case_path
        service = FrameworkService(state_root=tmp_path)
        result = service.query_case_ids({'modules': ['workflow.knowledge'], 'importance': ['P1'], 'aspects': ['functional']})
        assert result['schema'] == 'tfi.test-catalog-query.v1'
        assert result['count'] == len(result['case_ids'])
        assert result['case_ids']
        canonical = service.catalog.query({'modules': ['workflow.knowledge'], 'importance': ['P1'], 'aspects': ['functional']})
        assert result['case_ids'] == [case.id for case in canonical]
    with subtests.test(contract='test_run_records_fail_closed_instead_of_erasing_corrupt_history'), isolated_portal_case() as case_path:
        tmp_path = case_path
        path = tmp_path / 'runs.json'
        path.write_text('{broken', encoding='utf-8')
        with pytest.raises(RuntimeError, match='无法读取'):
            RunRecords(path).recent()
        assert path.read_text(encoding='utf-8') == '{broken'
    with subtests.test(contract='test_tau_cleanup_terminates_only_helpers_owned_by_case_directory'), isolated_portal_case() as case_path:
        tmp_path = case_path
        case_dir = tmp_path / 'run-artifacts' / 'run-a' / 'module-a' / 'test_000000000005'
        case_dir.mkdir(parents=True)
        helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', str(case_dir / 'helper.py')], start_new_session=True)
        try:
            assert helper.pid in _terminate_artifact_helpers(case_dir)
            assert helper.wait(timeout=5) != 0
            storage = tmp_path / 'storage.json'
            storage.write_text('{}', encoding='utf-8')
            storage.chmod(0o600)
            receipt = TauAdapter(_tau_config(storage), lambda **_kwargs: {}).cleanup(
                run_dir=case_dir,
                emit=lambda _chunk: None,
            )
            assert receipt['complete'] is True
            assert receipt['remaining_pids'] == []
            assert json.loads((case_dir / 'framework-helper-cleanup.json').read_text(encoding='utf-8'))['complete'] is True
        finally:
            if helper.poll() is None:
                helper.terminate()
                helper.wait(timeout=5)
    with subtests.test(contract='test_tau_resident_assignment_is_stable_until_user_rotates_it'), isolated_portal_case() as case_path:
        tmp_path = case_path
        assignments = TauResidentAssignments(tmp_path / 'tau-residents.json')
        first = assignments.assignment('operations.test-workbench')
        assert assignments.assignment('operations.test-workbench') == first
        rotated = assignments.rotate('operations.test-workbench', changed_by='alice', backend_route_id='four')
        assert rotated['slot_id'] != first['slot_id']
        assert assignments.scopes(first)[1] == assignments.scopes(rotated)[1]
        assert rotated['backend_route_id'] == 'four'
        assignments.record_identity('operations.test-workbench', resident_id='resident-test', lineage_id='lineage-test')
        assert TauResidentAssignments(assignments.path).assignment('operations.test-workbench')['backend_route_id'] == 'four'
        released = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr('management_portal.resident_handoff.release_resident', lambda **kw: released.append(kw))
            assert assignments.rotate('operations.test-workbench', changed_by='alice')['backend_route_id'] == ''
        assert released == [{'actor': 'alice', 'resident_id': 'resident-test', 'lineage_id': 'lineage-test'}]






def test_pytest_group_projects_streamed_results_and_process_outcomes(tmp_path: Path, monkeypatch) -> None:
    """pytest 分片把逐条事件映射回计划中的实现

    参数化用例取最差结果，失败在分片运行中即时上报；跳过记为前提未满足，收集错误、信号终止、中止和失败即停都有明确摘要。

    :importance: P1
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/test-fixture")
    selected = [
        _local_item("tests/cases/test_x.py::test_pass"),
        _local_item("tests/cases/test_x.py::test_param"),
        _local_item("tests/cases/test_x.py::TestKind::test_skip"),
        _local_item("tests/cases/test_x.py::test_vanished"),
        _local_item("tests/cases/test_y.py::test_any"),
        _local_item("tests/cases/test_x.py::test_expected_failure"),
    ]
    items = list(enumerate(selected))
    group_dir = tmp_path / "pytest-1"
    group_dir.mkdir()
    group = PytestGroup({"group_id": "pytest-1", "lane": "isolated", "item_indexes": list(range(6))}, selected, root=tmp_path)
    command, environment = group.command(items, group_dir)
    assert "management_portal.test_framework.pytest_results" in command and "--continue-on-collection-errors" in command
    basetemp = Path(next(value.split("=", 1)[1] for value in command if value.startswith("--basetemp=")))
    assert basetemp.parent == Path("/tmp")
    assert re.fullmatch(r"tfi-pytest-[0-9a-f]{32}", basetemp.name)
    assert basetemp != tmp_path and basetemp not in tmp_path.parents
    repeated_command, _ = group.command(items, group_dir)
    repeated_basetemp = Path(next(value.split("=", 1)[1] for value in repeated_command if value.startswith("--basetemp=")))
    assert repeated_basetemp != basetemp
    assert repeated_basetemp.parent == basetemp.parent
    assert re.fullmatch(r"tfi-pytest-[0-9a-f]{32}", repeated_basetemp.name)
    live_group = PytestGroup({"group_id": "pytest-live", "lane": "live"}, selected, root=tmp_path)
    live_command, live_environment = live_group.command(items, group_dir)
    assert not any(value.startswith("--basetemp=") for value in live_command)
    assert live_environment["XDG_RUNTIME_DIR"] == "/run/user/test-fixture"
    assert "XDG_RUNTIME_DIR" not in environment
    assert (group_dir / "pytest-args.txt").read_text().split() == [item["implementation"]["entrypoint"] for item in selected]
    assert "PORTAL_DATABASE_URL" not in environment
    context = RecordingContext(tmp_path)
    events = [
        {"event": "start", "nodeid": "tests/cases/test_x.py::test_pass"},
        {"event": "report", "nodeid": "tests/cases/test_x.py::test_pass", "when": "call", "outcome": "passed"},
        {"event": "report", "nodeid": "tests/cases/test_x.py::test_param[a]", "when": "call", "outcome": "passed"},
        {"event": "report", "nodeid": "tests/cases/test_x.py::test_param[b]", "when": "call", "outcome": "failed",
         "text": "def test_param(value):\n>       assert value == 'a'\nE       AssertionError: boom"},
        {"event": "report", "nodeid": "tests/cases/test_x.py::TestKind::test_skip", "when": "setup", "outcome": "skipped",
         "text": "Skipped: needs a GPU"},
        {"event": "collect-error", "nodeid": "tests/cases/test_y.py", "text": "E   ImportError: no module named nowhere"},
        {"event": "report", "nodeid": "tests/cases/test_x.py::test_expected_failure", "when": "call", "outcome": "skipped",
         "xfail": True, "text": "reason"},
    ]
    for event in events:
        group.handle_event(event, context, group_dir)
    # A failure is reported while the shard is still running.
    assert context.started_items == [0]
    assert context.results[1][0] == "failed"
    assert context.results[1][2]["summary"] == "pytest 失败：AssertionError: boom"
    group.finish(items, context, group_dir, 1, "")
    statuses = {index: result[0] for index, result in context.results.items()}
    assert statuses == {0: "passed", 1: "failed", 2: "unsupported", 3: "error", 4: "failed", 5: "passed"}
    assert context.results[2][2]["summary"] == "pytest 跳过：Skipped: needs a GPU"
    assert context.results[3][2]["summary"] == "pytest 结束时没有报告本项结果（退出码 1）。"
    assert context.results[4][2]["summary"] == "pytest 无法收集测试文件：ImportError: no module named nowhere"
    for returncode, stop, expected_status, expected_summary in [
        (-9, "", "error", "pytest 执行进程被 SIGKILL（9）终止，本项没有结果。"),
        (-999, "", "error", "pytest 执行进程被 未知信号（999）终止，本项没有结果。"),
        (-15, "cancelled", "cancelled", "运行已中止，本项没有执行完成。"),
        (-15, "fail-fast", "cancelled", "失败即停：前面的实现未通过，本项没有执行完成。"),
    ]:
        fresh = PytestGroup({"group_id": "pytest-1", "item_indexes": [0]}, selected, root=tmp_path)
        fresh.command(items[:1], group_dir)
        context = RecordingContext(tmp_path)
        fresh.finish(items[:1], context, group_dir, returncode, stop)
        assert context.results[0][:2] == (expected_status, returncode)
        assert context.results[0][2]["summary"] == expected_summary


def test_sandboxed_pytest_shard_reports_results_and_isolation_holes(tmp_path: Path) -> None:
    """沙箱中的 pytest 分片断网并报告写入真实状态目录的隔离漏洞

    分片在 bwrap 中运行，没有网络；真实状态目录被空目录替换，测试写入的文件留在替换目录并作为隔离漏洞报告。

    :importance: P1
    """
    probe = subprocess.run(["bwrap", "--bind", "/", "/", "--unshare-net", "--", "true"], capture_output=True, check=False)
    if probe.returncode:
        pytest.skip("bwrap cannot create a sandbox here")
    root = tmp_path / "repo"
    (root / "management_portal" / "test_framework").mkdir(parents=True)
    (root / "tests" / "cases").mkdir(parents=True)
    # Only the plugin's package; the real management_portal/__init__.py activates the shared runtime.
    (root / "management_portal" / "__init__.py").write_text("", encoding="utf-8")
    for name in ("management_portal/test_framework/__init__.py", "management_portal/test_framework/pytest_results.py"):
        (root / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
    protected = tmp_path / "portal-state"
    (protected / "kept").mkdir(parents=True)
    source = "tests/cases/test_sample.py"
    (root / source).write_text(
        "import pathlib, socket\n"
        "def test_pass(monkeypatch):\n"
        "    import builtins\n"
        "    def forbidden(*args, **kwargs):\n        raise AssertionError('case forbids file I/O')\n"
        "    monkeypatch.setattr(builtins, 'open', forbidden)\n"
        "def test_fail():\n    assert 1 == 2, 'boom'\n"
        # The real directory content is hidden; the write goes to a scratch copy.
        f"def test_writes_protected_state():\n    assert not pathlib.Path({str(protected / 'kept')!r}).exists()\n"
        f"    pathlib.Path({str(protected / 'leak.txt')!r}).write_text('x')\n"
        "def test_has_no_network():\n"
        "    probe = socket.socket()\n    probe.settimeout(0.5)\n"
        "    try:\n        probe.connect(('1.1.1.1', 443))\n    except OSError:\n        return\n"
        "    raise AssertionError('network reachable')\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256((root / source).read_bytes()).hexdigest()
    selected = [{**_local_item(f"{source}::{name}"), "source_sha256": digest}
                for name in ("test_pass", "test_fail", "test_writes_protected_state", "test_has_no_network")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    group = PytestGroup({"group_id": "pytest-1", "lane": "isolated", "slot": "pytest", "item_indexes": [0, 1, 2, 3]},
                        selected, root=root, state_roots=[protected])
    context = RecordingContext(run_dir)
    facts = group.run(context)
    statuses = {index: result[0] for index, result in context.results.items()}
    assert statuses == {0: "passed", 1: "failed", 2: "passed", 3: "passed"}, context.text
    assert "boom" in context.results[1][2]["summary"]
    # The write landed in a scratch copy, not in the real directory, and is reported.
    assert not (protected / "leak.txt").exists() and (protected / "kept").is_dir()
    assert facts["isolation_holes"] == [str(protected / "leak.txt")]
    assert "被隔离的真实状态目录" in facts["error"]
    assert not (run_dir / "pytest-1" / "pytest-temp").exists()


def test_public_case_metadata_is_the_cases_own_text_contracts(monkeypatch, subtests, isolated_portal_case):
    """用例公开的标题和描述取自用例本身不套模板，TAU 并行上限不超过 32"""
    with subtests.test(contract='test_public_case_metadata_is_the_cases_own_text'), isolated_portal_case():
        # No template stands in for a title or description that is not Chinese.
        case = FrameworkTestCase(id='test_000000000001', key='tests/cases/example.py::test_expected_behavior', title='Expected behavior remains available', description='', importance='P1', module='operations.test-workbench', aspects=('functional', 'ui'), is_design_point=False, design_refs=(), lifecycle='active', implementations=())
        public = case.public()
        assert (public['key'], public['title'], public['description']) == (case.key, case.title, '')
        assert case.workspace_summary()['title'] == case.title
    with subtests.test(contract='test_tau_parallel_limit_is_bounded'), isolated_portal_case():
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setenv('PORTAL_TEST_TAU_MAX_PARALLEL_MODULES', '999')
            assert tau_max_parallel_modules() == 32
            monkeypatch.setenv('PORTAL_TEST_TAU_MAX_PARALLEL_MODULES', 'invalid')
            assert tau_max_parallel_modules() == 8


def test_runner_filter_is_an_implementation_constraint_not_a_case_dimension_contracts(subtests, isolated_portal_case):
    """runner 筛选只约束实现，本地运行排除 TAU 用例，TAU 结果须有机器可读结论和 UI 截图证据"""
    # Planner consumers use the catalog while sources are being edited. The
    # dedicated discovery contract above owns exact source/catalog parity.
    with subtests.test(contract='a_local_run_leaves_tau_to_its_campaign'), isolated_portal_case():
        repository = CatalogRepository(verify_sources=False)
        planner = RunPlanner(repository, RunnerRegistry(tau_available=True))
        tau_case = next(case for case in repository.all() if {item.runner for item in case.implementations} == {'tau'})
        local_case = next(case for case in repository.all() if {item.runner for item in case.implementations} == {'pytest'})
        plan = planner.preview({'select': {'case_ids': [tau_case.id, local_case.id]}})
        assert {item['runner']['id'] for item in plan['selected']} == {'pytest'}
        assert {'case_id': tau_case.id, 'title': tau_case.title, 'reason': 'tau-campaign-only'} in plan['excluded']
        assert plan['execution']['mode'] == 'batch' and plan['campaign'] == ''
        assert [group['group_id'] for group in plan['execution']['groups']] == ['pytest-1']
        assert plan['execution']['groups'][0]['continue_on_failure'] is True
        fail_fast = planner.preview({'select': {'case_ids': [local_case.id]}, 'implementation': {'fail_fast': True}})
        assert fail_fast['execution']['groups'][0]['continue_on_failure'] is False
        with pytest.raises(CatalogError, match='fail_fast'):
            planner.preview({'select': {'case_ids': [local_case.id]}, 'implementation': {'fail_fast': 'yes'}})
        with pytest.raises(CatalogError, match='fail_fast'):
            planner.preview({'select': {'case_ids': [tau_case.id]}, 'implementation': {'fail_fast': True}})
    with subtests.test(contract='test_profile_is_optional_and_incompatible_profile_is_explained'), isolated_portal_case():
        repository = CatalogRepository(verify_sources=False)
        planner = RunPlanner(repository, RunnerRegistry())
        local_case = next((case for case in repository.all() if any((not item.profiles for item in case.implementations))))
        plan = planner.preview({'select': {'case_ids': [local_case.id]}, 'implementation': {'profile': 'live.controlled'}})
        assert plan['executable'] is False
        assert plan['excluded'] == [{'case_id': local_case.id, 'title': local_case.title, 'reason': 'no-compatible-implementation'}]
    with subtests.test(contract='test_agent_use_result_requires_an_explicit_machine_readable_verdict'), isolated_portal_case():
        parsed = parse_result('操作完成。\nPORTAL_TAU_RESULT: {"status":"passed","summary":"可用","issues":[],"evidence":["shot.png"]}')
        assert parsed == {'status': 'passed', 'summary': '可用', 'issues': [], 'evidence': ['shot.png'], 'visual_review': {'status': 'not-applicable', 'summary': ''}, 'user_experience': {}, 'diagnosis': {}}
        structured = parse_result('PORTAL_TAU_RESULT: {"status":"failed","summary":"failed","issues":[{"code":"BOUNDARY","title":"页面不可用","evidence":["shot.png"]}],"evidence":["shot.png"]}')
        assert structured['issues'] == ['页面不可用']
        assert structured['issue_details'] == [{'code': 'BOUNDARY', 'title': '页面不可用', 'evidence': ['shot.png']}]
    with subtests.test(contract='test_ui_agent_use_result_requires_evidence_and_visual_verdict'), isolated_portal_case():
        message = 'PORTAL_TAU_RESULT: {"status":"passed","summary":"可用","issues":[],"evidence":["shot.png"],"visual_review":{"status":"failed","summary":"截图中面板相互遮挡"}}'
        parsed = parse_result(message, require_visual=True)
        assert parsed['status'] == 'failed'
        assert parsed['visual_review']['summary'] == '截图中面板相互遮挡'
        try:
            parse_result('PORTAL_TAU_RESULT: {"status":"passed","summary":"可用","issues":[],"evidence":[]}', require_visual=True)
        except ValueError as exc:
            assert '截图证据' in str(exc)
        else:
            raise AssertionError('UI 的 AI 真实使用不能在缺少截图证据时通过')
    with subtests.test(contract='test_portal_test_profile_uses_the_selected_instance_and_contains_no_password'), isolated_portal_case():
        deployment = PortalDeployment(instance_id='dev', label='Dev', kind='dev', origin='http://127.0.0.1:18086', order=1)
        payload = storage_state_payload(deployment, 'signed-token')
        assert payload['cookies'][0]['value'] == 'signed-token'
        assert payload['origins'][0]['origin'] == deployment.origin
        assert 'password' not in json.dumps(payload).casefold()
        release = PortalDeployment('release', 'Release', 'release', 'http://127.0.0.1:18085', 2)
        assert storage_state_payload(release, 'signed-token')['origins'][0]['origin'] == release.origin


def test_selected_implementation_identity_is_fixed_in_the_run_plan() -> None:
    """运行计划固定所选实现，并校验 TAU 并行数、Resident 账号和运行设置参数"""
    from management_portal.execution.account_registrations import create_registration

    create_registration({"account_id": "codex_registered_extra", "provider": "codex",
                         "kind": "subscription_login", "route_id": "registered_extra",
                         "max_concurrency": 2, "config": {}}, "test")
    implementations = tuple(
        Implementation(
            id=f"impl-{index}", runner="pytest", entrypoint=f"synthetic::{index}",
            source="tests/cases/test_test_framework.py",  profiles=(),
            risk="no-external-side-effects" if index == 2 else "unspecified",
        )
        for index in (1, 2)
    )
    case = FrameworkTestCase(
        id="test_000000000002", key="multi", title="Multiple implementations",
        description="A Test Case can select one implementation.", importance="P1",
        module="operations.test-framework", aspects=("functional",), is_design_point=False,
        design_refs=(), lifecycle="active", implementations=implementations,
    )
    selected = case.implementations[-1]

    seen_selections = []

    class Repository:
        @staticmethod
        def query(selection):
            seen_selections.append(selection)
            return (case,)

    plan = RunPlanner(Repository(), RunnerRegistry(tau_available=True)).preview({
        "select": {"case_ids": [case.id]},
        "implementation": {"implementation_ids": [selected.id]},
    })

    assert [item["implementation"]["id"] for item in plan["selected"]] == [selected.id]
    assert plan["selected"][0]["risk"] == "no-external-side-effects"
    assert plan["selected"][0]["profile_risk"] == ""
    assert "lifecycle" not in seen_selections[-1]

    with pytest.raises(CatalogError, match="risk 必须是"):
        Implementation.parse(
            {
                "id": "impl-invalid-risk",
                "runner": "pytest",
                "entrypoint": "synthetic::invalid-risk",
                "source": "tests/cases/test_test_framework.py",
                "risk": "looks-safe",
            },
            case.id,
        )

    broad_plan = RunPlanner(Repository(), RunnerRegistry(tau_available=True)).preview({
        "select": {},
        "implementation": {"implementation_ids": [selected.id]},
    })

    assert broad_plan["request"]["select"]["lifecycle"] == [
        "active",
        "needs-review",
        "obsolete",
    ]
    assert seen_selections[-1] == broad_plan["request"]["select"]

    from dataclasses import replace

    case = replace(case, implementations=(replace(selected, runner="tau"),))
    planner = RunPlanner(Repository(), RunnerRegistry(tau_available=True))
    request = {"implementation": {"runners": ["tau"], "resident_account_id": "registered_extra", "max_parallel_modules": 12}}
    configured = planner.preview(request)
    assert configured["execution"]["max_parallel_modules"] == 12
    assert configured["selected"][0]["resident_account_id"] == "registered_extra"
    assert configured["request"]["implementation"] == request["implementation"]
    assert planner.preview({"implementation": {"runners": ["tau"]}})["plan_id"] != configured["plan_id"]
    for invalid in (True, 0, 33, "12", 2.5):
        with pytest.raises(CatalogError, match="max_parallel_modules"):
            planner.preview({"implementation": {"runners": ["tau"], "max_parallel_modules": invalid}})
    for valid in (16, 32):
        preview = planner.preview({"implementation": {"runners": ["tau"], "max_parallel_modules": valid}})
        assert preview["execution"]["max_parallel_modules"] == valid
    for invalid in (False, "", 3):
        with pytest.raises(CatalogError, match="resident_account_id"):
            planner.preview({"implementation": {"runners": ["tau"], "resident_account_id": invalid}})
    with pytest.raises((KeyError, ValueError)):
        planner.preview({"implementation": {"runners": ["tau"], "resident_account_id": "not-registered"}})
    runtime = {"model": "gpt-5.6-sol", "reasoning_effort": "medium", "permission_preset": "full-access"}
    pinned = planner.preview({"implementation": {"runners": ["tau"], "resident_account_id": "three", "resident_runtime": runtime}})
    assert pinned["selected"][0]["resident_runtime"] == runtime
    assert pinned["plan_id"] != planner.preview({"implementation": {"runners": ["tau"], "resident_account_id": "three"}})["plan_id"]
    for invalid in ({}, {"sandbox": "x"}, {"model": ""}, {"permission_preset": "root"}, "gpt-5.6-sol"):
        with pytest.raises(CatalogError, match="resident_runtime"):
            planner.preview({"implementation": {"runners": ["tau"], "resident_account_id": "three", "resident_runtime": invalid}})
    with pytest.raises(CatalogError, match="resident_account_id"):
        planner.preview({"implementation": {"runners": ["tau"], "resident_runtime": {"model": "gpt-5.6-sol"}}})
    case = replace(case, implementations=(selected,))
    with pytest.raises(CatalogError, match="仅用于 TAU"):
        planner.preview({"implementation": {"max_parallel_modules": 12}})


def test_run_coordinator_executes_a_fixed_plan_and_records_result(
    tmp_path: Path,
) -> None:
    """运行协调器按固定计划只执行一次并记录结果，只有发起者能取消，最近运行对已认证用户可见"""
    source = "tests/cases/test_test_framework.py"
    digest = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
    plan = {
        "schema": "tfi.test-run-plan.v1",
        "plan_id": "plan-test",
        "executable": True,
        "selected": [
            {
                "case_id": "test_000000000003",
                "case_title": "fixed plan",
                "implementation": {
                    "id": "impl-test",
                    "runner": "pytest",
                    "entrypoint": f"{source}::test_run_coordinator_executes_a_fixed_plan_and_records_result",
                    "source": source,
                    "line": 1,
                    "profiles": [],
                },
                "runner": {"id": "pytest"},
                "profile": "",
                "source_sha256": digest,
            }
        ],
    }
    plan["execution"] = {"mode": "batch", "groups": plan_groups(plan["selected"])}
    launched: list[str] = []
    coordinator = RunCoordinator(RunRecords(tmp_path / "runs.json"), tmp_path / "artifacts", launch=launched.append)

    class FixedRunner(PytestGroup):
        def run(self, ctx):
            for item_index in self.group["item_indexes"]:
                ctx.started(item_index)
                ctx.output("fixed runner output\n")
                ctx.result(item_index, "passed", 0, {"summary": "pytest 通过。"})
            return {}

    # Evidence of a Run whose record left the index goes when the next Run starts.
    orphan = tmp_path / "artifacts" / "testrun_0123456789abcdef"
    (orphan / "module-x").mkdir(parents=True)
    (orphan / "module-x" / "page.png").write_bytes(b"png")
    unrelated = tmp_path / "artifacts" / "notes"
    unrelated.mkdir()
    created = coordinator.start(plan, "alice")
    assert not orphan.exists() and unrelated.exists()
    # Web only queues a local run; the executor process owns its execution.
    assert launched == [created["run_id"]]
    assert coordinator.get(created["run_id"], "alice")["status"] == "queued"
    executor = LocalRunExecutor(RunRecords(tmp_path / "runs.json"), tmp_path / "artifacts",
                                runner_factory=FixedRunner, flush_seconds=0.02)
    assert executor.run(created["run_id"]) == "passed"
    assert executor.run(created["run_id"]) == ""  # a run is claimed once
    record = coordinator.get(created["run_id"], "alice")

    assert record["status"] == "passed"
    assert record["executor"]["pid"] == os.getpid()
    assert record["groups"][0]["label"] == "pytest 1/1"
    assert "fixed runner output" in record["output"]
    assert record["results"][0]["implementation_id"] == "impl-test"
    assert coordinator.records.recent()[0]["run_id"] == created["run_id"]
    assert coordinator.get(created["run_id"], "bob")["created_by"] == "alice"
    with pytest.raises(PermissionError, match="需要已认证用户"):
        coordinator.get(created["run_id"], "")
    with pytest.raises(PermissionError, match="只有运行发起者"):
        coordinator.cancel(created["run_id"], "bob")

    service = FrameworkService(state_root=tmp_path / "service")
    shared_run = service.records.create({"executable": True, "selected": []}, "test-actor")
    assert service.query({}, actor="sample_admin")["recent_runs"][0]["run_id"] == shared_run["run_id"]
    assert service.query({}, actor="")["recent_runs"] == []
    workspace = service.query_workspace({}, actor="sample_admin")
    assert workspace["schema"] == "tfi.test-workspace-catalog.v1"
    assert workspace["recent_runs"][0] == {
        "run_id": shared_run["run_id"],
        "status": "queued",
        "created_by": "test-actor",
        "created_at": shared_run["created_at"],
        "started_at": "",
        "finished_at": "",
        "plan": {"campaign": "", "selected": []},
    }
    assert workspace["cases"]
    assert workspace["cases"][0]["detail_loaded"] is False
    assert "implementations" not in workspace["cases"][0]
    assert "module_path" not in workspace["cases"][0]
    expected_runners = sorted({
        implementation.runner
        for implementation in service.catalog.get(workspace["cases"][0]["id"]).implementations
    })
    assert workspace["cases"][0]["runners"] == expected_runners


def test_run_coordinator_observes_cancellation_persisted_by_another_process(
    tmp_path: Path,
) -> None:
    """执行进程从运行记录读到其他进程写入的取消请求并中止运行"""
    source = "tests/cases/test_test_framework.py"
    digest = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
    plan = {
        "schema": "tfi.test-run-plan.v1",
        "plan_id": "plan-persisted-cancel",
        "executable": True,
        "selected": [{
            "case_id": "test_000000000004",
            "case_title": "persisted cancellation",
            "implementation": {
                "id": "impl-persisted-cancel",
                "runner": "pytest",
                "entrypoint": (
                    f"{source}::"
                    "test_run_coordinator_observes_cancellation_persisted_by_another_process"
                ),
                "source": source,
                "line": 1,
                "profiles": [],
            },
            "runner": {"id": "pytest"},
            "profile": "",
            "source_sha256": digest,
        }],
    }
    plan["execution"] = {"mode": "batch", "groups": plan_groups(plan["selected"])}
    records_path = tmp_path / "runs.json"
    coordinator = RunCoordinator(RunRecords(records_path), tmp_path / "artifacts", launch=lambda _run_id: None)
    started = threading.Event()
    cancellation_seen = threading.Event()

    class WaitingRunner(PytestGroup):
        def run(self, ctx):
            started.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if ctx.stop_reason() == "cancelled":
                    cancellation_seen.set()
                    self.report_rest(ctx, [(0, None)], "cancelled", -15, {"summary": "运行已中止，本项没有执行完成。"})
                    return {}
                time.sleep(0.01)
            ctx.result(0, "failed", 1, {})
            return {}

    created = coordinator.start(plan, "alice")
    executor = LocalRunExecutor(RunRecords(records_path), tmp_path / "artifacts",
                                runner_factory=WaitingRunner, flush_seconds=0.02)
    outcome: list[str] = []
    thread = threading.Thread(target=lambda: outcome.append(executor.run(created["run_id"])))
    thread.start()
    assert started.wait(timeout=2)

    # The Web process persists cancellation through its own RunRecords; the
    # executor process sees it in the record, not in shared memory.
    assert coordinator.cancel(created["run_id"], "alice")["status"] == "cancelling"

    assert cancellation_seen.wait(timeout=2)
    thread.join(timeout=5)
    assert outcome == ["cancelled"]
    assert coordinator.get(created["run_id"], "alice")["status"] == "cancelled"


@pytest.mark.parametrize("change_all", [False, True])
def test_queued_group_revalidates_sources_before_launch(tmp_path: Path, monkeypatch, change_all: bool) -> None:
    """排队的 pytest 组启动前重新校验源码，已变化的用例记为错误且不运行"""
    from types import SimpleNamespace

    selected = []
    for name in ("test_a.py", "test_b.py"):
        path = tmp_path / name
        path.write_text("def test_sample():\n    assert True\n")
        selected.append({**_local_item(f"{name}::test_sample"),
                         "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    released = []

    def acquire(*args, **kwargs):
        # Simulate another editor changing a file while this group awaits a slot.
        for item in selected if change_all else selected[:1]:
            (tmp_path / item["implementation"]["source"]).write_text("def test_sample():\n    assert False\n")
        return SimpleNamespace(count=1, release=lambda: released.append(True))

    monkeypatch.setattr(slots, "acquire", acquire)
    launched = []
    runner = PytestGroup({"group_id": "pytest-1", "item_indexes": [0, 1]}, selected, root=tmp_path)
    monkeypatch.setattr(runner, "_run_process", lambda items, ctx, count: launched.extend(i for i, _ in items) or {})
    context = RecordingContext(tmp_path / "run")
    assert runner.run(context) == {}
    assert launched == ([] if change_all else [1])
    assert set(context.results) == ({0, 1} if change_all else {0})
    assert all(result[0] == "error" and "源码已变化" in result[2]["summary"]
               for result in context.results.values())
    assert released == [True]


def test_local_run_fail_fast_stops_the_other_groups(tmp_path: Path) -> None:
    """失败即停时一个分组失败会停止其他分组

    计划设置 fail_fast 时，任一实现未通过后执行器停止其他分组，未完成的实现记为已中止。
    """
    selected = [_local_item("tests/cases/test_a.py::test_fails"), _local_item("tests/cases/test_b.py::test_slow")]
    plan = {"executable": True, "selected": selected,
            "execution": {"mode": "batch", "groups": plan_groups(selected, continue_on_failure=False)}}
    assert [group["continue_on_failure"] for group in plan["execution"]["groups"]] == [False, False]
    records = RunRecords(tmp_path / "runs.json")
    run_id = records.create(plan, "alice")["run_id"]

    class Runner(PytestGroup):
        def run(self, ctx):
            item_index = self.group["item_indexes"][0]
            if item_index == 0:
                ctx.result(item_index, "failed", 1, {"summary": "pytest 失败：boom"})
                return {}
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and ctx.stop_reason() != "fail-fast":
                time.sleep(0.01)
            ctx.result(item_index, "cancelled", -15, {"summary": "失败即停：前面的实现未通过，本项没有执行完成。"})
            return {}

    assert LocalRunExecutor(records, tmp_path / "artifacts", runner_factory=Runner, flush_seconds=0.02).run(run_id) == "failed"
    record = records.get(run_id)
    assert {result["item_index"]: result["status"] for result in record["results"]} == {0: "failed", 1: "cancelled"}
    assert [group["status"] for group in record["groups"]] == ["failed", "cancelled"]


def test_local_plan_shards_pytest_files(tmp_path: Path) -> None:
    """本地运行计划按文件均衡切分 pytest 分片。

    整文件分配到分片并按文件大小均衡。

    :importance: P1
    """
    for name, size in (("test_big.py", 9000), ("test_mid.py", 5000), ("test_small.py", 4000)):
        path = tmp_path / "tests" / "cases" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#" * size)
    selected = [
        _local_item("tests/cases/test_small.py::test_one"),
        _local_item("tests/cases/test_big.py::test_one"),
        _local_item("tests/cases/test_mid.py::test_one"),
        _local_item("tests/cases/test_small.py::test_two"),
    ]
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(slots.CAPACITY, "pytest", 2)
        groups = plan_groups(selected, root=tmp_path)
    by_id = {group["group_id"]: group for group in groups}
    assert list(by_id) == ["pytest-1", "pytest-2"]
    # Whole files per shard, the largest file alone; cases of a file stay together.
    assert by_id["pytest-1"]["item_indexes"] == [1]
    assert by_id["pytest-2"]["item_indexes"] == [2, 0, 3]
    assert all(group["continue_on_failure"] for group in groups)

    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("PORTAL_TEST_MAX_CONCURRENCY", raising=False)
        budget_selected = []
        for index in range(6):
            source = f"tests/cases/test_sample_{index}.py"
            path = tmp_path / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def test_one(): pass\n")
            budget_selected.append(_local_item(f"{source}::test_one"))
        assert len(plan_groups(budget_selected, root=tmp_path)) == 4


def test_host_slots_bound_concurrency_across_executors(tmp_path: Path, monkeypatch) -> None:
    """主机并发名额限制所有测试执行器的进程数

    名额是文件锁，持有进程退出后自动释放；没有空闲名额时执行器等待或在中止后放弃。

    :importance: P1
    """
    monkeypatch.setenv("PORTAL_TEST_SLOT_ROOT", str(tmp_path / "slots"))
    monkeypatch.setitem(slots.CAPACITY, "browser", 3)
    first = slots.acquire("browser", 2, stop=lambda: False)
    second = slots.acquire("browser", 5, stop=lambda: False)
    assert (first.count, second.count) == (2, 1)
    assert slots.acquire("browser", 1, stop=lambda: True, poll_seconds=0.01) is None
    first.release()
    third = slots.acquire("browser", 3, stop=lambda: False)
    assert third.count == 2
    second.release()
    third.release()
    # A lock held by another process counts until that process exits.
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; from management_portal.test_framework import slots; "
        "lease = slots.acquire('live', 1, stop=lambda: False); print('held', flush=True); time.sleep(30)"
    )], cwd=ROOT, stdout=subprocess.PIPE, text=True, env={**os.environ, "PORTAL_TEST_SLOT_ROOT": str(tmp_path / "slots")})
    try:
        assert holder.stdout.readline().strip() == "held"
        assert slots.acquire("live", 1, stop=lambda: True, poll_seconds=0.01) is None
    finally:
        holder.kill()
        holder.wait(timeout=5)
    lease = slots.acquire("live", 1, stop=lambda: False)
    assert lease.count == 1
    lease.release()

    monkeypatch.setenv("PORTAL_TEST_SLOT_ROOT", str(tmp_path / "global-slots"))
    monkeypatch.delenv("PORTAL_TEST_MAX_CONCURRENCY", raising=False)
    assert slots.max_concurrency() == 4
    monkeypatch.setenv("PORTAL_TEST_MAX_CONCURRENCY", "2")
    assert slots.max_concurrency() == 2
    browser = slots.acquire("browser", 2, stop=lambda: False)
    assert browser is not None and browser.count == 2
    assert slots.acquire("pytest", 1, stop=lambda: True, poll_seconds=0.01) is None
    browser.release()
    pytest_lease = slots.acquire("pytest", 1, stop=lambda: False)
    tau_lease = slots.acquire("tau", 1, stop=lambda: False)
    assert pytest_lease is not None and tau_lease is not None
    assert slots.acquire("live", 1, stop=lambda: True, poll_seconds=0.01) is None
    pytest_lease.release()
    tau_lease.release()
    for value in ("0", "33", "invalid"):
        monkeypatch.setenv("PORTAL_TEST_MAX_CONCURRENCY", value)
        with pytest.raises(ValueError, match="PORTAL_TEST_MAX_CONCURRENCY"):
            slots.max_concurrency()

    monkeypatch.setenv("PORTAL_TEST_SLOT_ROOT", str(tmp_path / "process-global-slots"))
    monkeypatch.setenv("PORTAL_TEST_MAX_CONCURRENCY", "1")
    holder = subprocess.Popen(
        [sys.executable, "-c", (
            "import time; from management_portal.test_framework import slots; "
            "lease = slots.acquire('browser', 1, stop=lambda: False); "
            "print('held', flush=True); time.sleep(30)"
        )], cwd=ROOT, stdout=subprocess.PIPE, text=True, env=os.environ.copy(),
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        assert slots.acquire("pytest", 1, stop=lambda: True, poll_seconds=0.01) is None
    finally:
        holder.kill()
        holder.wait(timeout=5)
    lease = slots.acquire("pytest", 1, stop=lambda: False)
    assert lease is not None and lease.count == 1
    lease.release()


def test_tau_campaign_runs_modules_concurrently_and_cases_in_module_order(tmp_path: Path, monkeypatch) -> None:
    """TAU 运行并发执行各模块、模块内按顺序执行用例，报告按失败类别汇总"""
    from contextlib import nullcontext
    from management_portal.society_service import application_target_configuration

    # This executor ordering test supplies an open admission boundary. Durable
    # configuration and freeze races are covered in test_application_target_configuration.
    monkeypatch.setattr(application_target_configuration, "admission_scope", nullcontext)
    source = "tests/cases/test_test_framework.py"
    digest = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
    selected = []
    modules = ["operations.test-workbench", "spec.design-system"]
    for module in modules:
        for suffix in ("1", "2"):
            selected.append({
                "case_id": f"{module}-{suffix}", "case_title": f"{module}-{suffix}",
                "module": module, "implementation": {"id": f"impl-{module}-{suffix}", "runner": "tau", "source": source},
                "runner": {"id": "tau"}, "source_sha256": digest,
            })
    plan = {
        "schema": "tfi.test-run-plan.v2", "executable": True,
        "campaign": "tau", "selected": selected,
        "execution": {"mode": "parallel-modules", "groups": [
            {"group_id": f"module:{module}", "module_id": module, "runner": "tau", "item_indexes": [index for index, item in enumerate(selected) if item["module"] == module], "continue_on_failure": True}
            for module in modules
        ]},
    }
    launched: list[str] = []
    coordinator = RunCoordinator(RunRecords(tmp_path / "runs.json"), tmp_path / "artifacts", launch=launched.append)
    barrier = threading.Barrier(2)
    observed = {module: [] for module in modules}

    class ConcurrentAdapter:
        def execute(self, **kwargs):
            module = kwargs["planned"]["module"]
            observed[module].append(kwargs["planned"]["case_id"])
            if kwargs["planned"]["case_id"].endswith("-1"):
                barrier.wait(timeout=2)
            # A failure keeps its module going; the framework classifies what it can.
            return {
                "operations.test-workbench-2": AdapterResult(status="unsupported", details={"summary": "未发送用例"}),
                "spec.design-system-2": AdapterResult(status="failed", returncode=1),
            }.get(kwargs["planned"]["case_id"], AdapterResult(status="passed"))

    created = coordinator.start(plan, "alice")
    # Web only queues the Campaign; its executor unit drives it against DEV.
    assert launched == [created["run_id"]]
    assert coordinator.get(created["run_id"], "alice")["status"] == "queued"
    executor = TauCampaignExecutor(RunRecords(tmp_path / "runs.json"), tmp_path / "artifacts", ConcurrentAdapter())
    assert executor.run(created["run_id"]) == "unsupported"
    assert executor.run(created["run_id"]) == ""  # a Campaign is claimed once
    record = coordinator.get(created["run_id"], "alice")

    assert record["status"] == "unsupported"
    assert all(values == [f"{module}-1", f"{module}-2"] for module, values in observed.items())
    assert {group["status"] for group in record["groups"]} == {"unsupported", "failed"}
    assert {item["case_id"]: item.get("failure_class") for item in record["results"]} == {
        "operations.test-workbench-1": None, "operations.test-workbench-2": "precondition",
        "spec.design-system-1": None, "spec.design-system-2": "unknown",
    }
    assert record["report"]["schema"] == "tfi.portal-tau-report.v1"
    assert record["report"]["summary"] == {
        "module_count": 2, "case_count": 4, "passed": 2,
        "failed": 1, "error": 0, "cancelled": 0, "unsupported": 1,
        "failure_classes": {"precondition": 1, "unknown": 1},
    }
    assert record["report"]["execution"]["account"] == "test-actor"
    assert record["report"]["execution"]["target"] == "DEV"


def test_tau_campaign_bounds_parallel_modules(tmp_path: Path) -> None:
    """TAU 运行同时执行的模块数不超过 max_parallel_modules"""
    source = "tests/cases/test_test_framework.py"
    digest = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
    modules = ["module-a", "module-b", "module-c"]
    selected = [
        {
            "case_id": module,
            "case_title": module,
            "module": module,
            "implementation": {"id": f"impl-{module}", "runner": "tau", "source": source},
            "runner": {"id": "tau"},
            "source_sha256": digest,
        }
        for module in modules
    ]
    plan = {
        "schema": "tfi.test-run-plan.v2",
        "executable": True,
        "campaign": "tau",
        "selected": selected,
        "execution": {
            "mode": "parallel-modules",
            "max_parallel_modules": 2,
            "groups": [
                {
                    "group_id": f"module:{module}",
                    "module_id": module,
                    "runner": "tau",
                    "item_indexes": [index],
                    "continue_on_failure": True,
                }
                for index, module in enumerate(modules)
            ],
        },
    }
    records = RunRecords(tmp_path / "runs.json")
    lock = threading.Lock()
    active = 0
    peak = 0
    # The first module holds its slot until a second one runs beside it, so
    # a loaded host cannot hide the overlap; a serial executor times out.
    overlapped = threading.Event()

    class LimitedAdapter:
        def execute(self, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    overlapped.set()
            overlapped.wait(timeout=10)
            with lock:
                active -= 1
            return AdapterResult(status="passed")

    created = records.create(plan, "alice")
    assert TauCampaignExecutor(records, tmp_path / "artifacts", LimitedAdapter()).run(created["run_id"]) == "passed"
    record = records.get(created["run_id"])

    assert record["status"] == "passed"
    assert peak == 2
    assert record["report"]["execution"]["max_parallel_modules"] == 2


def test_executor_unit_gets_the_service_environment_only_for_a_campaign(tmp_path: Path, monkeypatch) -> None:
    """TAU 执行单元获得服务路由环境且命令行不含密钥

    验证 TAU Campaign 的执行单元继承 Portal 服务路由和测试设置、从 PostgreSQL 环境文件读取数据库地址、不设运行时长上限；本地运行单元不获得这些环境；数据库地址和其他密钥不出现在 systemd-run 命令行中。

    :importance: P1
    :design: decision_8273271b67b1
    """
    from management_portal.test_framework import executor as executor_module

    commands: list[list[str]] = []

    def record(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(executor_module.subprocess, "run", record)
    monkeypatch.setenv("PORTAL_DATABASE_URL", "postgresql://portal:fixture-database-secret@127.0.0.1/portal")
    monkeypatch.setenv("PORTAL_FIXTURE_TOKEN", "fixture-token-secret")
    monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_SOCKET", "/run/fixture/society-service.sock")
    monkeypatch.setenv("PORTAL_TEST_TAU_MODEL", "fixture-model")
    monkeypatch.delenv("PORTAL_ISA_ROUTING", raising=False)
    monkeypatch.setenv("PORTAL_POSTGRES_ENV_FILE", str(tmp_path / "portal-postgres.env"))
    launch_unit("testrun_0123456789abcdef", tmp_path, tau=True)
    launch_unit("testrun_fedcba9876543210", tmp_path)
    campaign, local = commands

    # Secrets never reach a command line; the unit reads the database URL itself.
    assert "fixture-database-secret" not in " ".join(campaign + local)
    assert "fixture-token-secret" not in " ".join(campaign + local)
    assert f"--property=EnvironmentFile={tmp_path / 'portal-postgres.env'}" in campaign
    assert {
        "--setenv=PORTAL_SOCIETY_SERVICE_SOCKET=/run/fixture/society-service.sock",
        "--setenv=PORTAL_ISA_ROUTING=socket",
        "--setenv=PORTAL_TEST_TAU_MODEL=fixture-model",
        "--property=RuntimeMaxSec=infinity",
        "--unit=tfi-testrun-0123456789abcdef",
    } <= set(campaign)
    assert not [part for part in local if "EnvironmentFile" in part or "SERVICE" in part or "TAU" in part]
    assert "--property=RuntimeMaxSec=28800" in local


def test_tau_cleanup_never_terminates_the_caller_chain_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """TAU 清理只结束用例目录下的辅助进程而不动调用链，预检失败保留安全的传输错误详情"""
    with subtests.test(contract='test_tau_cleanup_never_terminates_the_caller_chain'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            case_dir = tmp_path / 'test_000000000006'
            from types import SimpleNamespace
            import psutil
            from management_portal.test_framework import runner_adapters
            marker = str(case_dir.resolve())
            meta = runner_adapters.HelperProcess
            snapshot = {10: meta(1, ('shell', marker), 1.0),
                        20: meta(10, ('python', marker), 2.0),
                        30: meta(20, ('node', marker), 3.0),
                        40: meta(30, ('chromium',), 4.0)}
            monkeypatch.setitem(_terminate_artifact_helpers.__globals__, '_process_snapshot', lambda: snapshot)
            monkeypatch.setattr(os, 'getpid', lambda: 20)
            monkeypatch.setattr(os, 'getppid', lambda: 10)
            killed = []
            monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: snapshot[pid].created_at))
            monkeypatch.setattr(runner_adapters.process_runtime, 'terminate_process_tree',
                                lambda pid, **kw: killed.append(pid))
            if sys.platform == 'win32':
                with pytest.raises(RuntimeError, match='Windows Job'):
                    _terminate_artifact_helpers(case_dir)
                assert killed == []
            else:
                assert _terminate_artifact_helpers(case_dir) == [30, 40]
                assert killed == [30]  # Shared runtime owns this root's descendants.
            assert set(killed).isdisjoint({10, 20})
    with subtests.test(contract='test_agent_use_preflight_preserves_safe_transport_failure_details'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            from types import SimpleNamespace
            from urllib.error import HTTPError
            monkeypatch.setattr('management_portal.test_framework.dev_target.resolve_unique_dev', lambda _path: SimpleNamespace(instance_id='dev', origin='http://127.0.0.1:18086'))

            storage = tmp_path / 'state.json'
            storage.write_text(json.dumps({'cookies': [{'name': 'session', 'value': 'secret', 'domain': '127.0.0.1'}]}))
            responses = {
                'health': b'{"instance":"dev","build_id":"dev-current"}',
                'page': b'<body data-test-workbench-version="component-v2"></body>',
                'identity': b'{"username":"test-actor"}',
            }
            for stage in responses:
                for status in (401, 403, 502, 503, 504):
                    def unavailable(url, _headers):
                        current = 'health' if url.endswith('/api/health') else 'identity' if url.endswith('/api/me') else 'page'
                        if current == stage:
                            raise HTTPError('http://private.invalid/?token=secret', status, 'private response', {}, None)
                        if url.endswith('/development/native-views?view=test'):
                            return json.dumps({'html': responses['page'].decode()}).encode()
                        return responses[current]
                    verifier = DevTargetVerifier(http_get=unavailable, build_id=lambda _root: 'dev-current')
                    with pytest.raises(DevTargetError) as captured:
                        verifier.verify(storage)
                    assert captured.value.details[f'{stage}_error_type'] == 'HTTPError'
                    assert captured.value.details[f'{stage}_http_status'] == str(status)
                    assert captured.value.temporarily_unavailable == (status >= 500)
                    assert 'secret' not in json.dumps(captured.value.details)
                    assert 'private' not in str(captured.value)


def test_run_records_recover_runs_interrupted_by_framework_restart_contracts(tmp_path, subtests, isolated_portal_case):
    """框架重启后，执行进程已退出的运行被收尾或由新执行器接管，存活进程的运行不受影响"""
    with subtests.test(contract='test_run_records_recover_runs_interrupted_by_framework_restart'), isolated_portal_case() as case_path:
        tmp_path = case_path
        records = RunRecords(tmp_path / 'runs.json')
        created = records.create({'executable': True, 'selected': [{}]}, 'alice')
        records.update(created['run_id'], {'status': 'running', 'current_case_id': 'test_000000000007', 'executor': {**created['executor'], 'pid': 999999999}})
        assert records.recover_interrupted() == 1
        recovered = records.get(created['run_id'])
        assert recovered['status'] == 'error'
        assert recovered['current_case_id'] == ''
        assert recovered['finished_at']
        assert recovered['error'] == '测试执行进程在运行完成前退出。'
    with subtests.test(contract='a_local_run_whose_executor_exited_is_closed_when_read'), isolated_portal_case() as case_path:
        records = RunRecords(case_path / 'runs.json')
        coordinator = RunCoordinator(records, case_path / 'artifacts', launch=lambda _run_id: None)
        created = records.create({'executable': True, 'selected': [{}]}, 'alice')
        records.update(created['run_id'], {'status': 'running', 'executor': {**created['executor'], 'pid': 999999999}})
        assert coordinator.get(created['run_id'], 'alice')['status'] == 'error'
    with subtests.test(contract='an_executor_claims_only_a_queued_run'), isolated_portal_case() as case_path:
        records = RunRecords(case_path / 'runs.json')
        identity = {'pid': 4242}
        queued = records.create({'executable': True, 'selected': []}, 'alice')
        claimed = records.claim(queued['run_id'], identity)
        assert claimed['status'] == 'running' and claimed['executor'] == identity and claimed['started_at']
        assert records.claim(queued['run_id'], identity) is None
        cancelled = records.create({'executable': True, 'selected': []}, 'alice')
        records.update(cancelled['run_id'], {'status': 'cancelling'})
        assert records.claim(cancelled['run_id'], identity) is None
        assert records.get(cancelled['run_id'])['status'] == 'cancelled'
    with subtests.test(contract='test_run_records_do_not_recover_a_campaign_owned_by_a_live_process'), isolated_portal_case() as case_path:
        tmp_path = case_path
        records = RunRecords(tmp_path / 'runs.json')
        created = records.create({'executable': True, 'selected': [{}]}, 'alice')
        records.update(created['run_id'], {'status': 'running'})
        assert records.recover_interrupted() == 0
        assert records.get(created['run_id'])['status'] == 'running'
    with subtests.test(contract='a_reused_pid_does_not_keep_an_interrupted_run_alive'), isolated_portal_case() as case_path:
        records = RunRecords(case_path / 'runs.json')
        created = records.create({'executable': True, 'selected': [{}]}, 'alice')
        records.update(created['run_id'], {'status': 'running', 'executor': {**created['executor'], 'start_ticks': '-1'}})
        assert records.recover_interrupted() == 1
        assert records.get(created['run_id'])['status'] == 'error'
    with subtests.test(contract='a_pid_namespace_cannot_recover_a_live_host_run'), isolated_portal_case() as case_path:
        records = RunRecords(case_path / 'runs.json')
        local = records.create({'executable': True, 'selected': [{}]}, 'alice')
        campaign = records.create({'executable': True, 'campaign': 'tau', 'selected': [{}]}, 'alice')
        for created in (local, campaign):
            records.update(created['run_id'], {'status': 'running', 'started_at': now_iso()})
        child = subprocess.run([
            'bwrap', '--unshare-pid', '--bind', '/', '/', '--proc', '/proc',
            '--', sys.executable, '-c',
            'import json,sys; from pathlib import Path; '
            'from management_portal.test_framework.records import RunRecords; '
            'records = RunRecords(Path(sys.argv[1])); '
            'print(json.dumps({"recovered": records.recover_interrupted(), "unowned": records.unowned(), '
            '"claimed": records.claim(sys.argv[2], {"pid": 4242})}))',
            str(records.path), campaign['run_id'],
        ], cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
        assert json.loads(child.stdout) == {'recovered': 0, 'unowned': [], 'claimed': None}
        assert {records.get(created['run_id'])['status'] for created in (local, campaign)} == {'running'}
    with subtests.test(contract='an_executor_takes_over_a_campaign_whose_executor_exited'), isolated_portal_case() as case_path:
        records = RunRecords(case_path / 'runs.json')
        created = records.create({'executable': True, 'campaign': 'tau', 'selected': [{}]}, 'alice')
        dead = {**created['executor'], 'pid': 999999999}
        records.update(created['run_id'], {'status': 'running', 'started_at': now_iso(), 'executor': dead, 'recovery_pending': False})
        # Records alone never reopen a Campaign; only its next executor does.
        assert records.recover_interrupted() == 0
        identity = {'pid': 4242}
        taken = records.claim(created['run_id'], identity)
        assert taken['status'] == 'running' and taken['executor'] == identity and 'recovery_pending' not in taken
        assert records.claim(created['run_id'], {'pid': 4343}) is None
        records.update(created['run_id'], {'status': 'cancelling', 'executor': dead})
        assert records.claim(created['run_id'], identity)['status'] == 'cancelling'
    with subtests.test(contract='a_local_run_whose_executor_exited_is_closed_by_the_next_executor'), isolated_portal_case() as case_path:
        records = RunRecords(case_path / 'runs.json')
        created = records.create({'executable': True, 'selected': [{}]}, 'alice')
        records.update(created['run_id'], {'status': 'running', 'started_at': now_iso(), 'executor': {**created['executor'], 'pid': 999999999}})
        assert records.claim(created['run_id'], {'pid': 4242}) is None
        assert records.get(created['run_id'])['status'] == 'error'
    with subtests.test(contract='web_relaunches_the_executor_of_every_unowned_run'), isolated_portal_case() as case_path:
        records = RunRecords(case_path / 'runs.json')
        dead = {**records.create({'executable': True, 'selected': []}, 'alice')['executor'], 'pid': 999999999}
        campaign, stranded, local, live = (
            records.create({'executable': True, **({'campaign': 'tau'} if index in {0, 3} else {}), 'selected': [{}]}, 'alice')['run_id']
            for index in range(4)
        )
        records.update(campaign, {'status': 'running', 'started_at': now_iso(), 'executor': dead})
        records.update(stranded, {'executor': dead})  # queued; the Web that created it is gone
        records.update(local, {'status': 'running', 'started_at': now_iso(), 'executor': dead})
        records.update(live, {'status': 'running', 'started_at': now_iso()})
        launched: list[str] = []
        coordinator = RunCoordinator(records, case_path / 'artifacts', launch=launched.append)
        assert sorted(coordinator.recover()) == sorted([campaign, stranded]) == sorted(launched)
        assert records.get(local)['status'] == 'error'
        assert records.get(live)['status'] == 'running'
        # A reader retries a run's launch at most once per interval.
        assert coordinator.get(campaign, 'alice')['status'] == 'running'
        assert len(launched) == 2

        def unavailable(_run_id):
            raise RuntimeError('systemd user bus unavailable')

        assert RunCoordinator(records, case_path / 'artifacts', launch=unavailable).recover() == []
    with subtests.test(contract='test_run_records_update_only_the_target_run_file'), isolated_portal_case() as case_path:
        records = RunRecords(case_path / 'runs.json')
        first = records.create({'executable': True, 'selected': []}, 'alice')
        second = records.create({'executable': True, 'selected': []}, 'alice')
        first_path = records.records_root / f"{first['run_id']}.json"
        second_path = records.records_root / f"{second['run_id']}.json"
        second_before = second_path.read_bytes()
        records.append_output(first['run_id'], 'isolated output')
        assert 'isolated output' in first_path.read_text(encoding='utf-8')
        assert second_path.read_bytes() == second_before
        assert 'isolated output' not in records.path.read_text(encoding='utf-8')
    with subtests.test(contract='test_new_test_run_ids_use_the_canonical_testrun_format'), isolated_portal_case() as case_path:
        tmp_path = case_path
        record = RunRecords(tmp_path / 'runs.json').create({'executable': True, 'selected': []}, 'alice')
        assert re.fullmatch('testrun_[0-9a-f]{16}', record['run_id'])
    with subtests.test(contract='test_run_report_batch_deletion_is_atomic_and_rejects_active_runs'), isolated_portal_case() as case_path:
        tmp_path = case_path
        records = RunRecords(tmp_path / 'runs.json')
        completed = records.create({'executable': True, 'selected': []}, 'alice')
        active = records.create({'executable': True, 'selected': []}, 'alice')
        records.update(completed['run_id'], {'status': 'passed'})
        with pytest.raises(ValueError, match='运行尚未结束'):
            records.delete_many([completed['run_id'], active['run_id']])
        assert records.get(completed['run_id'])['status'] == 'passed'
        assert records.get(active['run_id'])['status'] == 'queued'
    with subtests.test(contract='test_run_report_deletion_removes_records_and_artifacts'), isolated_portal_case() as case_path:
        tmp_path = case_path
        records = RunRecords(tmp_path / 'runs.json')
        coordinator = RunCoordinator(records, tmp_path / 'artifacts')
        first = records.create({'executable': True, 'selected': []}, 'alice')
        second = records.create({'executable': True, 'selected': []}, 'alice')
        records.update(first['run_id'], {'status': 'passed'})
        records.update(second['run_id'], {'status': 'failed'})
        for run_id in (first['run_id'], second['run_id']):
            run_dir = tmp_path / 'artifacts' / run_id
            run_dir.mkdir(parents=True)
            (run_dir / 'evidence.txt').write_text('evidence', encoding='utf-8')
        result = coordinator.delete_reports([first['run_id'], second['run_id']], 'alice')
        assert result == {'schema': 'tfi.test-run-deletion.v1', 'deleted_run_ids': [first['run_id'], second['run_id']], 'deleted_count': 2, 'artifact_warnings': []}
        assert records.recent() == []
        assert not (tmp_path / 'artifacts' / first['run_id']).exists()
        assert not (tmp_path / 'artifacts' / second['run_id']).exists()
    with subtests.test(contract='test_run_records_redact_runner_credentials_before_persistence'), isolated_portal_case() as case_path:
        records_path = case_path / 'runs.json'
        records = RunRecords(records_path)
        created = records.create({'executable': True, 'selected': []}, 'alice')
        records.append_output(
            created['run_id'],
            'request failed: {"cookie":"portal_session=fixture-cookie-secret; theme=dark"}\n',
            group_id='serial',
        )
        records.append_result(created['run_id'], 'serial', {
            'status': 'failed',
            'details': {
                'summary': 'Cookie: portal_session=fixture-header-secret; theme=dark',
                'diagnosis': 'portal_session=fixture-standalone-secret',
            },
        })
        persisted = records_path.read_text(encoding='utf-8') + (
            records.records_root / f"{created['run_id']}.json"
        ).read_text(encoding='utf-8')
        assert 'fixture-cookie-secret' not in persisted
        assert 'fixture-header-secret' not in persisted
        assert 'fixture-standalone-secret' not in persisted
        assert persisted.count('[REDACTED]') >= 3


def test_shared_tau_identity_is_exclusive_without_consuming_test_capacity(tmp_path, monkeypatch):
    """同一 TAU 模块身份互斥使用，不同模块互不阻塞，且不占用测试并发容量"""
    monkeypatch.setenv("PORTAL_TEST_SLOT_ROOT", str(tmp_path))
    monkeypatch.setenv("PORTAL_TEST_MAX_CONCURRENCY", "1")
    entered = threading.Event()
    def another_campaign():
        with slots.exclusive("tau-module:resident.chat", stop=lambda: False, poll_seconds=0.01) as acquired:
            assert acquired
            entered.set()
    with slots.exclusive("tau-module:resident.chat", stop=lambda: False):
        worker = threading.Thread(target=another_campaign)
        worker.start()
        assert not entered.wait(0.05)
        with slots.exclusive("tau-module:paper", stop=lambda: False) as independent:
            assert independent
        with slots.exclusive("tau-module:resident.chat", stop=lambda: True) as cancelled:
            assert not cancelled
        lease = slots.acquire("tau", 1, stop=lambda: True)
        assert lease is not None
        lease.release()
    worker.join(timeout=2)
    assert entered.is_set()


def test_run_history_limit_preserves_active_campaigns(tmp_path):
    """运行历史只保留最近 100 条已结束记录，进行中和排队的运行不被清除"""
    records = RunRecords(tmp_path / "runs.json")
    plan = {"selected": [], "execution": {"groups": []}}
    active = records.create(plan, "alice")["run_id"]
    records.update(active, {"status": "running"})
    completed = []
    for _ in range(101):
        run_id = records.create(plan, "alice")["run_id"]
        records.update(run_id, {"status": "passed"})
        completed.append(run_id)
    current = records.create(plan, "alice")["run_id"]
    assert records.get(active)["status"] == "running"
    assert records.get(current)["status"] == "queued"
    assert records.run_ids() == {active, current, *completed[-100:]}


@pytest.mark.parametrize("first_verdict", ["passed", "failed"])
def test_tau_cleanup_failure_stops_same_module_but_other_modules_continue(tmp_path, first_verdict):
    """资源未清理时不向同一模块继续派发，其他模块正常执行并保留原结果。"""
    records = RunRecords(tmp_path / "runs.json")
    source = "tests/cases/test_test_framework.py"
    selected = [
        {"case_id": case_id, "case_title": "清理隔离", "module": module,
         "runner": {"id": "tau"}, "source_sha256": hashlib.sha256((ROOT / source).read_bytes()).hexdigest(),
         "implementation": {"id": case_id, "source": source}}
        for case_id, module in [("test_123456789001", "workflow.paper"),
                                ("test_123456789002", "workflow.paper"),
                                ("test_123456789003", "resource.service")]
    ]
    groups = [
        {"group_id": "module:"+module, "runner": "tau", "module_id": module,
         "item_indexes": indexes, "continue_on_failure": True}
        for module, indexes in [("workflow.paper", [0, 1]), ("resource.service", [2])]
    ]
    run = records.create({"executable": True, "campaign": "tau", "selected": selected,
                          "execution": {"groups": groups}}, "alice")
    executed = []

    class Adapter:
        def execute(self, *, planned, **kwargs):
            executed.append(planned["case_id"])
            return AdapterResult(status=first_verdict if planned["case_id"] == selected[0]["case_id"] else "passed",
                                 details={"summary": "原始行为证据保留"})

        def cleanup(self, *, run_dir, emit):
            if run_dir.name == selected[0]["case_id"]:
                raise RuntimeError("review is still running")
            return {"complete": True}

    TauCampaignExecutor(records, tmp_path / "artifacts", Adapter()).run(run["run_id"])
    record = records.get(run["run_id"])
    assert set(executed) == {selected[0]["case_id"], selected[2]["case_id"]}
    assert record["status"] == "error"
    results = {row["case_id"]: row for row in record["results"]}
    assert results[selected[0]["case_id"]]["status"] == "error"
    assert results[selected[0]["case_id"]]["details"]["summary"] == "原始行为证据保留"
    assert results[selected[0]["case_id"]]["details"]["cleanup_error"] == "review is still running"
    assert results[selected[2]["case_id"]]["status"] == "passed"
    paper = next(group for group in record["groups"] if group["module_id"] == "workflow.paper")
    assert "清理尚未完成" in paper["error"]
    assert not paper.get("active_invocation")


def test_tau_cleanup_scan_tolerates_concurrent_report_deletion(tmp_path, monkeypatch):
    """清理扫描时旧运行报告被并发删除，不阻塞新的 TAU 运行"""
    records = RunRecords(tmp_path / "runs.json")
    plan = {"selected": [], "execution": {"groups": []}}
    previous = records.create(plan, "alice")["run_id"]
    records.update(previous, {"status": "passed"})
    current = records.create(plan, "alice")["run_id"]
    snapshot = records.run_ids()
    records.delete_many([previous])
    monkeypatch.setattr(records, "run_ids", lambda: snapshot)

    class Adapter:
        def cleanup(self, **kwargs):
            pytest.fail("A deleted report must not block the next campaign")

    executor = TauCampaignExecutor(records, tmp_path / "artifacts", Adapter())
    executor._recover_module_cleanup(current, "module:resident.chat")


@pytest.mark.parametrize("receipt_source", ["result", "file", "exception"])
@pytest.mark.parametrize("finished_scope", ["run", "module"])
def test_tau_retries_only_failed_cleanup_of_finished_runs(tmp_path, receipt_source, finished_scope):
    """TAU 只重试已结束运行中未完成的清理，成功一次后不再重试"""
    records = RunRecords(tmp_path / "runs.json")
    group_id = "module:resident.chat"
    plan = {"selected": [], "execution": {"groups": [{"group_id": group_id}]}}
    previous = records.create(plan, "alice")["run_id"]
    active = records.create(plan, "alice")["run_id"]
    current = records.create(plan, "alice")["run_id"]
    for run_id, status in [(previous, "error"), (active, "running")]:
        records.update(run_id, {"status": "running" if finished_scope == "module" else status})
        records.update_group(run_id, group_id, {"status": status})
        records.append_result(run_id, group_id, {"case_id": "test_123456789012", "status": "error",
            "details": {"framework_cleanup": {"complete": False}} if receipt_source == "result"
            else {"cleanup_error": "cleanup interrupted"}})
        if receipt_source == "file":
            case_dir = tmp_path / "artifacts" / run_id / "module-resident.chat" / "test_123456789012"
            case_dir.mkdir(parents=True)
            (case_dir / "framework-helper-cleanup.json").write_text('{"complete":false}')
    cleaned = []
    class Adapter:
        def cleanup(self, *, run_dir, emit):
            cleaned.append(run_dir)
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "framework-helper-cleanup.json").write_text('{"complete":true}')
    executor = TauCampaignExecutor(records, tmp_path / "artifacts", Adapter())
    executor._recover_module_cleanup(current, group_id)
    executor._recover_module_cleanup(current, group_id)
    assert cleaned == [tmp_path / "artifacts" / previous / "module-resident.chat" / "test_123456789012"]
    assert records.get(previous)["results"][0]["status"] == "error"


@pytest.mark.parametrize("mode", ["resume", "source_changed", "cancelling", "group_error"])
def test_tau_campaign_resumes_the_same_resident_invocation_after_restart(tmp_path: Path, mode: str) -> None:
    """执行器重启后 TAU 运行接续原 Resident 消息，源码变化或正在取消时也不重新发送

    :design: decision_c953eab654cd
    :design-point:
    """
    source = "tests/cases/test_test_framework.py"
    digest = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
    case = {
        "case_id": "test_000000000008", "case_title": "resume TAU",
        "module": "operations.test-workbench",
        "implementation": {"id": "impl-tau-resume", "runner": "tau", "source": source},
        "runner": {"id": "tau"}, "source_sha256": digest,
    }
    plan = {
        "schema": "tfi.test-run-plan.v2", "executable": True,
        "campaign": "tau", "selected": [case],
        "execution": {"mode": "parallel-modules", "groups": [{
            "group_id": "module:operations.test-workbench",
            "module_id": "operations.test-workbench", "runner": "tau",
            "item_indexes": [0], "continue_on_failure": True,
        }]},
    }
    records = RunRecords(tmp_path / "runs.json")
    created = records.create(plan, "alice")
    handle = {
        "case_id": "test_000000000008", "resident_id": "resident-stable",
        "lineage_id": "lineage-stable", "message_id": "message-existing",
    }
    records.update(created["run_id"], {
        "status": "cancelling" if mode == "cancelling" else "running", "started_at": now_iso(),
        "executor": {**created['executor'], "pid": 999_999_999},
    })
    records.update_group(
        created["run_id"], "module:operations.test-workbench",
        {"status": "error" if mode == "group_error" else "running", "current_case_id": "test_000000000008", "active_invocation": handle, "case_started_at_epoch": 1000.0},
    )

    # The executor unit ended with the Campaign unfinished; Web relaunches it.
    launched: list[str] = []
    assert RunCoordinator(records, tmp_path / "artifacts", launch=launched.append).recover() == [created["run_id"]]

    observed = {}

    if mode == "source_changed":
        # A confirmed invocation keeps its original plan even after the shared
        # source changes. The same mismatch must still reject a fresh launch.
        stale_case = {**case, "source_sha256": "0" * 64}
        with pytest.raises(RuntimeError, match="源码已变化"):
            TauCampaignExecutor._verify_source(stale_case)
        stored = records.get(created["run_id"])
        stored["plan"]["selected"][0]["source_sha256"] = stale_case["source_sha256"]
        records.update(created["run_id"], {"plan": stored["plan"]})

    class ResumingAdapter:
        def execute(self, **kwargs):
            assert kwargs["planned"]["_started_at_epoch"] == 1000.0
            observed.update(kwargs["planned"]["_resume_invocation"])
            # A cancelled Campaign still hands the submitted case to the
            # adapter, which withdraws that Resident input.
            assert kwargs["cancelled"]() == (mode == "cancelling")
            return AdapterResult(status="cancelled" if kwargs["cancelled"]() else "passed")

    executor = TauCampaignExecutor(records, tmp_path / "artifacts", ResumingAdapter())
    assert executor.run(created["run_id"]) == ("cancelled" if mode == "cancelling" else "passed")
    record = records.get(created["run_id"])

    assert record["status"] == ("cancelled" if mode == "cancelling" else "passed")
    assert observed["resident_id"] == "resident-stable"
    assert observed["message_id"] == "message-existing"
    assert record["groups"][0]["active_invocation"] == {}
    assert record["report"]["schema"] == "tfi.portal-tau-report.v1"


def test_agent_use_profile_requires_a_private_storage_state_contracts(tmp_path, subtests, isolated_portal_case):
    """TAU 登录状态文件须存在且权限为 0600，Resident 取消或失败分别记为取消和用例结果"""
    with subtests.test(contract='test_agent_use_profile_requires_a_private_storage_state'), isolated_portal_case() as case_path:
        tmp_path = case_path
        storage = tmp_path / 'test-actor.json'
        config = _tau_config(storage)
        assert not hasattr(config, 'timeout_seconds')
        assert '尚未创建' in config.unavailable_reason()
        storage.write_text('{}', encoding='utf-8')
        storage.chmod(420)
        assert '0600' in config.unavailable_reason()
        storage.chmod(384)
        assert config.available
    with subtests.test(contract='test_agent_use_adapter_maps_resident_cancellation_to_cancelled'), isolated_portal_case() as case_path:
        tmp_path = case_path
        storage = tmp_path / 'test-actor.json'
        storage.write_text('{}', encoding='utf-8')
        storage.chmod(384)

        def invoke(**kwargs):
            assert kwargs['cancellation_requested']()
            raise RuntimeError('Resident input ended with cancelled')
        outcome = TauAdapter(_tau_config(storage), invoke, lambda _storage: _verified_dev_target()).execute(run_id='testrun_0000000000000001', planned={'case_id': 'test_000000000009', 'case_title': '中止真实使用', 'description': '验证中止语义。', 'module': 'operations.test-workbench', 'implementation': {'task_prompt': '等待中止。'}}, run_dir=tmp_path / 'evidence', actor='alice', emit=lambda _chunk: None, cancelled=lambda: True)
        assert outcome.status == 'cancelled'
    with subtests.test(contract='test_tau_adapter_records_resident_failure_as_a_case_result'), isolated_portal_case() as case_path:
        tmp_path = case_path
        storage = tmp_path / 'test-actor.json'
        storage.write_text('{}', encoding='utf-8')
        storage.chmod(384)

        def invoke(**kwargs):
            kwargs['on_submitted']({'resident_id': 'resident-limited', 'lineage_id': 'lineage-limited', 'message_id': 'message-limited'})
            raise RuntimeError('provider usage limit reached')
        output = []
        cancelled_inputs = []
        outcome = TauAdapter(
            _tau_config(storage), invoke,
            lambda _storage: _verified_dev_target(),
            cancel_invocation=lambda resident_id, actor, message_id: (
                cancelled_inputs.append((resident_id, actor, message_id))
                or {"state": "cancelled"}
            ),
        ).execute(run_id='testrun_0000000000000002', planned={'case_id': 'test_00000000000c', 'case_title': '记录 Provider 失败', 'description': '验证失败进入结构化报告。', 'module': 'operations.test-workbench', 'implementation': {'task_prompt': '执行真实页面检查。'}}, run_dir=tmp_path / 'evidence', actor='alice', emit=output.append, cancelled=lambda: False)
        assert outcome.status == 'error'
        assert outcome.details['issues'] == ['provider usage limit reached']
        assert outcome.details['conversation']['resident_id'] == 'resident-limited'
        assert outcome.details['resident_cancellation'] == {
            'attempted': True, 'state': 'cancelled',
        }
        assert cancelled_inputs == [
            ('resident-limited', 'test-actor', 'message-limited'),
        ]
        assert 'TAU Resident 执行失败' in ''.join(output)


def _verified_dev_target() -> VerifiedDevTarget:
    return VerifiedDevTarget(
        instance_id="dev",
        origin="http://127.0.0.1:18086",
        expected_build_id="dev-current",
        actual_build_id="dev-current",
        page_fingerprint="component-v2",
        actor="test-actor",
    )


def test_tau_prompt_identity_does_not_change_with_normal_dev_build_restart(tmp_path: Path) -> None:
    """DEV 正常重启换构建后 TAU 提示词保持不变，不含构建 ID"""
    storage = tmp_path / "test-actor.json"
    storage.write_text("{}", encoding="utf-8")
    storage.chmod(0o600)
    planned = {
        "case_id": "test_000000000009",
        "case_title": "验证真实 DEV",
        "description": "在 DEV 重启后接续同一条 TAU 消息。",
        "module": "operations.test-workbench",
        "aspects": ["ui"],
        "implementation": {"task_prompt": "检查页面。"},
    }
    target = _verified_dev_target()
    restarted = VerifiedDevTarget(
        **{
            **target.__dict__,
            "expected_build_id": "dev-restarted",
            "actual_build_id": "dev-restarted",
        }
    )

    before = build_prompt(planned, _tau_config(storage), tmp_path / "evidence", target, "testrun_0000000000000003")
    after = build_prompt(planned, _tau_config(storage), tmp_path / "evidence", restarted, "testrun_0000000000000003")

    assert before == after
    assert "dev-current" not in before
    assert "dev-restarted" not in after
    assert "由 TAU preflight 核对，已写入证据目录的 run-context.json" in before


@pytest.mark.parametrize("component_scoped", [False, True])
def test_agent_use_dev_preflight_verifies_build_page_and_actor(tmp_path: Path, monkeypatch, component_scoped) -> None:
    """DEV 预检核对构建版本、页面组件指纹、实例和登录身份，构建不符单独归类

    :design: decision_c953eab654cd
    :design-point:
    """
    registry = tmp_path / "deployments.json"
    registry.write_text(
        json.dumps({
            "schema": "tfi.portal-deployments.v1",
            "instances": [{
                "enabled": True,
                "id": "dev",
                "kind": "dev",
                "label": "Dev",
                "order": 1,
                "origin": "http://127.0.0.1:18086",
            }],
        }),
        encoding="utf-8",
    )
    storage = tmp_path / "test-actor.json"
    storage.write_text(
        json.dumps({"cookies": [{"name": "db_session", "value": "secret", "domain": "127.0.0.1"}]}),
        encoding="utf-8",
    )
    responses = {
        "/api/health": {"instance": "dev", "build_id": "dev-current"},
        "/development?view=test": '<main id="projectionMain"></main>',
        "/development/native-views?view=test": {"html": '<section data-test-workbench-version="component-v2"></section>'},
        "/api/me": {"username": "test-actor", "portal_instance": "dev", "build_id": "dev-current"},
    }

    def http_get(url: str, headers: dict[str, str]) -> bytes:
        path = "/" + url.split("/", 3)[-1]
        assert path == "/api/health" or headers.get("Cookie") == "db_session=secret"
        value = responses[path]
        return value.encode() if isinstance(value, str) else json.dumps(value).encode()

    options = {"build_id": lambda _root: "dev-current"}
    if component_scoped:
        monkeypatch.setattr("management_portal.test_framework.dev_target.read_build_id", lambda: "dev-new-lineage")
        monkeypatch.setattr("management_portal.test_framework.dev_target.read_component_build_ids", lambda: {
            "web": "dev-current", "society_service": "dev-new-lineage",
        }, raising=False)
        options = {}
    verifier = DevTargetVerifier(
        registry_path=registry,
        source_root=tmp_path,
        http_get=http_get,
        **options,
    )
    target = verifier.verify(storage)

    assert target.result_details() == {
        "instance": "dev",
        "browser_origin": "http://127.0.0.1:18086",
        "expected_build_id": "dev-current",
        "actual_build_id": "dev-current",
        "page_fingerprint": "component-v2",
        "actor": "test-actor",
    }

    native_views = responses["/development/native-views?view=test"]
    responses["/development/native-views?view=test"] = {"html": "<section></section>"}
    with pytest.raises(DevTargetError, match="组件指纹"):
        verifier.verify(storage)
    responses["/development/native-views?view=test"] = native_views

    responses["/api/health"]["build_id"] = "dev-old"
    with pytest.raises(DevBuildMismatch):
        verifier.verify(storage)
    responses["/api/health"]["build_id"] = "dev-current"
    responses["/api/me"]["build_id"] = "dev-next"
    with pytest.raises(DevBuildMismatch):
        verifier.verify(storage)
    responses["/api/me"]["portal_instance"] = "release"
    with pytest.raises(DevTargetError) as wrong_instance:
        verifier.verify(storage)
    assert not isinstance(wrong_instance.value, DevBuildMismatch)
    responses["/api/me"]["portal_instance"] = "dev"
    responses["/api/me"]["username"] = "another-user"
    with pytest.raises(DevTargetError) as wrong_actor:
        verifier.verify(storage)
    assert not isinstance(wrong_actor.value, DevBuildMismatch)


@pytest.mark.parametrize('standalone_fixture', [False, True])
def test_agent_use_adapter_invokes_resident_and_preserves_reviewable_result(
    tmp_path: Path, monkeypatch, standalone_fixture,
) -> None:
    """TAU 适配器调用 Resident 并保留可复核结果，缺少机器可读结论记为错误，账号不符时不发送"""
    storage = tmp_path / "test-actor.json"
    storage.write_text("{}", encoding="utf-8")
    storage.chmod(0o600)
    config = _tau_config(storage)
    browsers = tmp_path / 'framework-browser-cache'
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(browsers))
    captured = {}
    from management_portal.test_framework import tau_fixture_paths
    scratch_base = tmp_path / 'scratch'
    scratch_base.mkdir()
    monkeypatch.setattr(tau_fixture_paths, '_bases', lambda: [scratch_base])
    checkout = tmp_path / 'checkout'
    (checkout / '.git').mkdir(parents=True)
    run_dir = checkout / "evidence"
    run_dir.mkdir()
    (run_dir / "test.png").write_bytes(b"valid-enough-for-path-validation")

    def invoke(**kwargs):
        import inspect
        from management_portal.resident_handoff import run_resident_turn
        inspect.signature(run_resident_turn).bind(**kwargs)
        captured.update(kwargs)
        if standalone_fixture:
            context = json.loads((run_dir / 'run-context.json').read_text())
            fixture = Path(context['standalone_fixture_dir'])
            assert fixture.is_dir() and not fixture.is_relative_to(checkout)
            assert 'standalone_fixture_dir' in kwargs['prompt']
        kwargs["on_submitted"]({"resident_id": "resident-test", "message_id": "message-test"})
        return {
            "resident_id": "resident-test",
            "lineage_id": "lineage-test",
            "message_id": "message-test",
            "message": '完成。\nPORTAL_TAU_RESULT: {"status":"passed","summary":"真实页面可用","issues":[],"evidence":["test.png"]}',
        }

    output = []
    assignments = TauResidentAssignments(tmp_path / "tau-residents.json")
    assignments.rotate("operations.test-workbench", changed_by="alice", backend_route_id="four")
    assignments.configure(["operations.test-workbench"], backend_route_id="five", service_tier="priority", changed_by="alice")
    outcome = TauAdapter(config, invoke, lambda _storage: _verified_dev_target(), assignments=assignments).execute(
        run_id="testrun_0000000000000004",
        planned={
            "case_id": "test_000000000009",
            "case_title": "真实使用 Test 页面",
            "description": "验证用户能够使用页面。",
            "module": "operations.test-workbench",
            "implementation": {"task_prompt": "打开页面并检查筛选。",
                               **({'standalone_git_fixture': 'Verify uninitialized Git registration'} if standalone_fixture else {})},
        },
        run_dir=run_dir,
        actor="alice",
        emit=output.append,
        cancelled=lambda: False,
    )

    assert outcome.status == "passed", outcome.details
    assert outcome.details["summary"] == "真实页面可用"
    assert outcome.details["conversation"]["resident_id"] == "resident-test"
    assert "PORTAL_TAU_RESULT" in "".join(output)
    assert str(storage) in captured["prompt"]
    assert str(run_dir) in captured["prompt"]
    assert "本次独立运行：testrun_0000000000000004" in captured["prompt"]
    assert "当前结论必须来自本次操作" in captured["prompt"]
    assert "用例总时限" not in captured["prompt"]
    assert "UTC 截止时间" not in captured["prompt"]
    assert "不得以固定墙钟时长判定失败" in "".join(captured["prompt"].split())
    assert "run-context.json" in captured["prompt"]
    # The framework records the run context and initial build before the case is sent.
    context = json.loads((run_dir / "run-context.json").read_text(encoding="utf-8"))
    assert context["run_id"] == "testrun_0000000000000004"
    assert context["case_id"] == "test_000000000009"
    assert context["browser_runtime"]["browsers_path"] == str(browsers)
    assert Path(context["browser_runtime"]["python_executable"]).is_absolute()
    assert context["evidence_dir"] == str(run_dir.resolve())
    assert context["build_id"]
    assert "此前 Run 的文件不能作为本轮证据" in captured["prompt"]
    assert "node --check" in captured["prompt"]
    assert "动态值写入同目录 JSON 配置" in captured["prompt"]
    assert "PW_TEST_SCREENSHOT_NO_FONTS_READY=1" in captured["prompt"]
    assert "document.fonts.ready" in captured["prompt"]
    assert "framework-helper-cleanup.json" in captured["prompt"]
    assert "TMPDIR" in captured["prompt"]
    assert "NUL 字节解析 argv" in captured["prompt"]
    assert "文件实际行数" in captured["prompt"]
    assert "waitForURL" in captured["prompt"]
    assert "waitForLoadState('domcontentloaded')" in captured["prompt"]
    assert "同一个稳定面板根节点" in captured["prompt"]
    assert "不得用整个 document" in captured["prompt"]
    assert "已经激活的标签直接读取" in captured["prompt"]
    assert "导航时刻标记" in captured["prompt"]
    assert "执行上下文因导航销毁属于辅助脚本同步错误" in captured["prompt"]
    assert "检查匹配数量" in captured["prompt"]
    assert "net::ERR_ABORTED" in captured["prompt"]
    assert "预期取消" in captured["prompt"]
    assert "superseded media request" in captured["prompt"]
    assert captured["metadata"]["test_case_id"] == "test_000000000009"
    assert captured["resident_scope"].startswith("portal-tau:test-actor:operations.test-workbench:")
    assert captured["lineage_scope"] == "portal-tau:test-actor:operations.test-workbench"
    assert captured["actor"] == "test-actor"
    assert captured["backend_route_id"] == "five"
    assert captured["runtime_options"]["service_tier"] == "priority"
    assert "dev-current" not in captured["prompt"]
    assert "由 TAU preflight 核对，已写入证据目录的 run-context.json" in captured["prompt"]
    assert outcome.details["target_preflight"]["actor"] == "test-actor"
    assert outcome.details["target_postflight"] == {
        **outcome.details["target_preflight"],
        "build_changed": False,
    }
    tau_fixture_paths.cleanup(run_dir)

    invalid = TauAdapter(
        config,
        lambda **_kwargs: {
            "resident_id": "resident-test",
            "lineage_id": "lineage-test",
            "message_id": "message-without-verdict",
            "message": "已完成操作，但遗漏机器可读结论。",
        },
        lambda _storage: _verified_dev_target(),
        assignments=TauResidentAssignments(tmp_path / "invalid-tau-residents.json"),
    ).execute(
        run_id="testrun_0000000000000005",
        planned={
            "case_id": "test_000000000009",
            "case_title": "真实使用 Test 页面",
            "description": "验证用户能够使用页面。",
            "module": "operations.test-workbench",
            "implementation": {"task_prompt": "打开页面并检查筛选。"},
        },
        run_dir=tmp_path / 'invalid-evidence',
        actor="alice",
        emit=output.append,
        cancelled=lambda: False,
    )

    assert invalid.status == "error"
    assert invalid.details["issues"] == ["TAU Resident 未返回 PORTAL_TAU_RESULT"]
    assert invalid.details["failure_class"] == "test"

    # A case whose module runs on another account than the Campaign asks for
    # is not sent: it did not run, so it is unsupported, not failed.
    unsent = TauAdapter(
        config, lambda **_kwargs: pytest.fail("the case must not be sent"),
        lambda _storage: _verified_dev_target(), assignments=assignments,
    ).execute(
        run_id="testrun_0000000000000006",
        planned={
            "case_id": "test_000000000009", "case_title": "真实使用 Test 页面",
            "description": "验证用户能够使用页面。", "module": "operations.test-workbench",
            "implementation": {"task_prompt": "打开页面并检查筛选。"}, "resident_account_id": "four",
        },
        run_dir=run_dir, actor="alice", emit=output.append, cancelled=lambda: False,
    )
    assert unsent.status == "unsupported"
    assert unsent.details["required_account"] == "four"


def test_tau_accepts_mixed_evidence_when_a_ui_screenshot_is_present(tmp_path: Path) -> None:
    """有 UI 截图时 TAU 也接受同时提交的其他类型证据"""
    storage = tmp_path / "test-actor.json"
    storage.write_text("{}", encoding="utf-8")
    storage.chmod(0o600)
    run_dir = tmp_path / "evidence"
    run_dir.mkdir()
    (run_dir / "screen.png").write_bytes(b"image")
    (run_dir / "notes.json").write_text('{"finding":"ok"}', encoding="utf-8")

    def invoke(**kwargs):
        kwargs["on_submitted"]({"resident_id": "resident-ui", "message_id": "message-ui"})
        return {
            "resident_id": "resident-ui",
            "message": (
                'PORTAL_TAU_RESULT: {"status":"passed","summary":"UI 可用","issues":[],'
                '"evidence":["screen.png","notes.json"],'
                '"visual_review":{"status":"passed","summary":"布局正常"}}'
            ),
        }

    outcome = TauAdapter(
        _tau_config(storage), invoke, lambda _storage: _verified_dev_target(),
    ).execute(
        run_id="testrun_0000000000000006",
        planned={
            "case_id": "test_00000000000a", "case_title": "UI", "description": "检查 UI。",
            "module": "workbench.panels", "aspects": ["ui"],
            "implementation": {"task_prompt": "检查页面。"},
        },
        run_dir=run_dir,
        actor="alice",
        emit=lambda _chunk: None,
        cancelled=lambda: False,
    )

    assert outcome.status == "passed"
    assert outcome.details["evidence"] == ["screen.png", "notes.json"]


@pytest.mark.parametrize("changed,expected_status", [
    ({"actual_build_id": "dev-next", "expected_build_id": "dev-next"}, "passed"),
    ({"instance_id": "release"}, "failed"),
    ({"origin": "http://127.0.0.1:18085"}, "failed"),
    ({"actor": "another-actor"}, "failed"),
])
def test_tau_fails_when_the_dev_target_changes_during_use(tmp_path: Path, changed, expected_status) -> None:
    """使用期间 DEV 实例、地址或身份改变时 TAU 用例失败，仅构建更新时仍通过

    :design: decision_c953eab654cd
    :design-point:
    """
    storage = tmp_path / "test-actor.json"
    storage.write_text("{}", encoding="utf-8")
    storage.chmod(0o600)
    original = _verified_dev_target()
    targets = [original, VerifiedDevTarget(**{**original.__dict__, **changed})]

    def verify(_storage):
        return targets.pop(0)

    def invoke(**kwargs):
        kwargs["on_submitted"]({"resident_id": "resident-drift", "message_id": "message-drift"})
        return {
            "resident_id": "resident-drift",
            "message": 'PORTAL_TAU_RESULT: {"status":"passed","summary":"完成","issues":[],"evidence":[]}',
        }

    outcome = TauAdapter(_tau_config(storage), invoke, verify).execute(
        run_id="testrun_0000000000000007",
        planned={
            "case_id": "test_00000000000b", "case_title": "DEV 稳定性", "description": "检查 DEV。",
            "module": "operations.deployment", "aspects": ["functional"],
            "implementation": {"task_prompt": "检查页面。"},
        },
        run_dir=tmp_path / "evidence",
        actor="alice",
        emit=lambda _chunk: None,
        cancelled=lambda: False,
    )

    assert outcome.status == expected_status
    if expected_status == "failed":
        assert any("DEV 目标发生变化" in issue for issue in outcome.details["issues"])
    else:
        assert not outcome.details["issues"]
        assert outcome.details["target_postflight"]["actual_build_id"] == "dev-next"
        assert outcome.details["target_postflight"]["build_changed"] is True


@pytest.mark.parametrize("phase", ["preflight", "postflight"])
@pytest.mark.parametrize("recovers", [False, True])
def test_tau_rechecks_build_mismatch_without_resubmitting(tmp_path: Path, monkeypatch, phase, recovers) -> None:
    """DEV 构建不一致时 TAU 等待后复检，恢复则通过，且不重新提交用例"""
    storage = tmp_path / "test-actor.json"
    storage.write_text("{}", encoding="utf-8")
    storage.chmod(0o600)
    clock = [0.0]
    monkeypatch.setattr("management_portal.test_framework.runner_adapters.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("management_portal.test_framework.runner_adapters.time.sleep", lambda _seconds: clock.__setitem__(0, clock[0] + 30))
    calls = []
    observations = []
    original = _verified_dev_target()
    updated = VerifiedDevTarget(**{**original.__dict__, "actual_build_id": "dev-next", "expected_build_id": "dev-next"})

    def verify(_storage):
        if phase == "postflight" and not calls:
            return original
        if not recovers or clock[0] < 30:
            raise DevBuildMismatch("build mismatch", details={
                "expected_build_id": "dev-next", "actual_build_id": original.actual_build_id,
            })
        return updated

    def invoke(**kwargs):
        calls.append(kwargs)
        kwargs["on_submitted"]({"resident_id": "resident-build", "message_id": "message-build"})
        return {"resident_id": "resident-build", "message":
                'PORTAL_TAU_RESULT: {"status":"passed","summary":"完成","issues":[],"evidence":[]}'}

    outcome = TauAdapter(_tau_config(storage), invoke, verify).execute(
        run_id="testrun_0000000000000007",
        planned={"case_id": "test_00000000000b", "case_title": "DEV 构建复检",
                 "description": "构建切换后重新验证。", "module": "operations.deployment",
                 "aspects": ["functional"], "implementation": {"task_prompt": "检查页面。"}},
        run_dir=tmp_path / "evidence", actor="alice", emit=observations.append, cancelled=lambda: False,
    )

    assert outcome.status == ("passed" if recovers else "failed")
    assert len(calls) == (1 if recovers or phase == "postflight" else 0)
    assert clock[0] == (30 if recovers else 60)
    assert any(original.actual_build_id in line and "dev-next" in line for line in observations)
    if recovers:
        assert outcome.details["target_postflight"]["actual_build_id"] == "dev-next"


@pytest.mark.parametrize("resuming", [False, True])
def test_agent_use_preflight_failure_never_invokes_resident(tmp_path: Path, resuming) -> None:
    """DEV 预检失败时不调用 Resident，新用例记为失败，续跑记为待定"""
    storage = tmp_path / "test-actor.json"
    storage.write_text("{}", encoding="utf-8")
    storage.chmod(0o600)
    invoked = False

    def invoke(**_kwargs):
        nonlocal invoked
        invoked = True

    def reject(_storage: Path) -> VerifiedDevTarget:
        raise DevTargetError(
            "Canonical DEV is not running the current source build.",
            details={"expected_build_id": "dev-new", "actual_build_id": "dev-old"},
        )

    outcome = TauAdapter(_tau_config(storage), invoke, reject).execute(
        run_id="testrun_0000000000000008",
        planned={
            "case_id": "test_000000000009",
            "case_title": "验证真实 DEV",
            "description": "只允许当前 DEV。",
            "module": "operations.test-workbench",
            "implementation": {"task_prompt": "检查页面。"},
            **({"_resume_invocation": {"resident_id": "resident-existing", "message_id": "message-existing",
                 "target_preflight": dict(_verified_dev_target().__dict__)}} if resuming else {}),
        },
        run_dir=tmp_path / "evidence",
        actor="alice",
        emit=lambda _chunk: None,
        cancelled=lambda: False,
    )

    assert outcome.status == ("pending" if resuming else "failed")
    assert not invoked
    if resuming:
        assert outcome.details["target_preflight"] == _verified_dev_target().result_details()
        assert outcome.details["target_resume_check"]["actual_build_id"] == "dev-old"
    else:
        assert outcome.details["target_preflight"]["actual_build_id"] == "dev-old"


@pytest.mark.parametrize("resuming", [False, True, "recorded"])
@pytest.mark.parametrize("unavailable_error", [SocietyServiceUnavailable, ResourceServiceUnavailable])
def test_tau_reconnects_to_accepted_input_without_resubmitting(tmp_path: Path, monkeypatch, resuming, unavailable_error) -> None:
    """连接在受理后中断时 TAU 重连到原消息取结果，不重新提交

    :design: decision_c953eab654cd
    :design-point:
    """
    from types import SimpleNamespace
    from management_portal.test_framework import runner_adapters

    storage = tmp_path / "test-actor.json"
    storage.write_text("{}", encoding="utf-8")
    storage.chmod(0o600)
    handle = {"resident_id": "resident-existing", "lineage_id": "lineage-existing", "message_id": "message-existing"}
    submissions, resumed, delays, persisted = [], [], [], []
    original = _verified_dev_target()
    current = VerifiedDevTarget(**{**original.__dict__, "expected_build_id": "dev-next", "actual_build_id": "dev-next"})

    def invoke(**kwargs):
        submissions.append(True)
        kwargs["on_submitted"](handle)
        raise unavailable_error("connection reset after acceptance")

    def resume(**kwargs):
        resumed.append({key: kwargs[key] for key in handle})
        if len(resumed) == 1:
            raise unavailable_error("service restarting")
        return {**handle, "message": 'PORTAL_TAU_RESULT: {"status":"passed","summary":"same input completed","issues":[],"evidence":[]}'}

    planned = {
        "case_id": "test_00000000000c", "case_title": "reconnect", "description": "Resume an accepted input.",
        "module": "operations.test-workbench", "implementation": {"task_prompt": "Test the page."},
    }
    if resuming:
        planned["_resume_invocation"] = dict(handle)
        if resuming == "recorded":
            planned["_resume_invocation"]["target_preflight"] = dict(original.__dict__)
    # Observe adapter retries without replacing sleep for unrelated threads.
    monkeypatch.setattr(
        runner_adapters, "time",
        SimpleNamespace(**{**vars(time), "sleep": delays.append}),
    )
    output = []
    outcome = TauAdapter(
        _tau_config(storage), invoke, lambda _storage: current, resume=resume,
    ).execute(
        run_id="testrun_0000000000000009", planned=planned, run_dir=tmp_path / "evidence",
        actor="alice", emit=output.append, cancelled=lambda: False, on_submitted=persisted.append,
    )
    assert outcome.status == "passed"
    assert len(submissions) == (0 if resuming else 1)
    assert resumed == [handle, handle]
    assert delays == [1.0] * (1 if resuming else 2)
    assert "".join(output).count("暂时不可用") == 1
    assert "已取得原消息" in "".join(output)
    if resuming == "recorded":
        assert outcome.details["target_preflight"] == original.result_details()
        assert outcome.details["target_resume_check"] == current.result_details()
        assert outcome.details["target_postflight"]["build_changed"] is True
    elif resuming:
        assert outcome.details["target_preflight"]["status"] == "not_recorded"
        assert outcome.details["target_postflight"]["build_changed"] is None
    else:
        assert persisted == [{**handle, "target_preflight": dict(current.__dict__)}]
        assert outcome.details["target_postflight"]["build_changed"] is False


def test_tau_does_not_retry_an_unconfirmed_submission(tmp_path, subtests, isolated_portal_case):
    """服务不可用导致提交结果未确认时 TAU 不重试，结果记为错误

    :design: decision_c953eab654cd
    :design-point:
    """
    for unavailable_error in [SocietyServiceUnavailable, ResourceServiceUnavailable]:
        with subtests.test(unavailable_error=unavailable_error), isolated_portal_case() as case_path:
            tmp_path = case_path
            storage = tmp_path / "test-actor.json"
            storage.write_text("{}", encoding="utf-8")
            storage.chmod(0o600)
            calls = []

            def invoke(**kwargs):
                calls.append(True)
                raise unavailable_error("submission outcome unknown")

            def resume(**kwargs):
                raise AssertionError("there is no confirmed input to resume")

            outcome = TauAdapter(
                _tau_config(storage), invoke, lambda _storage: _verified_dev_target(), resume=resume,
            ).execute(
                run_id="testrun_000000000000000a", planned={
                    "case_id": "test_00000000000c", "case_title": "unconfirmed", "description": "Do not replay writes.",
                    "module": "operations.test-workbench", "implementation": {"task_prompt": "Test the page."},
                }, run_dir=tmp_path / "evidence", actor="alice", emit=lambda _chunk: None, cancelled=lambda: False,
            )
            assert outcome.status == "error"
            assert calls == [True]


def test_tau_cancels_exact_prepared_input_when_workflow_submission_raises(
    tmp_path, isolated_portal_case,
) -> None:
    """工作流提交报错且只有请求 ID 时 TAU 保持待定，不取消输入也不允许清理"""
    with isolated_portal_case() as case_path:
        storage = case_path / "test-actor.json"
        storage.write_text("{}", encoding="utf-8")
        storage.chmod(0o600)
        prepared = {
            "resident_id": "resident-prepared",
            "lineage_id": "lineage-prepared",
            "request_id": "msg-prepared",
            "submission_state": "pending",
        }
        cancelled_inputs = []

        def invoke(**kwargs):
            kwargs["on_assigned"](prepared)
            raise SocietyServiceUnavailable("workflow committed before response failed")

        outcome = TauAdapter(
            _tau_config(storage),
            invoke,
            lambda _storage: _verified_dev_target(),
            resolve_submission=lambda **_: prepared,
            cancel_invocation=lambda resident_id, actor, message_id: (
                cancelled_inputs.append((resident_id, actor, message_id))
                or {"state": "cancelled"}
            ),
        ).execute(
            run_id="testrun_000000000000000d",
            planned={
                "case_id": "test_00000000000d",
                "case_title": "cleanup ambiguous workflow response",
                "description": "Preserve the environment until the actual input can be established.",
                "module": "operations.test-workbench",
                "implementation": {"task_prompt": "Test the page."},
            },
            run_dir=tmp_path / "evidence",
            actor="alice",
            emit=lambda _chunk: None,
            cancelled=lambda: False,
        )

        assert outcome.status == "pending"
        assert outcome.details["conversation"]["request_id"] == "msg-prepared"
        assert "resident_cancellation" not in outcome.details
        assert not cancelled_inputs
        assert not TauAdapter.cleanup_ready(run_dir=tmp_path / "evidence")


def test_tau_reconnection_obeys_cancellation(tmp_path, monkeypatch, subtests, isolated_portal_case):
    """等待服务恢复重连期间运行被取消时，TAU 停止重连并记为取消

    :design: decision_c953eab654cd
    :design-point:
    """
    for unavailable_error in [SocietyServiceUnavailable, ResourceServiceUnavailable]:
        with subtests.test(unavailable_error=unavailable_error), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                tmp_path = case_path
                from management_portal.test_framework import runner_adapters

                storage = tmp_path / "test-actor.json"
                storage.write_text("{}", encoding="utf-8")
                storage.chmod(0o600)
                calls, delays = [], []

                def resume(**kwargs):
                    calls.append(kwargs["message_id"])
                    raise unavailable_error("service restarting")

                monkeypatch.setattr(runner_adapters.time, "sleep", delays.append)
                outcome = TauAdapter(
                    _tau_config(storage), lambda **_kwargs: pytest.fail("must not resubmit"),
                    lambda _storage: _verified_dev_target(), resume=resume,
                    cancel_invocation=lambda *args: {"state": "cancelled"},
                ).execute(
                    run_id="testrun_000000000000000b", planned={
                        "case_id": "test_00000000000c", "case_title": "cancel reconnect", "description": "Bound reconnect waiting.",
                        "module": "operations.test-workbench", "implementation": {"task_prompt": "Test the page."},
                        "_resume_invocation": {"resident_id": "resident-existing", "lineage_id": "lineage-existing", "message_id": "message-existing"},
                    }, run_dir=tmp_path / "evidence", actor="alice", emit=lambda _chunk: None,
                    cancelled=lambda: len(calls) >= 2,
                )
                assert outcome.status == "cancelled"
                assert calls == ["message-existing", "message-existing"]
                assert delays == [1.0]


def test_test_page_is_composed_from_registered_workbench_panels_contracts(subtests, isolated_portal_case):
    """Test 页面由 PortalWorkbench 和已注册的六个面板组装，嵌在 WALL 页面中"""
    with subtests.test(contract='test_test_page_is_composed_from_registered_workbench_panels'), isolated_portal_case():
        page_root = ROOT / 'management_portal' / 'test_framework' / 'page'
        html = (page_root / 'test_native_view.html').read_text(encoding='utf-8')
        wall = (ROOT / 'management_portal' / 'wall.html').read_text(encoding='utf-8')
        entry = (page_root / 'test_workbench.js').read_text(encoding='utf-8')
        core = (page_root / 'test_workbench_core.js').read_text(encoding='utf-8')
        assert '/static/portal_workbench.js' in html
        assert 'class="portalActivityRail" id="testActivityRail"' in html
        assert 'testActivityRail' in entry
        assert 'PortalWorkbench.createActivityRail' in entry
        assert html.index('/static/portal_preview_runtime.js') < html.index('/static/portal_pdf_preview.js')
        assert html.index('/static/portal_pdf_preview.js') < html.index('/static/portal_preview_renderers.js')
        assert '/static/wall.js' in wall and '/static/wall.js' not in html
        assert '<template id="wallNativeView"' in wall
        assert '<template id="testNativeView">' in html and 'testNativeView' not in wall
        assert 'id="wallTestView"' in html
        assert 'registerView("test"' in entry
        assert not (page_root / 'test.html').exists()
        assert 'window.PortalWorkbench.create({' in entry
        assert 'panelDefinitions' in core
        panel_files = {'test-catalog': 'test_catalog_panel.js', 'test-case': 'test_case_panel.js', 'run-console': 'test_run_console_panel.js', 'tau': 'test_tau_panel.js', 'gate-composer': 'test_gate_composer_panel.js', 'test-review': 'test_review_panel.js'}
        for panel_type, filename in panel_files.items():
            source = (page_root / filename).read_text(encoding='utf-8')
            assert f'type: "{panel_type}"' in source
            assert f'/static/{filename}' in html
        case_panel = (page_root / 'test_case_panel.js').read_text(encoding='utf-8')
        assert '/test/api/source?case_id=' in case_panel
        assert 'data-implementation-id=' in case_panel
        assert 'document: { kind: "file"' not in case_panel
        assert '/static/test_live_adapter.js' in html
        assert 'window.PortalTestLabDemoAdapter = adapter' in (page_root / 'test_live_adapter.js').read_text(encoding='utf-8')
        assert not (page_root / 'test_lab.js').exists()
        assert not (page_root / 'test_lab.css').exists()


def test_implementation_source_preview_returns_catalog_owned_content(tmp_path):
    """实现源码预览返回登记的文件和当前行号，TAU 用例只读，未知实现报错"""
    service = FrameworkService(state_root=tmp_path)

    document = service.implementation_source(
        "test_8a2906c7e8bc",
        "impl-agent-use-test-page-discovery",
        actor="test-actor",
    )

    assert document["path"] == "tests/cases/tau_cases.json"
    assert document["line"] == 1
    assert '"id": "test_8a2906c7e8bc"' in document["content"]
    assert document["capabilities"]["edit"] is False

    with pytest.raises(KeyError, match="未知测试实现"):
        service.implementation_source(
            "test_8a2906c7e8bc",
            "impl-does-not-exist",
            actor="test-actor",
        )

    # A code test's line is read from the source, so it follows edits above it.
    entry = "tests/cases/test_test_framework.py::test_implementation_source_preview_returns_catalog_owned_content"
    case = next(case for case in service.catalog.all() if any(item.entrypoint == entry for item in case.implementations))
    implementation = next(item for item in case.implementations if item.entrypoint == entry)
    own = service.implementation_source(case.id, implementation.id, actor="test-actor")
    assert own["content"].splitlines()[own["line"] - 1].startswith("def test_implementation_source_preview_returns")


def test_contract_subchecks_reset_stores_on_the_canonical_database(isolated_portal_case):
    """每个契约子检查重建存储对象，但仍连接同一个规范数据库"""
    from management_portal.execution.agent_data_store import get_agent_data_store

    namespace = os.environ["PORTAL_DATABASE_NAMESPACE"]
    with isolated_portal_case() as first_path:
        first_store = get_agent_data_store()
        first_identity = first_store.database_identity
    with isolated_portal_case() as second_path:
        assert second_path != first_path
        second_store = get_agent_data_store()
        assert second_store is not first_store
        assert second_store.database_identity == first_identity
    assert os.environ["PORTAL_DATABASE_NAMESPACE"] == namespace


def test_run_cleanup_failure_changes_passed_outcome_to_error(tmp_path):
    """TAU 用例通过但清理失败时，运行结果改为错误"""
    source = 'tests/cases/test_test_framework.py'
    plan = {'schema': 'tfi.test-run-plan.v1', 'plan_id': 'cleanup-test', 'executable': True,
            'campaign': 'tau',
            'selected': [{'case_id': 'test_000000000003', 'case_title': 'cleanup failure', 'module': 'module-a',
                          'implementation': {'id': 'cleanup-test', 'runner': 'tau',
                                             'entrypoint': 'tau:cleanup-failure',
                                             'source': source, 'line': 1, 'profiles': []},
                          'runner': {'id': 'tau'}, 'profile': '',
                          'source_sha256': hashlib.sha256((ROOT / source).read_bytes()).hexdigest()}],
            'execution': {'mode': 'parallel-modules', 'max_parallel_modules': 1, 'groups': [
                {'group_id': 'module:module-a', 'module_id': 'module-a', 'runner': 'tau',
                 'item_indexes': [0], 'continue_on_failure': True}]}}
    records = RunRecords(tmp_path / 'runs.json')
    class Adapter:
        def execute(self, **kwargs):
            return AdapterResult(status='passed', details={'summary': 'UI passed'})
        def cleanup(self, **kwargs):
            raise RuntimeError('sample still registered')
    created = records.create(plan, 'alice')
    TauCampaignExecutor(records, tmp_path / 'artifacts', Adapter()).run(created['run_id'])
    record = records.get(created['run_id'])
    assert record['status'] == 'error'
    assert record['results'][0]['status'] == 'error'
    assert record['results'][0]['details']['cleanup_error'] == 'sample still registered'
