"""Original WALL response identity and scoped Watchboard DOM evidence.

:module: operations.test-framework
:design: decision_c953eab654cd
"""
from pathlib import Path
import subprocess


def test_wall_original_detail_identity_and_ready():
    """原详情响应先于 ready；未收身份及错误讨论对象不能当作已就绪。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'tau_wall_detail_evidence.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_watchboard_original_dom_scope_and_business_scrollport():
    """原完整 Watchboard 状态和业务滚动容器，不以按钮祖先或输入框替代。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'tau_watchboard_dom_evidence.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_wall_selected_discussion_with_hidden_single_tab_header():
    """原生单标签栏隐藏时读取已选面板；不点击、不拉历史，也不放宽面板身份。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'tau_wall_selected_panel.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
