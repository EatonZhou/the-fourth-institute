"""Event and Effect controller contracts remain two independent test cases.

:module: lineage.hatg
:aspects: ui, behavior
:design: decision_4adcc1ff2f2c
"""
from pathlib import Path
import subprocess


def _run(kind):
    source = Path(__file__).with_name("javascript") / "hatg_runtime_state.cjs"
    result = subprocess.run(
        ["node", "--test", f"--test-name-pattern=^{kind} runtime switch", str(source)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_event_runtime_switch_preserves_draft_and_retries():
    """Event/Effect 运行开关持久状态、草稿保留与失败重试

    Event/Effect 运行开关的实际控制器与 DOM 契约验证持久状态、草稿、禁用投影和失败重试；Event 浏览器保留真实点击、刷新与画布呈现，Effect 由独立状态及 DOM 入口承接。
    """
    _run("event_source")


def test_effect_runtime_switch_preserves_draft_and_retries():
    """Event/Effect 运行开关持久状态、草稿保留与失败重试

    Event/Effect 运行开关的实际控制器与 DOM 契约验证持久状态、草稿、禁用投影和失败重试；Event 浏览器保留真实点击、刷新与画布呈现，Effect 由独立状态及 DOM 入口承接。
    """
    _run("effect")
