"""The Agent Harness panel of the Resident Menu and its Prompt injection page.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_harness_panel_switches_saves_and_prompt_injection():
    """Agent Harness 面板按后端能力与模式显示开关，每次修改保存为 Resident 设置，Prompt 注入页可预览

    保存的选择覆盖服务端默认值，只有默认值为 true 的功能默认开启；没有默认值时只显示读取中。智能选择模式下功能开关由系统决定而禁用，失败后续跑仍可单独切换；手动模式下后端不支持的功能禁用并说明原因，阈值按 KiB 显示，保存的运行时机被选中。读取失败时说明原因；修改开关、运行时机、模式或 sub agent 时发出一次 UpdateResidentSettings，保存期间锁定面板并忽略其他修改，完成后清除缓存、通知调用方并强制重读；保存失败时提示。Prompt 注入页读取设置后预览第一轮，自定义预设只在当前为自定义时出现，切换轮次后丢弃旧轮次的预览，修改预设或类别带幂等键保存并重新预览；读取失败时回到 Harness 列表。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_harness_panel.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
