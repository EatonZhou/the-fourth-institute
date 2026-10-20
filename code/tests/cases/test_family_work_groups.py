"""Family navigation contracts through the complete production JavaScript module.

:design: decision_e5f66e2f6a5e
"""
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def _run_navigation_case(name):
    source = Path(__file__).with_name("javascript") / "family_work_groups.cjs"
    result = subprocess.run(
        ["node", str(source), name], input=portal_route_script().decode(),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_same_resource_canonical_lineage_preserves_navigation():
    """同一资源规范导航更新归属并保留对话和消息定位

    经真实资源链接绑定、引用加载与规范URL合并，验证资源身份、当前Lineage、open_chat、message与锚点；真实浏览器点击由保留用例覆盖。

    Proposed unit implementation for stable case test_d6a445772594.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _run_navigation_case("same-resource")


def test_different_resource_canonical_destination_drops_navigation():
    """跨资源规范导航不继承旧对话和消息定位

    经真实资源链接绑定、引用加载与规范URL合并，验证跨资源目标严格等于规范URL，不继承旧查询参数和锚点；真实浏览器点击由保留用例覆盖。

    Proposed unit implementation for stable case test_f080ca3e05c1.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _run_navigation_case("different-resource")
