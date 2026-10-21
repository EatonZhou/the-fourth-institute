"""Discover the research package's original contracts in the Portal catalog.

Only the existing Node unit contracts and Python contracts are adapted here.
The standalone browser fixture remains separate; see research_projection_contract.md.

:module: workbench.panels
:importance: P1
:design: decision_7a5ae487e2e9
"""
import importlib.util
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "management_portal/workbench_projections/research_apps"


def _session_contract(title):
    result = subprocess.run(
        ["node", "--test", "--test-reporter=tap", "--test-name-pattern=^" + re.escape(title) + "$",
         str(PACKAGE / "tests/session.test.mjs")],
        cwd=PACKAGE, capture_output=True, text=True, timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert re.search(r"^# pass 1$", result.stdout, re.MULTILINE), output
    assert re.search(r"^# fail 0$", result.stdout, re.MULTILINE), output


def _source_module(name):
    spec = importlib.util.spec_from_file_location(
        "_research_projection_" + name, PACKAGE / "tests" / (name + ".py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_permission_revocation_and_service_errors():
    """研究投影读取撤权与服务错误保持

    拒绝resolve后不读资源，读取时撤权不返回缓存，服务错误保留错误。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _session_contract("permission rejection and resolve/read revocation never yield cached data; service errors remain errors")


def test_hidden_destroyed_session_rejects_late_reads():
    """研究投影隐藏销毁后拒绝过期读取

    执行原会话合同，核对隐藏后的迟到读取、确认取消与watch释放。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _session_contract("hidden / destroyed session rejects late reads, cancels confirmations and releases watch")


def test_discussion_context_and_changed_source_cancellation():
    """研究投影讨论字段与来源变更取消

    核对五字段及文本保持、来源变更后取消；反馈出口为fixture，不证明真实发送或持久化。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _session_contract("discussion preserves exact five fields and text; changed source cancels without feedback")


def test_selection_race_and_closed_selection():
    """研究投影选择竞态与关闭后失效

    迟到A不能覆盖B，关闭选择后禁止继续讨论旧对象。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _session_contract("late A selection cannot replace B or revive after close")


def test_index_filters_unreadable_sources_before_counting():
    """研究投影计数前过滤不可读来源

    统计前剔除不可读测量、策略、缓存来源与清单，保留有效结构。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _session_contract("index excludes denied measurements, branches, cached source and inventory before counting")


def test_research_package_isolated_build(tmp_path, monkeypatch):
    """研究投影双入口离线隔离构建

    调用原构建合同，实际Git/npm/bubblewrap离线构建semantic与matrix。必须具备运行用户的 npm 锁定依赖缓存；fixture actor与仓库解析不调用真实ISA。详情见tests/cases/research_projection_contract.md。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    if not os.environ.get("PORTAL_RESEARCH_BUILD_REPORT"):
        monkeypatch.setenv("PORTAL_RESEARCH_BUILD_REPORT", str(tmp_path / "research-build.json"))
    _source_module("test_build").test_research_package_isolated_build(tmp_path, monkeypatch)


def test_migration_patch_uses_workbench_fields_and_preserves_metadata():
    """研究工作台迁移字段与原元数据保持

    迁移更新字段遵循Workbench DEFINITION_FIELDS；保留原ID、期望修订及角色绑定，不发送title/description/owner_ids/project_ids/navigation。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _source_module("test_definition").test_migration_patch_uses_workbench_fields_and_preserves_metadata()
