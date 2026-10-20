"""Image editor state and shared Dialog wiring through the production DOM modules.

:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def _run_contract(name):
    script = Path(__file__).with_name("javascript") / "image_editor_state.cjs"
    result = subprocess.run(
        ["node", str(script), name], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"contract passed: {name}"


def test_failed_image_load_disables_editing_and_clears_canvas():
    """图片加载失败时编辑器显示错误、禁用保存和标注并清空画布，仍可取消关闭

    Stable case test_95e946252220; image decode is an error event input."""
    _run_contract("failed_load")


def test_image_close_defers_until_dirty_confirmation():
    """图片有未保存修改时关闭编辑器先弹出确认，确认后才关闭

    Public close state; native Cancel/backdrop remain test_d99cc8edd18e."""
    _run_contract("close_confirmation")
