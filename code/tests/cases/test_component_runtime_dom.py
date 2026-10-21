"""Independent Event and Effect real DOM contracts; browser reload retained.

:module: lineage.hatg
:aspects: ui, behavior
:design: decision_4adcc1ff2f2c
"""
from pathlib import Path
import subprocess


def _run(kind):
    source = Path(__file__).with_name("javascript") / "component_runtime_dom.cjs"
    result = subprocess.run(
        ["node", str(source), kind], capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr


def test_event_runtime_projection_disabled_controls_and_fresh_document():
    """Event/Effect 运行开关持久状态、草稿保留与失败重试

    Event/Effect 运行开关的实际控制器与 DOM 契约验证持久状态、草稿、禁用投影和失败重试；Event 浏览器保留真实点击、刷新与画布呈现，Effect 由独立状态及 DOM 入口承接。
    """
    _run("event_source")


def test_effect_runtime_projection_disabled_controls_and_fresh_document():
    """Event/Effect 运行开关持久状态、草稿保留与失败重试

    Event/Effect 运行开关的实际控制器与 DOM 契约验证持久状态、草稿、禁用投影和失败重试；Event 浏览器保留真实点击、刷新与画布呈现，Effect 由独立状态及 DOM 入口承接。
    """
    _run("effect")
