"""Check original session readiness against visible Chromium DOM."""
from pathlib import Path
import shutil
import subprocess
import pytest

def test_session_original_dom_evidence():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for browser evidence checks')
    subprocess.run([node, str(Path(__file__).with_name('javascript') / 'tau_session_dom_evidence.cjs')], check=True, timeout=90)


def test_session_prerequisites_read_native_identity_search_and_node_captures():
    """TAU 会话前置检查从原生页面读取 Lineage 身份、消息卡片、搜索绑定与节点截图

    在 Chromium 夹具中核对原生 Lineage 资源头与创建入口、消息卡片的物理身份、Resident 隔离、隐藏与重复元素的拒绝、BODY 搜索绑定及异步状态，并拒绝过小的节点截图和有歧义的弹层；不调用生产服务。

    :module: operations.test-framework
    :design: decision_c953eab654cd
    """
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'tau_session_prerequisites.cjs')],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
