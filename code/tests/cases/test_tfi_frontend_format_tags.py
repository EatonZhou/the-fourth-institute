"""Production browser readers and writers accept and emit only canonical tfi.* format tags.

:design: decision_15aab18c0ab3
"""
from pathlib import Path
import subprocess


def _node_test(name):
    script = Path(__file__).with_name("javascript") / "tfi-format-compat" / name
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_browser_readers_reject_non_canonical_format_tags():
    """浏览器端读取器拒绝命名空间、名称、版本或类型不对以及缺失的格式标签

    在隔离的 VM 中运行完整的生产读取器：资源路由、HATG 原语定义与导览数据只接受完整的 tfi.* 标签，对旧前缀、其他版本、带后缀、大小写不同、非字符串和缺失的标签一律拒绝。

    :module: operations.deployment
    """
    _node_test("readers.cjs")


def test_browser_writers_emit_canonical_envelopes_and_keep_historical_records():
    """浏览器端写入器发出规范信封，重试保持业务身份，读取历史程序不改写记录

    指令客户端的 execute 与 watch 写出规范信封，重试时业务身份、参数、游标、幂等键与版本不变；新程序与提供的原语描述符经真实加载器标注规范标签；读取历史程序保留其显式 schema 与记录身份且不修改输入。

    :module: operations.deployment
    """
    _node_test("writers.cjs")
