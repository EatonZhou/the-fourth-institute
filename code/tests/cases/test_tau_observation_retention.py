"""
:importance: P1
:aspects: functional, architecture-conformance
:design: decision_c953eab654cd
"""
from pathlib import Path

import pytest

from management_portal.test_framework.runner_adapters import TauAdapter

@pytest.mark.parametrize('filename', ['tau-browser-observations.jsonl', 'tau-helper-calls.jsonl', 'tau-browser-identity.jsonl', 'harness-tool-rtt.jsonl', 'tau-network-review.json'])
def test_standard_browser_observations_are_cited_are_retained(tmp_path, filename):
    """TAU 标准观测证据在清理前引用并保留

    验证正式结果引用本用例的观测日志，未引用的文件也可用于报告补充观察。
    """
    adapter = object.__new__(TauAdapter)
    root = tmp_path / "case"
    root.mkdir()
    evidence = root / filename
    original = b'{"kind":"request_pending_at_capture_end","request_id":"original","response_status":null}\n'
    evidence.write_bytes(original)
    (root / "uncited.json").write_text("discard")
    details = {"evidence": []}
    assert adapter.retain_observations(run_dir=root, details=details) is None
    assert details["evidence"] == [evidence.name]
    assert evidence.read_bytes() == original
    assert (root / "uncited.json").read_text() == "discard"
    details = {"evidence": []}
    assert adapter.retain_observations(run_dir=root, details=details) is None
    assert details["evidence"] == [evidence.name]
    assert evidence.read_bytes() == original

@pytest.mark.parametrize('filename', ['tau-browser-observations.jsonl', 'tau-helper-calls.jsonl', 'tau-browser-identity.jsonl', 'harness-tool-rtt.jsonl', 'tau-network-review.json'])
def test_standard_browser_observations_do_not_cite_files_outside_the_case(tmp_path, filename):
    """TAU 观测证据禁止引用用例目录外文件

    验证指向其他用例或目录的符号链接不能作为本用例的标准证据，并不修改外部文件。
    """
    adapter = object.__new__(TauAdapter)
    root = tmp_path / "case"
    root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("not this run")
    (root / filename).symlink_to(outside)
    details = {"evidence": []}
    adapter.retain_observations(run_dir=root, details=details)
    assert details["evidence"] == []
    assert outside.read_text() == "not this run"
