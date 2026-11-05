"""核心页面清单与可选 Workbench 安装边界。

:module: platform.core
:design: decision_15aab18c0ab3
"""
import pytest

from management_portal import frontend_manifest


@pytest.fixture(autouse=True)
def fresh_manifest():
    for function in (frontend_manifest.builtins, frontend_manifest.manifest, frontend_manifest._script):
        function.cache_clear()
    yield
    for function in (frontend_manifest.builtins, frontend_manifest.manifest, frontend_manifest._script):
        function.cache_clear()


def test_installed_store_reuses_core_page_definitions():
    """已安装 Workbench 与核心页面使用同一份定义及功能贡献聚合。"""
    from management_portal.workbench_projections import store

    assert store.CORE_BUILTINS is frontend_manifest.CORE_BUILTINS
    assert store.builtins is frontend_manifest.builtins
    assert store.builtins() is frontend_manifest.builtins()


def test_manifest_propagates_a_broken_installed_contribution(monkeypatch):
    """已安装功能的依赖损坏必须显式失败，不能冒充剥离安装。"""
    original = frontend_manifest.portal_features.contributions
    failure = ModuleNotFoundError("installed feature dependency is missing", name="broken_dependency")

    def contributions(hook):
        if hook == "workbench.builtin":
            raise failure
        return original(hook)

    monkeypatch.setattr(frontend_manifest.portal_features, "contributions", contributions)
    with pytest.raises(ModuleNotFoundError) as error:
        frontend_manifest.manifest()
    assert error.value is failure
