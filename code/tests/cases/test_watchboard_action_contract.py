"""Original Watchboard removal and native workbench tab identity.

:module: resident.conversation
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_watchboard_original_removal_and_panel_binding():
    """Keep the original receipt and bind Chat DOM to its actual native tab.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_watchboard_action_contract.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_tau_watchboard_install_receipt():
    """Original installation IDs must reconcile with the original create operations.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_watchboard_install_receipt.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_tau_finite_instruction_evidence_keeps_the_original_response():
    """TAU 有限指令取证只认动作前登记的原请求，保留原响应、超时与清理

    隔离 Chromium 中核对：拒绝旧请求、其他目标与 Watch 流；有限 JSON 成功与 HTTP 错误按原样保留，非 JSON 流被排除；未完成的响应体按真实期限超时，迟到的响应体不能覆盖原超时；录制出错时仍关闭原上下文，只记安全元数据且不重放请求。

    :module: operations.test-framework
    :design: decision_c953eab654cd
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_finite_instruction_evidence.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
