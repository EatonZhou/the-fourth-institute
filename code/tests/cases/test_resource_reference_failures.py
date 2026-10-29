"""Resource references that fail through the complete production chip module."""
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def _run_case(name):
    source = Path(__file__).with_name("javascript") / "resource_reference_failures.cjs"
    result = subprocess.run(
        ["node", str(source), name], input=portal_route_script().decode(),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_deleted_resource_is_not_requested_again_on_every_redraw():
    """已删除资源的引用在缓存期内不再重复请求

    对话重绘时同一个已删除 Resident 的标签每次都会重新解析；404 与 403 这类不会自行恢复的失败和成功结果一样缓存一段时间，明确要求重新读取时仍请求服务端。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _run_case("deleted-is-remembered")


def test_a_retryable_reference_failure_is_requested_again():
    """可重试的引用失败不缓存

    服务暂时不可用这类可重试失败下一次解析立即重新请求。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _run_case("retryable-asks-again")
