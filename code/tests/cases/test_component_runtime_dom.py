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


def _run_fault_probe(name):
    """One in-memory fault in lineage_graph_cards.js must fail its DOM contract."""
    source = Path(__file__).with_name("javascript") / "component_runtime_dom_negatives.cjs"
    result = subprocess.run(
        ["node", str(source), name], capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"result":"EXPECTED_FAILURE"' in result.stdout, result.stdout + result.stderr
    assert f'"probe":"{name}"' in result.stdout, result.stdout


def test_fault_probe_event_control_ignoring_disabled_fails_the_event_dom_contract():
    """Event 手动触发按钮不再跟随禁用状态时被 Event DOM 契约拒绝

    在内存中让 lineage_graph_cards.js 的 disabled 属性只看 busy，event_source 契约必须报告禁用属性未跟随运行状态；生产文件不改动。

    :module: lineage.hatg
    """
    _run_fault_probe("canvas_disabled")


def test_fault_probe_node_badge_written_as_text_fails_the_effect_dom_contract():
    """节点暂停徽标改为纯文本写入时被 Effect DOM 契约拒绝

    在内存中把 lineage_graph_cards.js 的 innerHTML 写入换成 textContent，effect 契约必须报告真实暂停徽标未渲染。

    :module: lineage.hatg
    """
    _run_fault_probe("node_dom")
