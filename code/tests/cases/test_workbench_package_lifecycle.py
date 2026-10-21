"""Independent DOM contract for stable ID test_ebd70025ddb6.

:module: workbench.panels
"""
from pathlib import Path
import subprocess


def test_registered_package_lifecycle():
    """注册面板包的创建、事件、状态与销毁生命周期

    完整生产注册表、Context及渲染验证面板生命周期顺序、事件、状态恢复/持久化、同步show重入、异步关闭否决和DOM根移除；真实几何、iframe及可见呈现仍由浏览器覆盖。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    source = Path(__file__).with_name("javascript") / "workbench_package_lifecycle.cjs"
    result = subprocess.run(
        ["node", str(source), "registered-package"],
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
