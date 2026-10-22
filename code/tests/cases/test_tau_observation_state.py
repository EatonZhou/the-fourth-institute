"""TAU evidence processing through the production controller and document probe.

:module: operations.test-workbench
:aspects: ui, behavior
:design: decision_c953eab654cd
"""
from pathlib import Path
import subprocess


def _run_contract(name):
    script = Path(__file__).with_name("javascript") / "tau_observation_state.cjs"
    result = subprocess.run(
        ["node", str(script), name], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"contract passed: {name}"


def test_server_timing_records_only_known_durations_for_the_exact_request():
    """只保存当前响应的 Web 耗时白名单；未知、重复或未暴露值不推断。"""
    _run_contract("server_timing")


def test_pdf_reader_cancellation_links_only_its_actual_fetch():
    """PDF 原始读取切换 range 的取消与真实请求关联；未知原因不猜测。"""
    _run_contract("pdf_cancellation")


def test_unique_stream_receipt_matches_its_document_request():
    """事件流关闭回执仅关联唯一的同文档请求

    验证独立事件流标识关联准确的网络请求；同一标识对应多次请求时保留未知，且不保存凭据或其他查询参数。

    test_ec10e2e7d36e: expanded reused identity false.

    :design-point:
    """
    _run_contract("stream_unique")


def test_reused_stream_identity_preserves_ambiguous_receipt():
    """事件流关闭回执仅关联唯一的同文档请求

    验证独立事件流标识关联准确的网络请求；同一标识对应多次请求时保留未知，且不保存凭据或其他查询参数。

    test_ec10e2e7d36e: expanded reused identity true.

    :design-point:
    """
    _run_contract("stream_reused")


def test_unique_cancellation_receipt_matches_its_http_attempt():
    """指令取消回执通过独立 HTTP 尝试标识关联

    并发同地址指令使用各自请求标识关联关闭回执；重复标识保留歧义，原始失败记录和敏感数据隔离保持不变。

    test_e5ebb964a0f5: expanded reused identity false.

    :design-point:
    """
    _run_contract("cancellation_unique")


def test_reused_cancellation_identity_preserves_ambiguous_receipt():
    """指令取消回执通过独立 HTTP 尝试标识关联

    并发同地址指令使用各自请求标识关联关闭回执；重复标识保留歧义，原始失败记录和敏感数据隔离保持不变。

    test_e5ebb964a0f5: expanded reused identity true.

    :design-point:
    """
    _run_contract("cancellation_reused")


def test_capture_end_preserves_pending_request_and_unavailable_timing():
    """TAU 采集结束保留未完成请求与不可用计时

    验证缺少终态的请求保留为 pending，未暴露的浏览器时序不伪造为成功、取消或后端执行时间。

    test_017da3617e27: pending does not invent a terminal result.

    :module: operations.test-framework
    :importance: P1
    :aspects: ui, behavior, architecture-conformance
    :design-point:
    """
    _run_contract("pending_capture")


def test_actual_stream_ready_precedes_handover_close():
    """实际事件流 ready 与交接关闭回执保持同文档身份关联。

    HTTP open 不算 ready；旧流迟到消息不产生新回执；重连标识有歧义时不猜测请求。
    :design-point:
    """
    _run_contract("stream_ready")


def test_first_batch_close_reason_survives_observation_without_guessing_unknown_reasons():
    """首批读取完成的关闭回执可归因，未知原因仍保留未知。"""
    _run_contract("first_batch_read")
    _run_contract("unknown_close_reason")
