"""Projection host and development-workspace contracts kept as node:test files under tests/cases/fixtures.

:design: decision_f67ae5e70793
"""
from pathlib import Path
import subprocess


def _node_test(name):
    script = Path(__file__).with_name("fixtures") / name
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_projection_context_themes_and_region_chat_use_their_own_facade():
    """投影上下文转发只读的实时主题候选，分区 Chat 解析自己的门面并附带保存的分区上下文

    主题候选随宿主变化而更新且不改变外观，没有主题服务时为空；分区 Chat 使用该分区绑定的门面 Resident，提交时附带已保存的分区状态，门面绑定变化时拒绝提交；
    分区面板放进对话栈，单独打开分区或排除分区时只挂载对应的编辑器。

    :module: workbench.panels
    """
    _node_test("packaging_host.test.cjs")


def test_packaging_is_a_persistent_development_sidebar_page():
    """打包发布是开发工作台侧栏里的独立常驻页面，与开发标签页分开挂载

    侧栏列出 packaging 视图并以 region=packaging 挂载，开发主视图排除该分区；切换视图时只挂载一次打包页，地址记录 view=packaging，离开时隐藏而不销毁。

    :module: workbench.panels
    """
    _node_test("packaging_sidebar.test.cjs")


def test_release_bridge_is_limited_to_the_published_packaging_surface():
    """发布桥只允许打包分区的发布操作，并携带可信的面板身份

    发布请求带上实际工作台 ID 与 packaging 面板，未列出的操作和其他面板或预览中的调用被拒绝；发布入口按已发布修订读取 detail，资源与库限定在该修订，越界读取被拒绝。

    :module: workbench.panels
    """
    _node_test("projection_release.test.cjs")


def test_host_guards_unsaved_sandbox_state_until_a_write_succeeds():
    """投影宿主在沙箱状态写入未完成或失败时保持离开保护，直到重试成功

    空写入不触发保护；有内容的写入进行中和失败后都保持保护，并把失败原因回给沙箱；之后的写入成功才解除保护。

    :module: workbench.panels
    """
    _node_test("projection_state_save.test.cjs")
