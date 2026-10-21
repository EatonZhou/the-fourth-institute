"""Independent controller/DOM contracts; physical editing and focus stay in UI.

:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def run_contract(name):
    source = Path(__file__).with_name("javascript") / "portal_dom_contracts.cjs"
    result = subprocess.run(["node", str(source), name], capture_output=True, text=True, timeout=30)
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr


def test_root_command_priority():
    """根命令快捷键优先于局部和页面捕获，普通输入不被拦截"""
    run_contract("command-priority")


def test_composition_and_input_command_policy():
    """输入法组合中和输入框内的快捷键不触发普通命令，只执行可用的命令"""
    run_contract("command-policy")


def test_confirmation_with_blocked_forms():
    """页面拦截表单提交时，本地确认框仍可确认且不提交表单

    :module: workbench.components"""
    run_contract("local-confirmation")


def test_dirty_close_uses_shared_confirmation():
    """关闭有未保存修改的对话框时先弹出共享确认框，确认后才关闭

    :module: workbench.components"""
    run_contract("dirty-confirmation")
